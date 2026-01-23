import re
import asyncio
import logging
import random
from uuid import uuid4
from .utils import STS, start_range_selection, update_range_message, edit_or_reply, parse_buttons
from database import db
from config import temp
from translation import Translation
from .test import CLIENT, update_configs, get_configs
from .unequify import process_unequify_target
from pyrogram import Client, filters, enums
from pyrogram.errors import PeerIdInvalid
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, CallbackQuery, Message

logger = logging.getLogger(__name__)

# Metadata for restoration
SETTING_META = {
    "caption": {
        "title": "CAPTION SETTING",
        "desc": "Send your custom caption.\n\nUse placeholders like <code>{filename}</code>, <code>{size}</code>, and <code>{caption}</code>."
    },
    "button": {
        "title": "BUTTON SETTINGS",
        "desc": "Send your button in the format:\n<code>[Button Text][buttonurl:https://example.com]</code>"
    },
    "db_uri": {
        "title": "DUPLICATE CHECK DB",
        "desc": "Send your MongoDB connection string to be used for duplicate checking."
    },
    "extension": {
        "title": "EXTENSION FILTER",
        "desc": "Send a comma-separated list of file extensions to filter (e.g., <code>mkv,mp4,zip</code>)."
    },
    "keywords": {
        "title": "KEYWORD FILTER",
        "desc": "Send a comma-separated list of keywords to filter.\nUse a <code>-</code> prefix to exclude messages with a keyword (e.g., <code>cat,-dog</code>)."
    }
}

def parse_message_input(message):
    """Parses a forwarded message or a message link."""
    if not message or (not message.text and not message.forward_date):
        return None, None, "Invalid input. A message link or forwarded message is required."

    if message.text:
        open_msg_match = re.search(r"tg://openmessage\?user_id=(\d+)(?:&message_id=(\d+))?", message.text)
        if open_msg_match:
            chat_id = int(open_msg_match.group(1))
            msg_id = int(open_msg_match.group(2)) if open_msg_match.group(2) else None
            return chat_id, msg_id, "id_scan"

        chat_scheme_match = re.search(r"chat://@?([\w\d_]+)", message.text)
        if chat_scheme_match:
             return chat_scheme_match.group(1), None, "id_scan"

    if message.text and not message.forward_date:
        regex = re.compile(r"(https://)?(t\.me/|telegram\.me/|telegram\.dog/)(c/)?(\d+|[a-zA-Z_0-9]+)/(\d+)$")
        match = regex.match(message.text.replace("?single", ""))
        if not match: return None, None, 'Invalid Link.'
        chat_id_str, msg_id = match.group(4), int(match.group(5))
        chat_id = int(("-100" + chat_id_str)) if chat_id_str.isnumeric() else chat_id_str
        return chat_id, msg_id, None
    elif message.forward_from_chat and message.forward_from_chat.type == enums.ChatType.CHANNEL:
        msg_id, chat_id = message.forward_from_message_id, message.forward_from_chat.username or message.forward_from_chat.id
        return chat_id, msg_id, None
    else:
        return None, None, "Invalid input. Please forward from a channel or provide a valid message link."

@Client.on_message(filters.private & filters.command(["fwd", "forward"]))
async def run(bot, message):
    user_id = message.from_user.id
    if temp.lock.get(user_id):
        return await message.reply("A task is already in progress. Please wait for it to complete before starting a new one.")
    
    temp.USER_STATES.pop(user_id, None)
    bots = await db.get_bots(user_id)
    if not bots:
        return await message.reply("Add a bot or userbot to proceed. ( >⁠.⁠< ) --> /settings")

    if len(bots) == 1:
        temp.FORWARD_BOT_ID[user_id] = bots[0]['id']
        await prompt_target_channel(bot, message)
    else:
        buttons = [[InlineKeyboardButton(b.get('name') or f"ID: {b['id']}", callback_data=f"fwd_select_bot_{b['id']}")] for b in bots]
        buttons.append([InlineKeyboardButton("Cancel", callback_data="close_btn")])
        await message.reply("<b>Select a Bot or Userbot</b>", reply_markup=InlineKeyboardMarkup(buttons))

@Client.on_callback_query(filters.regex(r'^fwd_select_bot_'))
async def cb_select_bot(bot, query):
    bot_id = int(query.data.split('_')[-1])
    temp.FORWARD_BOT_ID[query.from_user.id] = bot_id
    await prompt_target_channel(bot, query.message)

