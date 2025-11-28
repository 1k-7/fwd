# mistaldrin/fwd/fwd-dawn-improve-v2/plugins/public.py
import re
import asyncio
import logging
import random
from uuid import uuid4
from .utils import STS, start_range_selection, update_range_message
from database import db
from config import temp
from translation import Translation
from .test import CLIENT, update_configs
from .unequify import process_unequify_target
from pyrogram import Client, filters, enums
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, CallbackQuery, Message

logger = logging.getLogger(__name__)

def parse_message_input(message):
    """Parses a forwarded message or a message link."""
    if not message or (not message.text and not message.forward_date):
        return None, None, "Invalid input. A message link or forwarded message is required."

    if message.text:
        # Check for openmessage link: tg://openmessage?user_id=...&message_id=...
        open_msg_match = re.search(r"tg://openmessage\?user_id=(\d+)(?:&message_id=(\d+))?", message.text)
        if open_msg_match:
            chat_id = int(open_msg_match.group(1))
            msg_id = int(open_msg_match.group(2)) if open_msg_match.group(2) else None
            return chat_id, msg_id, None

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
        buttons.append([InlineKeyboardButton("« Cancel", callback_data="close_btn")])
        await message.reply("<b>Select a Bot or Userbot</b>", reply_markup=InlineKeyboardMarkup(buttons))

@Client.on_callback_query(filters.regex(r'^fwd_select_bot_'))
async def cb_select_bot(bot, query):
    bot_id = int(query.data.split('_')[-1])
    temp.FORWARD_BOT_ID[query.from_user.id] = bot_id
    await query.message.delete()
    await prompt_target_channel(bot, query.message)

async def prompt_target_channel(bot, message):
    user_id = message.chat.id
    channels = await db.get_user_channels(user_id)
    if not channels:
       return await message.reply("Add a target channel first. ( >⁠.⁠< ) --> /settings")

    buttons = [[InlineKeyboardButton(c['title'], callback_data=f"fwd_target_{c['chat_id']}")] for c in channels]
    buttons.append([InlineKeyboardButton("« Cancel", callback_data="close_btn")])
    await message.reply(Translation.TO_MSG, reply_markup=InlineKeyboardMarkup(buttons))

