# plugins/restricted.py
import os
import asyncio
import time
import math
import logging
from uuid import uuid4
from pyrogram import Client, filters, enums
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message, CallbackQuery
from pyrogram.errors import FloodWait, PeerIdInvalid, MessageNotModified

# Import from existing modules
from database import db
from config import temp
from plugins.test import CLIENT as PyClient, start_clone_bot
from plugins.utils import STS, start_range_selection, edit_or_reply, get_readable_time
from plugins.public import parse_message_input, custom_caption, get_size

logger = logging.getLogger(__name__)

# ==========================================
# DATABASE HELPERS FOR RESTRICTED SETTINGS
# ==========================================
async def get_restr_configs(user_id):
    """Fetch specialized configurations for restricted forwarding."""
    user = await db.col.find_one({'id': int(user_id)})
    default = {
        'file_size': 0, # 0 means no limit
        'caption': None,
        'delay': 2.0 # Higher default delay due to upload/download limits
    }
    if user and 'restr_configs' in user:
        default.update(user['restr_configs'])
    return default

async def update_restr_configs(user_id, key, value):
    """Update specialized configurations."""
    configs = await get_restr_configs(user_id)
    configs[key] = value
    await db.col.update_one({'id': int(user_id)}, {'$set': {'restr_configs': configs}}, upsert=True)

# ==========================================
# /restrsettings - SETTINGS MENU
# ==========================================
@Client.on_message(filters.private & filters.command(['restrsettings']))
async def restr_settings(client, message):
    user_id = message.from_user.id
    if temp.lock.get(user_id):
        return await message.reply("A task is already in progress.")
    await show_restr_settings(message, user_id)

async def show_restr_settings(message, user_id):
    configs = await get_restr_configs(user_id)
    
    size_limit = configs.get('file_size', 0)
    display_size = f"{float(size_limit)/1048576:.2f} MB" if size_limit else "No Limit"
    delay = configs.get('delay', 2.0)
    
    text = (
        "<b>Restricted Forwarding Settings</b>\n\n"
        f"<b>Max File Size:</b> <code>{display_size}</code>\n"
        f"<b>Forward Delay:</b> <code>{delay} seconds</code>\n"
        f"<b>Custom Caption:</b> {'Set' if configs.get('caption') else 'Not Set'}\n\n"
        "<i>These settings only apply to the /fwdrestricted command.</i>"
    )
    
    buttons = [
        [InlineKeyboardButton(f"Max Size: {display_size}", callback_data="restr_set_file_size")],
        [InlineKeyboardButton("Set Caption", callback_data="restr_set_caption"), 
         InlineKeyboardButton("Set Delay", callback_data="restr_set_delay")],
        [InlineKeyboardButton("Close", callback_data="close_btn")]
    ]
    await edit_or_reply(message, text, reply_markup=InlineKeyboardMarkup(buttons))