async def prompt_target_channel(bot, message):
    user_id = message.chat.id
    channels = await db.get_user_channels(user_id)
    
    chan_btns = [InlineKeyboardButton(c['title'], callback_data=f"fwd_target_{c['chat_id']}") for c in channels]
    chan_btns.append(InlineKeyboardButton("👤 PM Target", callback_data="fwd_target_pm"))
    
    grid = []
    if len(chan_btns) % 2 == 1:
        grid = [chan_btns[i:i+2] for i in range(0, len(chan_btns)-1, 2)]
        grid.append([chan_btns[-1]])
    else:
        grid = [chan_btns[i:i+2] for i in range(0, len(chan_btns), 2)]
        
    grid.append([InlineKeyboardButton("Cancel", callback_data="close_btn")])
    
    await edit_or_reply(message, Translation.TO_MSG, reply_markup=InlineKeyboardMarkup(grid))

@Client.on_callback_query(filters.regex(r'^fwd_target_'))
async def cb_select_target(bot, query):
    user_id = query.from_user.id
    data = query.data
    
    if data == "fwd_target_pm":
        prompt_message = await query.message.edit_text(
            "<b>👤 PM Target</b>\n\n"
            "Send the **Username**, **User ID**, **Profile Link**, or forward a message from the target user.\n\n"
            "/cancel - to abort."
        )
        temp.USER_STATES[user_id] = {"state": "awaiting_pm_target", "prompt_message_id": prompt_message.id}
        return

    to_chat_id = int(data.split('_')[-1])
    
    prompt_message = await query.message.edit_text(Translation.FROM_MSG)
    temp.USER_STATES[user_id] = {"state": "awaiting_source", "to_chat_id": to_chat_id, "prompt_message_id": prompt_message.id}

