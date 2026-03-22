# plugins/restricted.py
import os
import re
import asyncio
import time
import math
import logging
from uuid import uuid4

from pyrogram import Client, filters, enums, StopPropagation
from pyrogram.types import (
    InlineKeyboardButton, 
    InlineKeyboardMarkup,
    InputMediaPhoto, 
    InputMediaVideo, 
    InputMediaDocument, 
    InputMediaAudio
)
from pyrogram.errors import FloodWait, MessageNotModified

# Import from existing modules safely
from database import db
from config import temp
from plugins.test import CLIENT as PyClient, start_clone_bot
from plugins.utils import STS, start_range_selection, edit_or_reply, get_readable_time, format_thumbnail

logger = logging.getLogger(__name__)

# ==========================================
# SELF-CONTAINED HELPER FUNCTIONS
# ==========================================
def get_size(size):
    try:
        if not size: return "0 B"
        units, size = ["B", "KB", "MB", "GB", "TB"], float(size)
        i = 0
        while size >= 1024.0 and i < len(units) - 1:
            i += 1
            size /= 1024.0
        return f"{size:.2f} {units[i]}"
    except: return "N/A"

def custom_caption(msg, caption):
    if not msg: return ""
    fcaption_text = msg.text.html if msg.text else (msg.caption.html if msg.caption else "")
    if not caption: return fcaption_text
    file_name, file_size = "", "0 B"
    if msg.media:
        media = getattr(msg, msg.media.value, None)
        if media:
            file_name = getattr(media, 'file_name', '')
            file_size = get_size(getattr(media, 'file_size', 0))
    return caption.format(filename=file_name, size=file_size, caption=fcaption_text)

def parse_message_input(message):
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

def parse_button_markup(button_str):
    if not button_str: return None
    btns = []
    try:
        for line in button_str.split('\n'):
            row = []
            for btn in line.split(','):
                parts = btn.split('|')
                if len(parts) == 2:
                    row.append(InlineKeyboardButton(parts[0].strip(), url=parts[1].strip()))
            if row: btns.append(row)
        return InlineKeyboardMarkup(btns) if btns else None
    except: return None

# ==========================================
# DATABASE HELPERS FOR RESTRICTED SETTINGS
# ==========================================
async def get_restr_configs(user_id):
    """Fetch specialized configurations for restricted forwarding. Completely isolated."""
    user = await db.col.find_one({'id': int(user_id)})
    default = {
        'file_size': 0, 
        'caption': None,
        'delay': 2.0,
        'preserve_group': False,
        'force_group': False,
        'filters': [], # List of types to EXCLUDE
        'thumbnail': None,
        'button': None,
        'protect': False
    }
    if user and 'restr_configs' in user:
        default.update(user['restr_configs'])
    return default

async def update_restr_configs(user_id, key, value):
    configs = await get_restr_configs(user_id)
    configs[key] = value
    await db.col.update_one({'id': int(user_id)}, {'$set': {'restr_configs': configs}}, upsert=True)

# ==========================================
# /restrsettings - FULL SETTINGS MENU
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
    preserve = configs.get('preserve_group', False)
    force = configs.get('force_group', False)
    protect = configs.get('protect', False)
    
    text = (
        "<b>🛡 Restricted Forwarding Settings</b>\n\n"
        f"<b>Max File Size:</b> <code>{display_size}</code>\n"
        f"<b>Forward Delay:</b> <code>{delay} seconds</code>\n"
        f"<b>Protect Content:</b> {'✅ ON' if protect else '❌ OFF'}\n"
        f"<b>Preserve Original Grouping:</b> {'✅ ON' if preserve else '❌ OFF'}\n"
        f"<b>Always Force Groups of 10:</b> {'✅ ON' if force else '❌ OFF'}\n\n"
        "<i>Note: These settings are completely independent of your normal /settings.</i>"
    )
    
    buttons = [
        [InlineKeyboardButton("⚙️ Media Filters", callback_data="restr_menu_filters"),
         InlineKeyboardButton("🖼 Custom Thumbnail", callback_data="restr_set_thumbnail")],
        [InlineKeyboardButton("📝 Custom Caption", callback_data="restr_set_caption"),
         InlineKeyboardButton("🔗 Custom Button", callback_data="restr_set_button")],
        [InlineKeyboardButton(f"Protect Content: {'✅' if protect else '❌'}", callback_data="restr_toggle_protect")],
        [InlineKeyboardButton(f"Preserve Groups: {'✅' if preserve else '❌'}", callback_data="restr_toggle_preserve_group")],
        [InlineKeyboardButton(f"Force Group (10): {'✅' if force else '❌'}", callback_data="restr_toggle_force_group")],
        [InlineKeyboardButton(f"Max Size: {display_size}", callback_data="restr_set_file_size"),
         InlineKeyboardButton(f"Delay: {delay}s", callback_data="restr_set_delay")],
        [InlineKeyboardButton("Close", callback_data="close_btn")]
    ]
    await edit_or_reply(message, text, reply_markup=InlineKeyboardMarkup(buttons))