@Client.on_callback_query(filters.regex(r'^restr_set_'))
async def restr_set_callback(bot, query):
    user_id = query.from_user.id
    setting_key = query.data.split('restr_set_')[1]
    
    prompts = {
        "file_size": "Send the maximum file size in <b>MB</b> (e.g., 50 or 1.5). Files larger than this will be skipped.\nSend `0` for no limit.",
        "caption": "Send your custom caption.\nUse `{filename}`, `{size}`, and `{caption}` as placeholders.\nSend `/reset` to remove.",
        "delay": "Send the delay in seconds (e.g. 2.5). Recommended > 2s to avoid floodwaits."
    }
    
    prompt_msg = await edit_or_reply(query.message, prompts[setting_key], reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Cancel", callback_data="restr_settings_back")]]))
    temp.USER_STATES[user_id] = {"state": f"awaiting_restr_{setting_key}", "prompt_message_id": prompt_msg.id}

@Client.on_callback_query(filters.regex(r'^restr_settings_back$'))
async def restr_settings_back(bot, query):
    user_id = query.from_user.id
    temp.USER_STATES.pop(user_id, None)
    await show_restr_settings(query.message, user_id)

# Input Handler (Group -3 to catch before public.py or settings.py)
@Client.on_message(filters.private & filters.incoming, group=-3)
async def restr_input_handler(bot, message):
    if message.edit_date: return
    user_id = message.from_user.id
    state_info = temp.USER_STATES.get(user_id)
    if not state_info: return

    current_state = state_info.get("state", "")
    if not current_state.startswith("awaiting_restr_"):
        return # Not our state

    # Handle /cancel
    if message.text and message.text.lower() == "/cancel":
        temp.USER_STATES.pop(user_id, None)
        await message.reply("Cancelled.")
        message.stop_propagation()

    setting_key = current_state.replace("awaiting_restr_", "")
    value = None
    
    if setting_key == "source":
        # Handle source selection for /fwdrestricted
        await handle_restr_source(bot, message, user_id, state_info)
        message.stop_propagation()
        
    try: await message.delete()
    except: pass
    
    if message.text and message.text.lower() == "/reset":
        value = None
    elif setting_key == "file_size":
        try: value = float(message.text) * 1024 * 1024
        except: return await bot.send_message(user_id, "❌ Invalid number.")
    elif setting_key == "delay":
        try: value = float(message.text)
        except: return await bot.send_message(user_id, "❌ Invalid number.")
    elif setting_key == "caption":
        value = message.text

    await update_restr_configs(user_id, setting_key, value)
    temp.USER_STATES.pop(user_id, None)
    
    prompt_id = state_info.get("prompt_message_id")
    if prompt_id:
        try: await bot.delete_messages(user_id, prompt_id)
        except: pass
    
    msg = await bot.send_message(user_id, "✅ Setting updated.")
    await show_restr_settings(msg, user_id)
    message.stop_propagation()


# ==========================================
# /fwdrestricted - COMMAND FLOW
# ==========================================
@Client.on_message(filters.private & filters.command(["fwdrestricted"]))
async def fwd_restricted_cmd(bot, message):
    user_id = message.from_user.id
    if temp.lock.get(user_id):
        return await message.reply("A task is already in progress.")
    
    temp.USER_STATES.pop(user_id, None)
    
    # We highly recommend userbots since bots cannot read restricted chats normally
    bots = await db.get_bots(user_id)
    userbots = [b for b in bots if not b.get('is_bot')]
    
    if not userbots:
        return await message.reply("You must add a **Userbot** to use Restricted Forwarding.\n(Go to /settings -> Bots & Userbots -> Add Userbot)")

    if len(userbots) == 1:
        temp.FORWARD_BOT_ID[user_id] = userbots[0]['id']
        await prompt_restr_target(bot, message)
    else:
        buttons = [[InlineKeyboardButton(b.get('name') or f"ID: {b['id']}", callback_data=f"restr_bot_{b['id']}")] for b in userbots]
        buttons.append([InlineKeyboardButton("Cancel", callback_data="close_btn")])
        await message.reply("<b>Select a Userbot to Download with:</b>", reply_markup=InlineKeyboardMarkup(buttons))

@Client.on_callback_query(filters.regex(r'^restr_bot_'))
async def cb_restr_bot(bot, query):
    bot_id = int(query.data.split('_')[-1])
    temp.FORWARD_BOT_ID[query.from_user.id] = bot_id
    await prompt_restr_target(bot, query.message)

async def prompt_restr_target(bot, message):
    user_id = message.chat.id
    channels = await db.get_user_channels(user_id)
    
    chan_btns = [InlineKeyboardButton(c['title'], callback_data=f"restr_target_{c['chat_id']}") for c in channels]
    grid = [chan_btns[i:i+2] for i in range(0, len(chan_btns), 2)]
    grid.append([InlineKeyboardButton("Cancel", callback_data="close_btn")])
    
    await edit_or_reply(message, "<b>Restricted Forward: Target Chat?</b>\nSelect where to upload the files.", reply_markup=InlineKeyboardMarkup(grid))

@Client.on_callback_query(filters.regex(r'^restr_target_'))
async def cb_restr_target(bot, query):
    user_id = query.from_user.id
    to_chat_id = int(query.data.split('_')[-1])
    
    prompt = await query.message.edit_text("<b>Source Chat?</b>\n\nSend a link to the message inside the restricted channel/group.\n\n/cancel - abort.")
    temp.USER_STATES[user_id] = {"state": "awaiting_restr_source", "to_chat_id": to_chat_id, "prompt_message_id": prompt.id}

async def handle_restr_source(bot, message, user_id, state_info):
    to_chat_id = state_info["to_chat_id"]
    from_chat_id, end_id, info = parse_message_input(message)
    
    if not from_chat_id:
        return await bot.send_message(user_id, "❌ Invalid input. Please send a valid Telegram message link.")
    
    status_msg = await bot.send_message(user_id, "`Verifying Source...`")
    bot_id = temp.FORWARD_BOT_ID.get(user_id)
    _bot_data = await db.get_bot(user_id, bot_id)
    
    try:
        async with PyClient().client(_bot_data) as client_instance:
            chat_info = await client_instance.get_chat(from_chat_id)
            from_title = chat_info.title or chat_info.first_name
            if not end_id:
                async for msg in client_instance.get_chat_history(from_chat_id, limit=1):
                    end_id = msg.id
                    break
    except Exception as e:
        await status_msg.delete()
        return await bot.send_message(user_id, f"❌ Error verifying source: `{e}`\nEnsure the userbot is in the restricted chat.")
    
    # We delegate range selection to the public tool, but give it a unique final callback
    await start_range_selection(bot, status_msg, from_chat_id, from_title, to_chat_id, 1, end_id, final_callback_prefix="restr_final")


# ==========================================
# FINAL CONFIRMATION & TASK EXECUTION
# ==========================================
@Client.on_callback_query(filters.regex(r'^range_confirm_restr_final_'))
async def restr_final_confirmation(bot, query):
    session_id = query.data.split('_')[-1]
    session = temp.RANGE_SESSIONS.pop(session_id, None)
    if not session: return await query.answer("Session expired.", show_alert=True)
    
    user_id = query.from_user.id
    task_id = str(uuid4())
    
    # Initialize STS for this run
    sts = STS(task_id).store(From=session['from_chat_id'], to=session['to_chat_id'], start_id=session['start_id'], end_id=session['end_id'])
    
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("🚀 Start Downloading & Uploading", callback_data=f"start_restr_task_{task_id}")],
        [InlineKeyboardButton("Cancel", callback_data="close_btn")]
    ])
    
    text = (
        "<b>Restricted Mode Ready</b>\n\n"
        f"<b>From:</b> <code>{session['from_title']}</code>\n"
        f"<b>To Chat ID:</b> <code>{session['to_chat_id']}</code>\n"
        f"<b>Range:</b> <code>{session['start_id']}</code> to <code>{session['end_id']}</code>\n\n"
        "<i>Note: This process downloads files to the server and re-uploads them. It will be slower than normal forwarding.</i>"
    )
    
    await query.message.edit_text(text, reply_markup=markup)