@Client.on_message(filters.private & filters.incoming, group=-1)
async def stateful_message_handler(bot: Client, message: Message):
    if message.edit_date: return

    user_id = message.from_user.id
    state_info = temp.USER_STATES.get(user_id)
    if not state_info: return

    current_state = state_info.get("state")
    prompt_id = state_info.get("prompt_message_id")

    if message.text and message.text.lower() == "/cancel":
        if prompt_id:
            try: await bot.delete_messages(user_id, prompt_id)
            except Exception: pass
        temp.USER_STATES.pop(user_id, None)
        return await message.reply(Translation.CANCEL)

    if prompt_id:
        try: await bot.delete_messages(user_id, prompt_id)
        except: pass
    try: await message.delete()
    except: pass
    
    if current_state == "awaiting_pm_target":
        target_input = message.text
        resolved_chat_id = None
        target_name = "PM Target"

        try:
            bot_id = temp.FORWARD_BOT_ID.get(user_id)
            if not bot_id: return await bot.send_message(user_id, "Bot selection lost. Please restart.")
            
            _bot_data = await db.get_bot(user_id, bot_id)
            async with CLIENT().client(_bot_data) as client_instance:
                if message.forward_from:
                    chat = message.forward_from
                elif message.forward_from_chat:
                    chat = message.forward_from_chat
                else:
                    try:
                        if target_input.lstrip('-').isdigit():
                            target_input = int(target_input)
                        chat = await client_instance.get_chat(target_input)
                    except Exception:
                        await bot.send_message(user_id, "❌ Invalid user/chat. Please try again.")
                        return 

                resolved_chat_id = chat.id
                target_name = chat.title or chat.first_name

            prompt_message = await bot.send_message(user_id, Translation.FROM_MSG)
            temp.USER_STATES[user_id] = {
                "state": "awaiting_source", 
                "to_chat_id": resolved_chat_id, 
                "prompt_message_id": prompt_message.id
            }
            return

        except Exception as e:
            await bot.send_message(user_id, f"❌ Error resolving target: {e}\nTry again.")
            return

    elif current_state == "awaiting_source":
        to_chat_id = state_info["to_chat_id"]
        parsed_res = parse_message_input(message)
        from_chat_id, end_id, info = parsed_res
        
        mode = "standard"
        if info == "id_scan":
            mode = "id_scan"
            if isinstance(from_chat_id, str) and from_chat_id.isdigit():
                 from_chat_id = int(from_chat_id)
            
            bot_id = temp.FORWARD_BOT_ID.get(user_id)
            _bot_data = await db.get_bot(user_id, bot_id) if bot_id else None
            
            if _bot_data and _bot_data.get('is_bot') and (isinstance(from_chat_id, str) or message.text.startswith("chat://")):
                 await bot.send_message(user_id, "❌ `chat://` source is only supported for **Userbots**.\nPlease provide a forwarded message or link.")
                 return

        elif info: 
             await bot.send_message(user_id, f"❌ {info}\nPlease try again.")
             return 
        
        status_msg = await bot.send_message(user_id, "`Verifying Source...`")
        from_title = None

        try:
            bot_id = temp.FORWARD_BOT_ID.get(user_id)
            if not bot_id:
                await status_msg.delete()
                return await bot.send_message(user_id, "Bot selection lost. Please restart.")
            
            _bot_data = await db.get_bot(user_id, bot_id)
            if not _bot_data:
                await status_msg.delete()
                return await bot.send_message(user_id, "Selected bot/userbot not found in DB.")

            async with CLIENT().client(_bot_data) as client_instance:
                chat_info = None
                try:
                    chat_info = await client_instance.get_chat(from_chat_id)
                except PeerIdInvalid:
                    async for dialog in client_instance.get_dialogs(limit=500):
                        if (isinstance(from_chat_id, int) and dialog.chat.id == from_chat_id) or \
                           (isinstance(from_chat_id, str) and dialog.chat.username and dialog.chat.username.lower() == from_chat_id.lower()):
                            chat_info = dialog.chat
                            from_chat_id = dialog.chat.id
                            break
                    if not chat_info:
                         raise ValueError("Peer not found in dialogs.")
                except Exception as e:
                     if isinstance(from_chat_id, str):
                         try:
                             chat_info = await client_instance.get_chat(from_chat_id)
                             from_chat_id = chat_info.id
                         except: raise ValueError(f"Could not resolve: {e}")
                     else:
                        raise ValueError(f"Could not access chat: {e}")

                from_title = chat_info.title or f"{chat_info.first_name} {chat_info.last_name or ''}".strip()
                
                if end_id is None:
                    async for msg in client_instance.get_chat_history(from_chat_id, limit=1):
                        end_id = msg.id
                        break
                    if not end_id:
                        raise ValueError("Could not fetch history (Chat might be empty).")
        
        except Exception as e:
            await status_msg.delete()
            await bot.send_message(user_id, f"❌ Error verifying source: `{e}`\nTry again.")
            return

        temp.USER_STATES.pop(user_id, None)
        await start_range_selection(bot, status_msg, from_chat_id, from_title, to_chat_id, 1, end_id, mode=mode)

    elif current_state == "awaiting_range_edit":
        session_id, value_type = state_info.get("session_id"), state_info.get("value_type")
        session = temp.RANGE_SESSIONS.get(session_id)
        if not session: 
            temp.USER_STATES.pop(user_id, None)
            return await bot.send_message(user_id, "Your session has expired. Please start over.")
        
        if message.text and message.text.isdigit():
            session[f'{value_type}_id'] = int(message.text)
            await update_range_message(bot, session_id)
            temp.USER_STATES.pop(user_id, None)
        else:
            await bot.send_message(user_id, "❌ Invalid ID. Please send a number.")
            return

    elif current_state == "awaiting_channel_forward":
        if not message.forward_date: 
            await bot.send_message(user_id, "❌ Not a forwarded message.\nPlease forward a message from the target channel.")
            return
        
        chat_id, title = message.forward_from_chat.id, message.forward_from_chat.title
        username = f"@{message.forward_from_chat.username}" if message.forward_from_chat.username else "private"
        
        if await db.in_channel(user_id, chat_id): 
            await bot.send_message(user_id, "This channel has already been added.")
        else: 
            await db.add_channel(user_id, chat_id, title, username)
            await bot.send_message(user_id, "Channel added. ✓")
        temp.USER_STATES.pop(user_id, None)
    
    elif current_state == "awaiting_bot_token":
        await CLIENT().add_bot(bot, message)
        temp.USER_STATES.pop(user_id, None)
    
    elif current_state == "awaiting_user_session":
        await CLIENT().add_session(bot, message)
        temp.USER_STATES.pop(user_id, None)

    elif current_state and current_state.startswith("awaiting_setting_"):
        setting_key = current_state.split("awaiting_setting_")[1]
        value = None
        
        if message.text and message.text.lower() == "/reset": 
            value = None
        elif setting_key == "file_size":
            try: 
                value = float(message.text) * 1024 * 1024
            except ValueError: 
                await bot.send_message(user_id, "❌ Invalid number. Please enter a valid number (e.g., 10 or 2.5).")
                return
        elif setting_key == "button":
             # VALIDATE BUTTON
             if not parse_buttons(message.text, markup=False):
                 await bot.send_message(user_id, "❌ Invalid button format.\n\nUse: `[Text][url:link]`")
                 return
             value = message.text
        elif setting_key == "db_uri":
             # VALIDATE MONGODB
             if not message.text.startswith("mongodb"):
                 await bot.send_message(user_id, "❌ Invalid MongoDB URI. It must start with `mongodb`.")
                 return
             value = message.text
        elif setting_key == "thumbnail":
            if message.photo:
                value = message.photo.file_id
            elif message.document and message.document.mime_type.startswith("image/"):
                value = message.document.file_id
            else:
                await bot.send_message(user_id, "❌ Invalid media. Send a Photo or an Image Document.")
                return
        else: 
            value = message.text

        await update_configs(user_id, setting_key, value)
        
        # --- RESTORE SETTINGS MENU ---
        temp.USER_STATES.pop(user_id, None)
        
        if setting_key == "thumbnail":
            buttons = [
                [InlineKeyboardButton("View Current", callback_data="settings#viewthumb")],
                [InlineKeyboardButton("Change", callback_data="settings#changethumb"),
                 InlineKeyboardButton("Delete", callback_data="settings#delthumb")],
                [InlineKeyboardButton("Back", callback_data="settings#main")]
            ]
            await bot.send_message(user_id, "<b>Custom Thumbnail</b>\n\nThumbnail saved successfully.", reply_markup=InlineKeyboardMarkup(buttons))
        
        elif setting_key == "file_size":
             # Restore the Merged File Size Menu
             configs = await get_configs(user_id) # Fetch fresh
             size_limit_bytes = configs.get('file_size')
             mode = configs.get('size_limit', 'below')
             
             size_display = "Not Set"
             if size_limit_bytes:
                mb_value = float(size_limit_bytes) / (1024 * 1024)
                size_display = f"{mb_value:.2f} MB"
             mode_display = "Below (Skip larger files)" if mode == 'below' else "Above (Skip smaller files)"
             
             text = (
                "<b>FILE SIZE FILTER</b>\n\n"
                f"<b>Current Limit:</b> <code>{size_display}</code>\n"
                f"<b>Mode:</b> <code>{mode_display}</code>\n\n"
                "Limit updated successfully."
            )
             buttons = []
             buttons.append([InlineKeyboardButton("Set Limit (MB)", callback_data="settings#set#file_size")])
             toggle_text = "Switch to 'Above'" if mode == 'below' else "Switch to 'Below'"
             buttons.append([InlineKeyboardButton(toggle_text, callback_data="settings#toggle_size_limit")])
             if size_limit_bytes:
                buttons.append([InlineKeyboardButton("Reset Limit", callback_data="settings#reset#file_size")])
             buttons.append([InlineKeyboardButton("Back", callback_data="settings#main")])
             
             await bot.send_message(user_id, text, reply_markup=InlineKeyboardMarkup(buttons))

        elif setting_key in SETTING_META:
            meta = SETTING_META[setting_key]
            buttons = [
                [InlineKeyboardButton("View Value", callback_data=f"settings#view#{setting_key}")],
                [InlineKeyboardButton("Update Value", callback_data=f"settings#set#{setting_key}")],
                [InlineKeyboardButton("Reset Value", callback_data=f"settings#reset#{setting_key}")],
                [InlineKeyboardButton("Back", callback_data="settings#main")]
            ]
            await bot.send_message(user_id, f"<b>{meta['title']}</b>\n\nValue updated successfully.", reply_markup=InlineKeyboardMarkup(buttons))
        else:
            await bot.send_message(user_id, f"✅ **{setting_key.replace('_', ' ').title()}** has been updated.")


    elif current_state == "awaiting_unequify_manual_target":
        userbot_id = temp.UNEQUIFY_USERBOT_ID.get(user_id)
        if not userbot_id: 
            temp.USER_STATES.pop(user_id, None)
            return await bot.send_message(user_id, "Error: Userbot selection lost.")
        
        await process_unequify_target(bot, message, user_id, userbot_id, message.text)
        temp.USER_STATES.pop(user_id, None)

    elif current_state == "awaiting_unequify_chat_selection":
        chats, user_input = state_info.get("chats", {}), message.text.strip()
        selected_chat = chats.get(user_input)
        if not selected_chat: 
            await bot.send_message(user_id, "❌ Invalid selection. Try again.")
            return 
        
        userbot_id = temp.UNEQUIFY_USERBOT_ID.get(user_id)
        if not userbot_id: 
            temp.USER_STATES.pop(user_id, None)
            return await bot.send_message(user_id, "Error: Userbot selection lost.")
            
        await process_unequify_target(bot, message, user_id, userbot_id, selected_chat.id)
        temp.USER_STATES.pop(user_id, None)

