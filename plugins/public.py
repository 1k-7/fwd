# plugins/public.py
import re
import asyncio
import logging
from uuid import uuid4
from .utils import STS, start_range_selection, update_range_message, edit_or_reply, parse_buttons
from database import db
from config import temp
from translation import Translation
from .test import CLIENT, update_configs, get_configs
from .unequify import process_unequify_target
from .settings import generate_setting_page
from pyrogram import Client, filters, enums
from pyrogram.errors import PeerIdInvalid, MessageNotModified
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, CallbackQuery, Message, InputMediaPhoto

logger = logging.getLogger(__name__)

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

    # --- HANDLE CANCEL ---
    if message.text and message.text.lower() == "/cancel":
        if prompt_id:
            try: await bot.delete_messages(user_id, prompt_id)
            except Exception: pass
        temp.USER_STATES.pop(user_id, None)
        try: await message.delete() 
        except: pass
        await message.reply(Translation.CANCEL)
        message.stop_propagation()
        return

    # --- SETTINGS INPUT HANDLER (Consolidated) ---
    if current_state.startswith("awaiting_setting_"):
        setting_key = current_state.split("awaiting_setting_")[1]
        value = None
        error_msg = None
        
        try: await message.delete()
        except: pass
        
        if message.text and message.text.lower() == "/reset": 
            value = None
        elif setting_key == "file_size":
            if not message.text: error_msg = "❌ Error: Please send a number."
            else:
                try: value = float(message.text) * 1024 * 1024
                except ValueError: error_msg = "❌ Invalid number. Please enter a valid number (e.g., 10 or 2.5)."
        elif setting_key == "button":
             if not message.text: error_msg = "❌ Error: Text required."
             elif not parse_buttons(message.text, markup=False): error_msg = "❌ Invalid button format.\n\nUse: `[Text][buttonurl:link]`"
             else: value = message.text
        elif setting_key == "db_uri":
             if not message.text: error_msg = "❌ Error: Text required."
             elif not (message.text.startswith("mongodb") or message.text.startswith("mongodb+srv")): error_msg = "❌ Invalid MongoDB URI. It must start with `mongodb`."
             else: value = message.text
        elif setting_key == "thumbnail":
            if message.photo: value = message.photo.file_id
            elif message.document and message.document.mime_type.startswith("image/"): value = message.document.file_id
            else: error_msg = "❌ Invalid media. Send a Photo or an Image Document."
        else: 
            if not message.text: error_msg = "❌ Error: Text required."
            else: value = message.text

        if error_msg:
             sent = await bot.send_message(user_id, error_msg)
             message.stop_propagation()
             return

        await update_configs(user_id, setting_key, value)
        temp.USER_STATES.pop(user_id, None)
        
        # REFRESH MENU
        text, markup, thumb_id = await generate_setting_page(user_id, setting_key)
        
        if prompt_id:
            try:
                if thumb_id: 
                     await bot.edit_message_media(chat_id=user_id, message_id=prompt_id, 
                         media=InputMediaPhoto(thumb_id, caption="✅ Saved!\n\n" + text), reply_markup=markup)
                else:
                     await bot.edit_message_text(chat_id=user_id, message_id=prompt_id, 
                         text=f"✅ <b>Saved Successfully!</b>\n\n{text}", reply_markup=markup)
            except Exception:
                try: await bot.delete_messages(user_id, prompt_id)
                except: pass
                if thumb_id: await bot.send_photo(user_id, photo=thumb_id, caption="✅ Saved!\n\n" + text, reply_markup=markup)
                else: await bot.send_message(user_id, f"✅ <b>Saved Successfully!</b>\n\n{text}", reply_markup=markup)
        
        message.stop_propagation()
        return

    # --- BOT / USERBOT / CHANNEL ADDITION ---
    elif current_state == "awaiting_channel_forward":
        try: await message.delete()
        except: pass
        if prompt_id:
             try: await bot.delete_messages(user_id, prompt_id)
             except: pass

        if not message.forward_date: 
            await bot.send_message(user_id, "❌ Not a forwarded message.\nPlease forward a message from the target channel.")
        else:
            chat_id, title = message.forward_from_chat.id, message.forward_from_chat.title
            username = f"@{message.forward_from_chat.username}" if message.forward_from_chat.username else "private"
            if await db.in_channel(user_id, chat_id): await bot.send_message(user_id, "This channel has already been added.")
            else: 
                await db.add_channel(user_id, chat_id, title, username)
                await bot.send_message(user_id, "Channel added. ✓")
        temp.USER_STATES.pop(user_id, None)

    elif current_state == "awaiting_bot_token":
        try: await message.delete()
        except: pass
        if prompt_id:
             try: await bot.delete_messages(user_id, prompt_id)
             except: pass
        await CLIENT().add_bot(bot, message)
        temp.USER_STATES.pop(user_id, None)
    
    elif current_state == "awaiting_user_session":
        try: await message.delete()
        except: pass
        if prompt_id:
             try: await bot.delete_messages(user_id, prompt_id)
             except: pass
        await CLIENT().add_session(bot, message)
        temp.USER_STATES.pop(user_id, None)

    # --- PM TARGET / SOURCE HANDLERS ---
    elif current_state == "awaiting_pm_target":
        try: await message.delete()
        except: pass
        if prompt_id:
             try: await bot.delete_messages(user_id, prompt_id)
             except: pass

        target_input = message.text
        resolved_chat_id = None
        try:
            bot_id = temp.FORWARD_BOT_ID.get(user_id)
            if not bot_id: 
                 await bot.send_message(user_id, "Bot selection lost. Please restart.")
                 message.stop_propagation(); return

            _bot_data = await db.get_bot(user_id, bot_id)
            async with CLIENT().client(_bot_data) as client_instance:
                if message.forward_from: chat = message.forward_from
                elif message.forward_from_chat: chat = message.forward_from_chat
                else:
                    try:
                        if target_input.lstrip('-').isdigit(): target_input = int(target_input)
                        chat = await client_instance.get_chat(target_input)
                    except Exception:
                        await bot.send_message(user_id, "❌ Invalid user/chat. Please try again.")
                        message.stop_propagation(); return 
                resolved_chat_id = chat.id
            prompt_message = await bot.send_message(user_id, Translation.FROM_MSG)
            temp.USER_STATES[user_id] = {"state": "awaiting_source", "to_chat_id": resolved_chat_id, "prompt_message_id": prompt_message.id}
        except Exception as e:
            await bot.send_message(user_id, f"❌ Error resolving target: {e}\nTry again.")

    elif current_state == "awaiting_source":
        try: await message.delete()
        except: pass
        if prompt_id:
             try: await bot.delete_messages(user_id, prompt_id)
             except: pass

        to_chat_id = state_info["to_chat_id"]
        parsed_res = parse_message_input(message)
        from_chat_id, end_id, info = parsed_res
        
        mode = "standard"
        if info == "id_scan":
            mode = "id_scan"
            if isinstance(from_chat_id, str) and from_chat_id.isdigit(): from_chat_id = int(from_chat_id)
            bot_id = temp.FORWARD_BOT_ID.get(user_id)
            _bot_data = await db.get_bot(user_id, bot_id) if bot_id else None
            if _bot_data and _bot_data.get('is_bot') and (isinstance(from_chat_id, str) or message.text.startswith("chat://")):
                 await bot.send_message(user_id, "❌ `chat://` source is only supported for **Userbots**.\nPlease provide a forwarded message or link.")
                 message.stop_propagation(); return
        elif info: 
             await bot.send_message(user_id, f"❌ {info}\nPlease try again.")
             message.stop_propagation(); return 
        
        status_msg = await bot.send_message(user_id, "`Verifying Source...`")
        from_title = None
        try:
            bot_id = temp.FORWARD_BOT_ID.get(user_id)
            if not bot_id:
                await status_msg.delete(); await bot.send_message(user_id, "Bot selection lost. Please restart.")
                message.stop_propagation(); return
            _bot_data = await db.get_bot(user_id, bot_id)
            if not _bot_data:
                await status_msg.delete(); await bot.send_message(user_id, "Selected bot/userbot not found in DB.")
                message.stop_propagation(); return

            async with CLIENT().client(_bot_data) as client_instance:
                chat_info = None
                try:
                    chat_info = await client_instance.get_chat(from_chat_id)
                except PeerIdInvalid:
                    async for dialog in client_instance.get_dialogs(limit=500):
                        if (isinstance(from_chat_id, int) and dialog.chat.id == from_chat_id) or \
                           (isinstance(from_chat_id, str) and dialog.chat.username and dialog.chat.username.lower() == from_chat_id.lower()):
                            chat_info = dialog.chat; from_chat_id = dialog.chat.id; break
                    if not chat_info: raise ValueError("Peer not found in dialogs.")
                except Exception as e:
                     if isinstance(from_chat_id, str):
                         try: chat_info = await client_instance.get_chat(from_chat_id); from_chat_id = chat_info.id
                         except: raise ValueError(f"Could not resolve: {e}")
                     else: raise ValueError(f"Could not access chat: {e}")
                from_title = chat_info.title or f"{chat_info.first_name} {chat_info.last_name or ''}".strip()
                if end_id is None:
                    async for msg in client_instance.get_chat_history(from_chat_id, limit=1): end_id = msg.id; break
                    if not end_id: raise ValueError("Could not fetch history (Chat might be empty).")
        except Exception as e:
            await status_msg.delete(); await bot.send_message(user_id, f"❌ Error verifying source: `{e}`\nTry again.")
            message.stop_propagation(); return

        temp.USER_STATES.pop(user_id, None)
        await start_range_selection(bot, status_msg, from_chat_id, from_title, to_chat_id, 1, end_id, mode=mode)

    elif current_state == "awaiting_range_edit":
        try: await message.delete()
        except: pass
        session_id, value_type = state_info.get("session_id"), state_info.get("value_type")
        session = temp.RANGE_SESSIONS.get(session_id)
        if not session: 
            temp.USER_STATES.pop(user_id, None); await bot.send_message(user_id, "Your session has expired. Please start over.")
        elif message.text and message.text.isdigit():
            session[f'{value_type}_id'] = int(message.text)
            await update_range_message(bot, session_id)
            temp.USER_STATES.pop(user_id, None)
        else:
            await bot.send_message(user_id, "❌ Invalid ID. Please send a number.")

    elif current_state == "awaiting_unequify_manual_target":
        try: await message.delete()
        except: pass
        userbot_id = temp.UNEQUIFY_USERBOT_ID.get(user_id)
        if not userbot_id: 
            temp.USER_STATES.pop(user_id, None); await bot.send_message(user_id, "Error: Userbot selection lost.")
        else:
            await process_unequify_target(bot, message, user_id, userbot_id, message.text)
            temp.USER_STATES.pop(user_id, None)

    elif current_state == "awaiting_unequify_chat_selection":
        try: await message.delete()
        except: pass
        chats, user_input = state_info.get("chats", {}), message.text.strip()
        selected_chat = chats.get(user_input)
        if not selected_chat: 
            await bot.send_message(user_id, "❌ Invalid selection. Try again.")
        else:
            userbot_id = temp.UNEQUIFY_USERBOT_ID.get(user_id)
            if not userbot_id: 
                temp.USER_STATES.pop(user_id, None); await bot.send_message(user_id, "Error: Userbot selection lost.")
            else:
                await process_unequify_target(bot, message, user_id, userbot_id, selected_chat.id)
                temp.USER_STATES.pop(user_id, None)
        
    message.stop_propagation()