@Client.on_callback_query(filters.regex(r'^start_restr_task_'))
async def start_restr_task(bot, query):
    user_id = query.from_user.id
    task_id = query.data.split('_')[-1]
    
    if temp.lock.get(user_id):
         return await query.answer("A task is already running.", show_alert=True)
         
    temp.lock[user_id] = True
    temp.CANCEL[task_id] = False
    
    sts = STS(task_id)
    if not sts.verify(): return await query.answer("Invalid task session.", show_alert=True)
    
    bot_id = temp.FORWARD_BOT_ID.get(user_id)
    _bot_data = await db.get_bot(user_id, bot_id)
    configs = await get_restr_configs(user_id)
    
    status_msg = await query.message.edit_text("`Initializing Restricted Runner...`")
    
    # Run in background
    asyncio.create_task(restricted_worker(bot, user_id, task_id, _bot_data, configs, status_msg, sts))


async def restricted_worker(bot, user_id, task_id, bot_data, configs, message_obj, sts):
    i = sts.get(full=True)
    client_instance = None
    
    start_id = min(i.start_id, i.end_id)
    end_id = max(i.start_id, i.end_id)
    
    delay = configs.get('delay', 2.0)
    size_limit = configs.get('file_size', 0)
    custom_cap = configs.get('caption')
    
    temp.ACTIVE_TASKS[user_id] = {task_id: {"process": message_obj, "details": {"type": "Restricted Forwarding", "from": str(i.FROM), "to": str(i.TO)}}}
    last_update = time.time()
    
    try:
        client_instance = await start_clone_bot(PyClient().client(bot_data), bot_data)
        
        for msg_id in range(start_id, end_id + 1):
            if temp.CANCEL.get(task_id): break
            
            # Update UI occasionally
            if time.time() - last_update > 5:
                await edit_restr_progress(message_obj, sts, "Running")
                last_update = time.time()
            
            file_path = None
            try:
                # Fetch single message safely
                messages = await client_instance.get_messages(i.FROM, [msg_id])
                if not messages:
                    sts.add('failed')
                    sts.add('fetched')
                    continue
                    
                msg = messages[0]
                if msg.empty:
                    sts.add('filtered')
                    sts.add('fetched')
                    continue
                
                sts.add('fetched')
                
                # Extract Caption
                capt = custom_caption(msg, custom_cap)
                
                # --- Text Message ---
                if msg.text:
                    await client_instance.send_message(i.TO, msg.text.html, parse_mode=enums.ParseMode.HTML)
                    sts.add('total_files')
                    await asyncio.sleep(delay)
                    continue
                
                # --- Media Message ---
                if msg.media:
                    media_obj = getattr(msg, msg.media.value, None)
                    if media_obj:
                        f_size = getattr(media_obj, 'file_size', 0)
                        if size_limit > 0 and f_size > size_limit:
                            sts.add('filtered')
                            continue
                    
                    # Download
                    file_path = await client_instance.download_media(msg)
                    if not file_path:
                        sts.add('failed')
                        continue
                    
                    # Upload
                    if msg.photo:
                        await client_instance.send_photo(i.TO, file_path, caption=capt)
                    elif msg.video:
                        await client_instance.send_video(i.TO, file_path, caption=capt)
                    elif msg.document:
                        await client_instance.send_document(i.TO, file_path, caption=capt)
                    elif msg.audio:
                        await client_instance.send_audio(i.TO, file_path, caption=capt)
                    elif msg.voice:
                        await client_instance.send_voice(i.TO, file_path, caption=capt)
                    elif msg.animation:
                        await client_instance.send_animation(i.TO, file_path, caption=capt)
                    else:
                        await client_instance.send_document(i.TO, file_path, caption=capt)
                        
                    sts.add('total_files')
                    await asyncio.sleep(delay)
                    
            except FloodWait as e:
                await asyncio.sleep(e.value + 1)
                # We skip retry logic for restricted to keep it simple, just count as failed
                sts.add('failed')
            except Exception as e:
                logger.error(f"Restricted forward error on {msg_id}: {e}")
                sts.add('failed')
            finally:
                if file_path and os.path.exists(file_path):
                    try: os.remove(file_path)
                    except: pass
                    
    except Exception as e:
        await edit_or_reply(message_obj, f"❌ **Fatal Error:** `{e}`")
    finally:
        final_status = "Cancelled" if temp.CANCEL.get(task_id) else "Completed"
        await edit_restr_progress(message_obj, sts, final_status)
        
        # Cleanup
        if client_instance:
            try: await client_instance.stop()
            except: pass
        if task_id in temp.ACTIVE_TASKS.get(user_id, {}):
            del temp.ACTIVE_TASKS[user_id][task_id]
        temp.CANCEL.pop(task_id, None)
        temp.lock.pop(user_id, None)