@Client.on_callback_query(filters.regex(r"^range_"))
async def range_selection_callbacks(bot, query):
    user_id = query.from_user.id
    parts = query.data.split("_")
    action, session_id = parts[1], parts[-1]
    session = temp.RANGE_SESSIONS.get(session_id)
    if not session or session['user_id'] != user_id:
        return await query.answer("This is not for you, or the session has expired.", show_alert=True)
    if action == "cancel":
        temp.RANGE_SESSIONS.pop(session_id, None)
        await query.message.delete(); await bot.send_message(user_id, "Operation cancelled.")
    elif action == "swap":
        session['order'] = 'desc' if session['order'] == 'asc' else 'asc'
        session['start_id'], session['end_id'] = session['end_id'], session['start_id']
        await update_range_message(bot, session_id, message_to_edit=query.message); await query.answer(f"Order swapped!")
    elif action == "edit":
        value_type = parts[2]
        await query.message.delete()
        prompt = await bot.send_message(user_id, f"Send the new **{value_type.upper()} ID**.")
        temp.USER_STATES[user_id] = {
            "state": "awaiting_range_edit", 
            "session_id": session_id, 
            "value_type": value_type, 
            "prompt_message_id": prompt.id
        }
    elif action == "confirm":
        final_callback = session.get('final_callback')
        if final_callback == 'fwd_final': await show_final_confirmation(bot, session_id, query.message)
        elif final_callback == 'uneq_final': from plugins.unequify import prompt_type_selection; await prompt_type_selection(bot, query, session_id)