@Client.on_callback_query(filters.regex(r'^restr_menu_filters$'))
async def restr_filters_menu(bot, query):
    configs = await get_restr_configs(query.from_user.id)
    active = configs.get('filters', [])
    types = ['text', 'photo', 'video', 'document', 'audio', 'voice', 'animation']
    
    btns = []
    for t in types:
        # If it is in "active" list, it means it is EXCLUDED
        status = "❌" if t in active else "✅"
        btns.append(InlineKeyboardButton(f"{t.capitalize()}: {status}", callback_data=f"restr_filter_{t}"))
        
    grid = [btns[i:i+2] for i in range(0, len(btns), 2)]
    grid.append([InlineKeyboardButton("🔙 Back to Restricted Settings", callback_data="restr_settings_back")])
    
    await edit_or_reply(
        query.message, 
        "<b>⚙️ Restricted Media Filters</b>\n\n✅ = Will be forwarded\n❌ = Will be skipped", 
        reply_markup=InlineKeyboardMarkup(grid)
    )

@Client.on_callback_query(filters.regex(r'^restr_filter_(.*)$'))
async def restr_filter_toggle(bot, query):
    user_id = query.from_user.id
    t = query.matches[0].group(1)
    configs = await get_restr_configs(user_id)
    active = configs.get('filters', [])
    
    if t in active: active.remove(t)
    else: active.append(t)
        
    await update_restr_configs(user_id, 'filters', active)
    await restr_filters_menu(bot, query)

@Client.on_callback_query(filters.regex(r'^restr_toggle_'))
async def restr_toggle_callback(bot, query):
    user_id = query.from_user.id
    setting_key = query.data.split('restr_toggle_')[1]
    configs = await get_restr_configs(user_id)
    
    if setting_key == "preserve_group":
        new_val = not configs.get('preserve_group', False)
        await update_restr_configs(user_id, 'preserve_group', new_val)
        if new_val: await update_restr_configs(user_id, 'force_group', False)
            
    elif setting_key == "force_group":
        new_val = not configs.get('force_group', False)
        await update_restr_configs(user_id, 'force_group', new_val)
        if new_val: await update_restr_configs(user_id, 'preserve_group', False)
            
    elif setting_key == "protect":
        new_val = not configs.get('protect', False)
        await update_restr_configs(user_id, 'protect', new_val)
            
    await show_restr_settings(query.message, user_id)

@Client.on_callback_query(filters.regex(r'^restr_set_'))
async def restr_set_callback(bot, query):
    user_id = query.from_user.id
    setting_key = query.data.split('restr_set_')[1]
    
    prompts = {
        "file_size": "Send max file size in <b>MB</b> (e.g., 50 or 1.5).\nSend `0` for no limit.",
        "caption": "Send custom caption.\nPlaceholders: `{filename}`, `{size}`, `{caption}`.\nSend `/reset` to remove.",
        "delay": "Send delay in seconds (e.g. 2.5).",
        "button": "Send custom button markup.\nFormat: `Button Name | http://url.com`\nMultiple: `Btn1 | url1, Btn2 | url2`\nSend `/reset` to remove.",
        "thumbnail": "Send a Photo to set as the custom thumbnail.\nSend `/reset` to remove."
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
    if not current_state.startswith("awaiting_restr_"): return

    if message.text and message.text.lower() == "/cancel":
        temp.USER_STATES.pop(user_id, None)
        await message.reply("Cancelled.")
        message.stop_propagation()

    setting_key = current_state.replace("awaiting_restr_", "")
    value = None
    
    if setting_key == "source":
        await handle_restr_source(bot, message, user_id, state_info)
        message.stop_propagation()
        return
        
    try: await message.delete()
    except: pass
    
    if message.text and message.text.lower() == "/reset":
        value = None
    elif setting_key == "file_size":
        try: value = float(message.text) * 1024 * 1024
        except: 
            await bot.send_message(user_id, "❌ Invalid number.")
            message.stop_propagation(); return
    elif setting_key == "delay":
        try: value = float(message.text)
        except: 
            await bot.send_message(user_id, "❌ Invalid number.")
            message.stop_propagation(); return
    elif setting_key == "caption":
        value = message.text
    elif setting_key == "button":
        value = message.text
    elif setting_key == "thumbnail":
        if message.photo: value = message.photo.file_id
        elif message.document and message.document.thumbs: value = message.document.file_id
        else:
            await bot.send_message(user_id, "❌ Please send a valid photo.")
            message.stop_propagation(); return

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
    
    temp.USER_STATES.pop(user_id, None)
    await start_range_selection(bot, status_msg, from_chat_id, from_title, to_chat_id, 1, end_id, final_callback_prefix="restr_final")


# ==========================================
# FINAL CONFIRMATION & TASK EXECUTION
# ==========================================
@Client.on_callback_query(filters.regex(r'^range_confirm_restr_final_'), group=-1)
async def restr_final_confirmation(bot, query):
    session_id = query.data.split('_')[-1]
    session = temp.RANGE_SESSIONS.pop(session_id, None)
    
    if not session: 
        await query.answer("Session expired.", show_alert=True)
        raise StopPropagation
    
    user_id = query.from_user.id
    task_id = str(uuid4())
    
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
        "<i>Note: Uses your /restrsettings completely independent of global settings.</i>"
    )
    
    await query.message.edit_text(text, reply_markup=markup)
    raise StopPropagation

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
    restr_configs = await get_restr_configs(user_id)
    
    status_msg = await query.message.edit_text("`Initializing Restricted Runner...`")
    asyncio.create_task(restricted_worker(bot, user_id, task_id, _bot_data, restr_configs, status_msg, sts))