async def edit_restr_progress(msg, sts, status):
    i = sts.get(full=True)
    diff = time.time() - i.start
    if diff == 0: diff = 1
    speed = i.fetched / diff
    eta = get_readable_time(int((i.total - i.fetched) / speed if speed > 0 else 0))
    percentage = "{:.2f}".format(i.fetched * 100 / i.total if i.total > 0 else 0.00)
    
    progress_bar = "▰{0}▱{1}".format('▰' * math.floor(float(percentage) / 10), '▱' * (10 - math.floor(float(percentage) / 10)))
    
    if status == "Running":
        text = (
            "<b>📥 Restricted Upload/Download Running 📤</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"<b>📊 Progress:</b> {percentage}%\n"
            f"{progress_bar}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"<b>✅ Sent:</b> {i.total_files}\n"
            f"<b>🚫 Failed:</b> {i.failed}\n"
            f"<b>⏭ Skipped:</b> {i.filtered}\n"
            f"<b>⏳ ETA:</b> {eta}"
        )
        button = InlineKeyboardMarkup([[InlineKeyboardButton('❌ Cancel', callback_data=f'cancel_restr_task_{i.id}')]])
    else:
        text = (
            f"<b>{status} Restricted Forwarding</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"<b>⏱ Time Taken:</b> {get_readable_time(int(diff))}\n"
            f"<b>✅ Uploaded:</b> {i.total_files}\n"
            f"<b>🚫 Failed:</b> {i.failed}\n"
            f"<b>⏭ Skipped:</b> {i.filtered}"
        )
        button = InlineKeyboardMarkup([[InlineKeyboardButton("Done", callback_data="close_btn")]])

    try: await msg.edit_text(text, reply_markup=button)
    except MessageNotModified: pass

@Client.on_callback_query(filters.regex(r'^cancel_restr_task_'))
async def cancel_restr_task_cb(bot, query):
    task_id = query.data.split('_')[-1]
    temp.CANCEL[task_id] = True
    await query.answer("Cancellation signal sent...", show_alert=True)