async def show_final_confirmation(bot, session_id, message_to_edit=None):
    session = temp.RANGE_SESSIONS.get(session_id)
    if not session: return
    user_id, bot_id = session['user_id'], temp.FORWARD_BOT_ID.get(session['user_id'])
    if not bot_id: return await bot.send_message(user_id, "Error: Bot selection lost.")
    _bot, channels = await db.get_bot(user_id, bot_id), await db.get_user_channels(user_id)
    
    to_title = "Unknown"
    for c in channels:
        if c['chat_id'] == session['to_chat_id']:
            to_title = c['title']
            break
    if to_title == "Unknown": to_title = f"ID: {session['to_chat_id']}"

    message_range_text = f"{min(session['start_id'], session['end_id'])} to {max(session['start_id'], session['end_id'])}"
    forward_id = str(uuid4())
    
    sts = STS(forward_id).store(From=session['from_chat_id'], to=session['to_chat_id'], start_id=session['start_id'], end_id=session['end_id'])
    sts.data[forward_id]['mode'] = session.get('mode', 'standard')
    
    text = Translation.DOUBLE_CHECK.format(
            botname=_bot.get('name', 'N/A'), botuname=_bot.get('username', ''),
            from_chat=session['from_title'], to_chat=to_title, message_range=message_range_text)
    
    markup = InlineKeyboardMarkup([
            [InlineKeyboardButton('✓ Yes, Start Forwarding', callback_data=f"start_public_{forward_id}")],
            [InlineKeyboardButton('« No, Cancel', callback_data="close_btn")]
        ])

    if message_to_edit:
        try: await message_to_edit.edit_text(text, reply_markup=markup, disable_web_page_preview=True)
        except: await bot.send_message(user_id, text, reply_markup=markup, disable_web_page_preview=True)
    else:
        await bot.send_message(user_id, text, reply_markup=markup, disable_web_page_preview=True)
        
    temp.RANGE_SESSIONS.pop(session_id, None)

@Client.on_callback_query(filters.regex(r'^close_btn$'))
async def close_callback(bot, query):
    user_id = query.from_user.id
    if not temp.lock.get(user_id):
        temp.USER_STATES.pop(user_id, None)
    await query.message.delete()