async def restricted_worker(bot, user_id, task_id, bot_data, restr_configs, message_obj, sts):
    i = sts.get(full=True)
    client_instance = None
    
    start_id = min(i.start_id, i.end_id)
    end_id = max(i.start_id, i.end_id)
    
    # 1. Gather STRICTLY ISOLATED Configs
    delay = restr_configs.get('delay', 2.0)
    size_limit = restr_configs.get('file_size', 0)
    final_caption = restr_configs.get('caption')
    preserve_group = restr_configs.get('preserve_group', False)
    force_group = restr_configs.get('force_group', False)
    protect = restr_configs.get('protect', False)
    filters_to_apply = restr_configs.get('filters', [])
    thumb_id = restr_configs.get('thumbnail')
    
    button_raw = restr_configs.get('button')
    button = parse_button_markup(button_raw) if button_raw else None
    
    thumb_path = None
    
    temp.ACTIVE_TASKS[user_id] = {task_id: {"process": message_obj, "details": {"type": "Restricted Forwarding", "from": str(i.FROM), "to": str(i.TO)}}}
    last_update = time.time()
    
    buffer = []
    current_mg_id = None
    
    async def flush_buffer():
        nonlocal buffer, current_mg_id
        if not buffer: return
        
        success = False
        attempts = 0
        while attempts < 3 and not success:
            try:
                if len(buffer) == 1:
                    item = buffer[0]
                    fp, m, c = item['file_path'], item['msg'], item['caption']
                    
                    thumb_file = open(thumb_path, 'rb') if thumb_path else None
                    send_args = {
                        "chat_id": i.TO,
                        "caption": c,
                        "protect_content": protect
                    }
                    if button: send_args['reply_markup'] = button
                    
                    try:
                        if m.photo: await client_instance.send_photo(photo=fp, **send_args)
                        elif m.video: 
                            if thumb_file: send_args['thumb'] = thumb_file
                            await client_instance.send_video(video=fp, **send_args)
                        elif m.document:
                            if thumb_file: send_args['thumb'] = thumb_file
                            await client_instance.send_document(document=fp, **send_args)
                        elif m.audio:
                            if thumb_file: send_args['thumb'] = thumb_file
                            await client_instance.send_audio(audio=fp, **send_args)
                        elif m.voice: await client_instance.send_voice(voice=fp, **send_args)
                        elif m.animation:
                            if thumb_file: send_args['thumb'] = thumb_file
                            await client_instance.send_animation(animation=fp, **send_args)
                        else: await client_instance.send_document(document=fp, **send_args)
                    finally:
                        if thumb_file: thumb_file.close()
                else:
                    media_group = []
                    for item in buffer:
                        fp, m, c = item['file_path'], item['msg'], item['caption']
                        if m.photo: media_group.append(InputMediaPhoto(fp, caption=c))
                        elif m.video: media_group.append(InputMediaVideo(fp, caption=c))
                        elif m.document: media_group.append(InputMediaDocument(fp, caption=c))
                        elif m.audio: media_group.append(InputMediaAudio(fp, caption=c))
                        else: media_group.append(InputMediaDocument(fp, caption=c))
                        
                    # Telegram restriction: no buttons allowed on media groups
                    await client_instance.send_media_group(chat_id=i.TO, media=media_group, protect_content=protect)
                
                sts.add('total_files', len(buffer))
                success = True
            except FloodWait as e:
                await asyncio.sleep(e.value + 1)
                attempts += 1
            except Exception as e:
                logger.error(f"Error flushing buffer: {e}")
                sts.add('failed', len(buffer))
                break 
                
        if not success and attempts >= 3:
            sts.add('failed', len(buffer))
            
        # Cleanup files from disk
        for item in buffer:
            if os.path.exists(item['file_path']):
                try: os.remove(item['file_path'])
                except: pass
        buffer.clear()
        current_mg_id = None
    
    try:
        # Download isolated thumbnail
        if thumb_id:
            try:
                thumb_path = await bot.download_media(thumb_id)
                thumb_path = await format_thumbnail(thumb_path)
            except Exception as e:
                logger.error(f"Failed to prepare thumbnail: {e}")

        client_instance = await start_clone_bot(PyClient().client(bot_data), bot_data)
        
        for msg_id in range(start_id, end_id + 1):
            if temp.CANCEL.get(task_id): break
            
            if time.time() - last_update > 5:
                await edit_restr_progress(message_obj, sts, "Running")
                last_update = time.time()
            
            try:
                messages = await client_instance.get_messages(i.FROM, [msg_id])
                if not messages:
                    sts.add('failed'); sts.add('fetched')
                    continue
                    
                msg = messages[0]
                if msg.empty or msg.service:
                    sts.add('filtered'); sts.add('fetched')
                    continue
                
                sts.add('fetched')
                
                # ISOLATED Filter Check
                msg_type_str = str(msg.media.value) if msg.media else "text"
                if msg_type_str in filters_to_apply:
                    sts.add('filtered')
                    continue
                
                capt = custom_caption(msg, final_caption)
                
                # --- Text Message ---
                if not msg.media and msg.text:
                    await flush_buffer()
                    success_txt = False
                    for _ in range(3):
                        try:
                            await client_instance.send_message(
                                i.TO, msg.text.html, parse_mode=enums.ParseMode.HTML,
                                reply_markup=button, protect_content=protect, disable_web_page_preview=True
                            )
                            sts.add('total_files')
                            success_txt = True
                            await asyncio.sleep(delay)
                            break
                        except FloodWait as e:
                            await asyncio.sleep(e.value + 1)
                        except Exception as e:
                            logger.error(f"Error text sending: {e}")
                            break
                    if not success_txt: sts.add('failed')
                    continue
                
                # --- Media Message ---
                if msg.media:
                    media_obj = getattr(msg, msg.media.value, None)
                    if media_obj:
                        f_size = getattr(media_obj, 'file_size', 0)
                        if size_limit > 0 and f_size > size_limit:
                            sts.add('filtered')
                            continue
                    
                    file_path = await client_instance.download_media(msg)
                    if not file_path:
                        sts.add('failed')
                        continue
                        
                    is_groupable = bool(msg.photo or msg.video or msg.document or msg.audio)
                    
                    if not is_groupable:
                        await flush_buffer()
                        buffer.append({'file_path': file_path, 'msg': msg, 'caption': capt})
                        await flush_buffer()
                        await asyncio.sleep(delay)
                        continue
                    
                    if preserve_group:
                        if msg.media_group_id:
                            if current_mg_id and current_mg_id != msg.media_group_id:
                                await flush_buffer()
                                await asyncio.sleep(delay)
                            current_mg_id = msg.media_group_id
                            buffer.append({'file_path': file_path, 'msg': msg, 'caption': capt})
                            if len(buffer) == 10:
                                await flush_buffer()
                                await asyncio.sleep(delay)
                        else:
                            await flush_buffer()
                            buffer.append({'file_path': file_path, 'msg': msg, 'caption': capt})
                            await flush_buffer()
                            await asyncio.sleep(delay)
                            
                    elif force_group:
                        buffer.append({'file_path': file_path, 'msg': msg, 'caption': capt})
                        if len(buffer) == 10:
                            await flush_buffer()
                            await asyncio.sleep(delay)
                            
                    else: # No grouping
                        await flush_buffer()
                        buffer.append({'file_path': file_path, 'msg': msg, 'caption': capt})
                        await flush_buffer()
                        await asyncio.sleep(delay)
                    
            except Exception as e:
                logger.error(f"Restricted forward error on {msg_id}: {e}")
                sts.add('failed')
                
        await flush_buffer()
                    
    except Exception as e:
        await edit_or_reply(message_obj, f"❌ **Fatal Error:** `{e}`")
    finally:
        if thumb_path and os.path.exists(thumb_path):
            try: os.remove(thumb_path)
            except: pass
            
        final_status = "Cancelled" if temp.CANCEL.get(task_id) else "Completed"
        await edit_restr_progress(message_obj, sts, final_status)
        
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