@Client.on_callback_query(filters.regex(r'^fwd_target_'))
async def cb_select_target(bot, query):
    user_id = query.from_user.id
    to_chat_id = int(query.data.split('_')[-1])
    
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

    # Universal /cancel
    if message.text and message.text.lower() == "/cancel":
        if prompt_id:
            try: await bot.delete_messages(user_id, prompt_id)
            except Exception: pass
        temp.USER_STATES.pop(user_id, None)
        return await message.reply(Translation.CANCEL)

    # Clean up prompts and user message
    if prompt_id:
        try: await bot.delete_messages(user_id, prompt_id)
        except: pass
    try: await message.delete()
    except: pass
    
    temp.USER_STATES.pop(user_id, None)

    # --- FORWARDING WORKFLOW ---
    if current_state == "awaiting_source":
        to_chat_id = state_info["to_chat_id"]
        from_chat_id, end_id, error = parse_message_input(message)
        if error: return await bot.send_message(user_id, error)
        
        status_msg = await bot.send_message(user_id, "`Verifying Source...`")
        from_title = None

        # Resolve the peer (and end_id if missing) using the SELECTED BOT/USERBOT
        # This is critical for raw IDs (like from tg://openmessage) that the main bot instance doesn't know.
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
                # 1. Resolve Peer & Get Title (Crucial for PeerIdInvalid prevention)
                try:
                    chat_info = await client_instance.get_chat(from_chat_id)
                    from_title = chat_info.title or f"{chat_info.first_name} {chat_info.last_name or ''}".strip()
                except Exception as e:
                    logger.error(f"Failed to resolve peer {from_chat_id}: {e}")
                    # If we can't resolve it, we can't proceed with this specific client
                    raise ValueError(f"Could not access chat/user: {e}")

                # 2. Fetch Latest Message if end_id is missing
                if end_id is None:
                    async for msg in client_instance.get_chat_history(from_chat_id, limit=1):
                        end_id = msg.id
                        break
                    
                    if not end_id:
                        raise ValueError("Could not fetch history (Chat might be empty).")
        
        except Exception as e:
            await status_msg.delete()
            return await bot.send_message(user_id, f"Error verifying source: `{e}`")

        await status_msg.delete()
        
        # Fallback title if something went weird but didn't crash
        if not from_title: from_title = f"Chat: {from_chat_id}"
        
        # Start selection from 1 to end_id
        await start_range_selection(bot, message, from_chat_id, from_title, to_chat_id, 1, end_id)

    elif current_state == "awaiting_range_edit":
        session_id, value_type = state_info.get("session_id"), state_info.get("value_type")
        session = temp.RANGE_SESSIONS.get(session_id)
        if not session: return await bot.send_message(user_id, "Your session has expired. Please start over.")
        if message.text and message.text.isdigit():
            session[f'{value_type}_id'] = int(message.text)
            await update_range_message(bot, session_id)
        else:
            await bot.send_message(user_id, "Invalid ID provided. The process has been cancelled.")
            temp.RANGE_SESSIONS.pop(session_id, None)

    # --- SETTINGS WORKFLOW ---
    elif current_state == "awaiting_channel_forward":
        if not message.forward_date: return await bot.send_message(user_id, "Not a forwarded message. (・_・;)\nProcess cancelled.")
        chat_id, title = message.forward_from_chat.id, message.forward_from_chat.title
        username = f"@{message.forward_from_chat.username}" if message.forward_from_chat.username else "private"
        if await db.in_channel(user_id, chat_id): await bot.send_message(user_id, "This channel has already been added.")
        else: await db.add_channel(user_id, chat_id, title, username); await bot.send_message(user_id, "Channel added. ✓")
    
    elif current_state == "awaiting_bot_token":
        await CLIENT().add_bot(bot, message)
    
    elif current_state == "awaiting_user_session":
        await CLIENT().add_session(bot, message)

    elif current_state and current_state.startswith("awaiting_setting_"):
        setting_key = current_state.split("awaiting_setting_")[1]
        value = None
        if message.text.lower() == "/reset": value = None
        elif setting_key == "file_size":
            try: value = float(message.text) * 1024 * 1024 # MB to bytes
            except ValueError: return await bot.send_message(user_id, "Invalid number for file size.")
        elif setting_key == "size_limit":
            if message.text.lower() not in ["above", "below"]: return await bot.send_message(user_id, "Invalid option. Please enter 'above' or 'below'.")
            value = message.text.lower()
        else: value = message.text
        await update_configs(user_id, setting_key, value)
        await bot.send_message(user_id, f"✅ **{setting_key.replace('_', ' ').title()}** has been updated.")

    # --- UNEQIFY WORKFLOW ---
    elif current_state == "awaiting_unequify_manual_target":
        userbot_id = temp.UNEQUIFY_USERBOT_ID.get(user_id)
        if not userbot_id: return await bot.send_message(user_id, "Error: Userbot selection lost. Please start over.")
        await process_unequify_target(bot, message, user_id, userbot_id, message.text)

    elif current_state == "awaiting_unequify_chat_selection":
        chats, user_input = state_info.get("chats", {}), message.text.strip()
        selected_chat = chats.get(user_input)
        if not selected_chat: return await bot.send_message(user_id, "Invalid selection. Please start the /unequify process again.")
        userbot_id = temp.UNEQUIFY_USERBOT_ID.get(user_id)
        if not userbot_id: return await bot.send_message(user_id, "Error: Userbot selection lost. Please start over.")
        await process_unequify_target(bot, message, user_id, userbot_id, selected_chat.id)

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
        await query.message.delete()
        final_callback = session.get('final_callback')
        if final_callback == 'fwd_final': await show_final_confirmation(bot, session_id)
        elif final_callback == 'uneq_final': from plugins.unequify import prompt_type_selection; await prompt_type_selection(bot, query, session_id)

async def show_final_confirmation(bot, session_id):
    session = temp.RANGE_SESSIONS.get(session_id)
    if not session: return
    user_id, bot_id = session['user_id'], temp.FORWARD_BOT_ID.get(session['user_id'])
    if not bot_id: return await bot.send_message(user_id, "Error: Bot selection lost.")
    _bot, channels = await db.get_bot(user_id, bot_id), await db.get_user_channels(user_id)
    to_title = next((c['title'] for c in channels if c['chat_id'] == session['to_chat_id']), 'Unknown')
    message_range_text = f"{min(session['start_id'], session['end_id'])} to {max(session['start_id'], session['end_id'])}"
    forward_id = str(uuid4())
    STS(forward_id).store(From=session['from_chat_id'], to=session['to_chat_id'], start_id=session['start_id'], end_id=session['end_id'])
    await bot.send_message(user_id, Translation.DOUBLE_CHECK.format(
            botname=_bot.get('name', 'N/A'), botuname=_bot.get('username', ''),
            from_chat=session['from_title'], to_chat=to_title, message_range=message_range_text),
        disable_web_page_preview=True,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton('✓ Yes, Start Forwarding', callback_data=f"start_public_{forward_id}")],
            [InlineKeyboardButton('« No, Cancel', callback_data="close_btn")]
        ]))
    temp.RANGE_SESSIONS.pop(session_id, None)

@Client.on_callback_query(filters.regex(r'^close_btn$'))
async def close_callback(bot, query):
    user_id = query.from_user.id
    if not temp.lock.get(user_id):
        temp.USER_STATES.pop(user_id, None)
    await query.message.delete()