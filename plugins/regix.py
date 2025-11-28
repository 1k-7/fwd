# mistaldrin/fwd/fwd-DawnUltra/plugins/regix.py

import re
import asyncio
import logging
import math
import time
from .utils import STS
from database import db
from .test import CLIENT, start_clone_bot
from config import Config, temp
from translation import Translation
from pyrogram import Client, filters
from pyrogram.enums import ParseMode
from pyrogram.errors import FloodWait, MessageNotModified, RPCError, MediaEmpty, UserIsBlocked, PeerIdInvalid
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, CallbackQuery, Message

CLIENT = CLIENT()
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

async def run_forwarding_task(bot, user_id, frwd_id, bot_id, sts, message_obj):
    """The core logic for processing and forwarding messages."""
    i = sts.get(full=True)
    
    temp.lock[user_id] = True
    temp.forwardings += 1
    
    client_instance = None
    try:
        _bot_data, caption, forward_tag, data_params, protect, button = await sts.get_data(user_id, bot_id=bot_id)
        if not _bot_data:
            return await msg_edit(message_obj, "Bot/userbot used for this task is no longer available.", wait=True)

        delay = data_params.get('forward_delay', 0.5)
        filters_to_apply = data_params.get('filters', [])

        await msg_edit(message_obj, "Starting client...")
        client_instance = await start_clone_bot(CLIENT.client(_bot_data), _bot_data)
        
        await msg_edit(message_obj, "Accessing channels...")

        async def get_chat_safe(chat_id):
            try:
                return await client_instance.get_chat(chat_id)
            except PeerIdInvalid:
                logger.info(f"Peer {chat_id} not found in cache. Scanning dialogs...")
                async for dialog in client_instance.get_dialogs(limit=500):
                    if dialog.chat.id == chat_id:
                        return dialog.chat
                raise ValueError(f"Peer {chat_id} not found in recent dialogs. Please interact with it first.")
            except Exception as e:
                # Retry if username
                 if isinstance(chat_id, str):
                     try: return await client_instance.get_chat(chat_id)
                     except: pass
                 raise e

        try:
            from_chat_details = await get_chat_safe(i.FROM)
            to_chat_details = await get_chat_safe(i.TO)
        except Exception as e:
            logger.error(f"Failed to resolve peers for task {frwd_id}: {e}")
            await msg_edit(message_obj, f"<b>Connection Error:</b>\n`{e}`\n\nTask stopped.")
            await stop(client_instance, user_id, frwd_id)
            return
        
        from_title = from_chat_details.title or f"{from_chat_details.first_name} {from_chat_details.last_name or ''}".strip()
        to_title = to_chat_details.title or f"{to_chat_details.first_name} {to_chat_details.last_name or ''}".strip()

        if user_id not in temp.ACTIVE_TASKS: temp.ACTIVE_TASKS[user_id] = {}
        temp.ACTIVE_TASKS[user_id][frwd_id] = {"process": message_obj, "details": {"type": "Forwarding", "from": from_title, "to": to_title}}
        
        final_status = "error"
        forward_batch = []
        last_update_time = time.time()

        await edit_progress(message_obj, sts, "running")
        
        start_id = min(i.start_id, i.end_id)
        end_id = max(i.start_id, i.end_id)
        
        # --- MODE SELECTION ---
        task_mode = sts.data[frwd_id].get('mode', 'standard')
        message_ids_to_process = []
        
        if task_mode == "id_scan":
             await msg_edit(message_obj, "Scanning chat history for valid messages... (This may take a moment)")
             try:
                 # Fetch ALL history IDs to ensure we only target this chat and handle sparse global IDs
                 valid_ids = []
                 async for m in client_instance.get_chat_history(i.FROM):
                     if start_id <= m.id <= end_id:
                         valid_ids.append(m.id)
                 
                 # Sort Oldest -> Newest
                 valid_ids.sort()
                 message_ids_to_process = valid_ids
                 sts.data[frwd_id]['total'] = len(valid_ids) # Update total to accurate count
                 # Reset fetched to 0 relative to this new list?
                 # Actually STS fetches tracks *processed*, so if we resume, we might need logic.
                 # For now, simplistic resume: filter out already processed?
                 # The 'fetched' in STS is count. We need index.
                 # If we are resuming 'id_scan', we might re-scan.
                 # Let's just process from 'fetched' index.
                 message_ids_to_process = message_ids_to_process[i.fetched:]
             except Exception as e:
                 logger.error(f"Error scanning history: {e}")
                 await msg_edit(message_obj, f"Error scanning history: {e}")
                 return
        else:
             # Standard range mode
             # We generate chunks in the loop, but for consistency let's define the generator logic below
             pass 

        # --- PROCESSING LOOP ---
        
        # Helper to yield chunks
        def chunk_generator():
            if task_mode == "id_scan":
                for k in range(0, len(message_ids_to_process), 200):
                    yield message_ids_to_process[k : k + 200]
            else:
                 current_id_to_process = start_id + i.fetched
                 for k in range(current_id_to_process, end_id + 1, 200):
                     yield list(range(k, min(k + 200, end_id + 1)))

        for chunk in chunk_generator():
            if temp.CANCEL.get(frwd_id):
                final_status = "cancelled"
                break
            
            if not chunk: continue

            try:
                messages = await client_instance.get_messages(i.FROM, chunk)
            except Exception as e_fetch:
                logger.error(f"Could not fetch message chunk: {e_fetch}")
                sts.add('failed', len(chunk)); sts.add('fetched', len(chunk))
                continue

            for message in messages:
                if time.time() - last_update_time > 15:
                    await edit_progress(message_obj, sts, "running")
                    last_update_time = time.time()
                
                sts.add('fetched')
                
                # --- CRITICAL FIX: Cross-Chat Forwarding Prevention ---
                # Verify that the message actually belongs to the source chat.
                # Use str(id) comparison for robustness against some int/str nuances, though normally int.
                if message and message.chat and message.chat.id != from_chat_details.id:
                     # Skip messages from other chats (Ghost/Global ID artifact)
                     continue 
                # ------------------------------------------------------

                if not message or message.empty or message.service or (message.media and str(message.media.value) in filters_to_apply) or (not message.media and "text" in filters_to_apply):
                    sts.add('filtered'); continue

                try:
                    if forward_tag:
                        forward_batch.append(message.id)
                        if len(forward_batch) >= 100:
                            await client_instance.forward_messages(chat_id=i.TO, from_chat_id=i.FROM, message_ids=forward_batch, protect_content=protect)
                            sts.add('total_files', len(forward_batch)); forward_batch.clear()
                            await asyncio.sleep(max(delay, 2))
                    else:
                        await message.copy(chat_id=i.TO, caption=custom_caption(message, caption), reply_markup=button, protect_content=protect)
                        sts.add('total_files')
                except FloodWait as e:
                    await asyncio.sleep(e.value + 2)
                    sts.add('failed')
                except Exception as e:
                    logger.error(f"Failed to process message {message.id}: {e}", exc_info=False)
                    sts.add('failed')

                if not forward_tag: await asyncio.sleep(delay)
            
            # Save progress
            await db.save_task(frwd_id, {'fetched': sts.get('fetched'), 'mode': task_mode})

        if forward_tag and forward_batch and not temp.CANCEL.get(frwd_id):
            await client_instance.forward_messages(chat_id=i.TO, from_chat_id=i.FROM, message_ids=forward_batch, protect_content=protect)
            sts.add('total_files', len(forward_batch))

        if not temp.CANCEL.get(frwd_id): final_status = "completed"

    except Exception as e:
        logger.error(f"Error during forwarding task {frwd_id}: {e}", exc_info=True)
        final_status = "error"
        await msg_edit(message_obj, f"An error occurred: `{e}`")

    finally:
        await edit_progress(message_obj, sts, final_status)
        await stop(client_instance, user_id, frwd_id)

@Client.on_callback_query(filters.regex(r'^start_public'))
async def pub_(bot, cb):
    user_id = cb.from_user.id
    if temp.lock.get(user_id):
        return await cb.answer("Please wait for the previous task to complete!", show_alert=True)

    frwd_id = cb.data.split("_")[2]
    temp.CANCEL[frwd_id] = False
    sts = STS(frwd_id)
    if not sts.verify():
        return await cb.answer("This is an old button, please start over.", show_alert=True)
    
    i = sts.get(full=True)
    m = await msg_edit(cb.message, "Verifying...")
    
    bot_id = temp.FORWARD_BOT_ID.get(user_id)
    if not bot_id:
        return await m.edit("Bot selection lost from session. Please restart.")
    
    # Preserve the mode if it was set in STS data
    current_mode = sts.data[frwd_id].get('mode', 'standard')

    task_details = {
        'id': frwd_id, 'user_id': user_id, 'bot_id': bot_id,
        'from_chat': i.FROM, 'to_chat': i.TO,
        'start_id': i.start_id, 'end_id': i.end_id, 'fetched': 0,
        'mode': current_mode
    }
    await db.save_task(frwd_id, task_details)
    await run_forwarding_task(bot, user_id, frwd_id, bot_id, sts, m)

async def resume_forwarding(bot, task_data):
    user_id = task_data['user_id']
    frwd_id = task_data['id']
    bot_id = task_data['bot_id']
    
    sts = STS(frwd_id).store(
        From=task_data['from_chat'], to=task_data['to_chat'],
        start_id=task_data['start_id'], end_id=task_data['end_id']
    )
    sts.data[frwd_id]['fetched'] = task_data.get('fetched', 0)
    sts.data[frwd_id]['mode'] = task_data.get('mode', 'standard')
    
    try:
        m = await bot.send_message(user_id, f"🔄 Resuming task from `{task_data['from_chat']}`...")
    except (UserIsBlocked, PeerIdInvalid):
        logger.warning(f"User {user_id} has blocked the bot or chat is inaccessible. Deleting task {frwd_id}.")
        await db.delete_task(frwd_id)
        return

    await run_forwarding_task(bot, user_id, frwd_id, bot_id, sts, m)

@Client.on_callback_query(filters.regex(r'^frwd_status_'))
async def get_frwd_status(bot, query):
    task_id = query.data.split("_", 2)[2]
    sts = STS(task_id)
    if not sts.verify(): return await query.answer("This task has completed or been cancelled.", show_alert=True)
    i = sts.get(full=True)
    diff = time.time() - i.start
    if diff == 0: diff = 1
    speed = i.fetched / diff
    eta = sts.get_readable_time(int((i.total - i.fetched) / speed if speed > 0 else 0))
    percentage = "{:.2f}".format(i.fetched * 100 / i.total if i.total > 0 else 0.00)
    await query.answer(
        Translation.STATUS_ALERT.format(
            status=i.status, fetched=i.fetched, total=i.total, forwarded=i.total_files,
            failed=i.failed, remaining=(i.total - i.fetched), skipped=i.deleted + i.duplicate + i.filtered,
            percentage=percentage, eta=eta), show_alert=True)

async def msg_edit(msg, text, button=None, wait=None):
    try:
        return await msg.edit(text, reply_markup=button, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
    except MessageNotModified: return msg
    except FloodWait as e:
        if wait: await asyncio.sleep(e.value); return await msg_edit(msg, text, button, wait)
    except Exception: return msg

async def edit_progress(msg, sts, status):
    i = sts.get(full=True); sts.set_status(status)
    button = None
    if status not in ["cancelled", "completed", "error"]:
        diff = time.time() - i.start
        if diff == 0: diff = 1
        eta = sts.get_readable_time(int((i.total - i.fetched) / (i.fetched / diff) if (i.fetched / diff) > 0 else 0))
        percentage = "{:.2f}".format(i.fetched * 100 / i.total if i.total > 0 else 0.00)
        progress_bar = "▰{0}▱{1}".format('▰' * math.floor(float(percentage) / 10), '▱' * (10 - math.floor(float(percentage) / 10)))
        text = Translation.TEXT.format(
            status=status, fetched=i.fetched, total=i.total, forwarded=i.total_files,
            failed=i.failed, skipped=i.deleted + i.filtered, duplicates=i.duplicate,
            percentage=percentage, eta=eta, progress_bar=progress_bar)
        button = InlineKeyboardMarkup([[InlineKeyboardButton(f"📊 Status: {percentage}%", callback_data=f'frwd_status_{i.id}')], [InlineKeyboardButton('❌ Cancel ❌', f'cancel_task_{i.id}')]])
    else:
        end_time = time.time(); time_taken = sts.get_readable_time(int(end_time - i.start))
        total_skipped = i.deleted + i.duplicate + i.filtered
        title = "✅ <b>Forwarding Complete</b>" if status == "completed" else "❌ <b>Task Cancelled or Errored</b>"
        line = "━━━━━━━━━━━━━━━━━━━━"
        text = (f"{title}\n{line}\n"
                f"<b>Time Taken:</b> <code>{time_taken}</code>\n\n"
                f"<b><u>Statistics</u></b>:\n"
                f"  Processed: <code>{i.fetched}</code>\n"
                f"  Forwarded: <code>{i.total_files}</code>\n"
                f"  Skipped:   <code>{total_skipped}</code>\n"
                f"  Failed:    <code>{i.failed}</code>")
        button = InlineKeyboardMarkup([[InlineKeyboardButton("Done!", callback_data="close_btn")]])
    await msg_edit(msg, text, button)

async def stop(client, user_id, task_id):
    if client:
        try: await client.stop() 
        except: pass
    if temp.ACTIVE_TASKS.get(user_id, {}).get(task_id): del temp.ACTIVE_TASKS[user_id][task_id]
    temp.CANCEL.pop(task_id, None)
    await db.delete_task(task_id)
    if temp.forwardings > 0: temp.forwardings -= 1
    temp.lock.pop(user_id, None)

def custom_caption(msg, caption):
    if not msg: return ""
    fcaption_text = msg.text.html if msg.text else (msg.caption.html if msg.caption else "")
    if not caption: return fcaption_text
    file_name, file_size = "", "0 B"
    if msg.media:
        media = getattr(msg, msg.media.value, None)
        if media: file_name = getattr(media, 'file_name', ''); file_size = get_size(getattr(media, 'file_size', 0))
    return caption.format(filename=file_name, size=file_size, caption=fcaption_text)

def get_size(size):
    try:
        if not size: return "0 B"
        units, size = ["B", "KB", "MB", "GB", "TB"], float(size); i = 0
        while size >= 1024.0 and i < len(units) - 1: i += 1; size /= 1024.0
        return f"{size:.2f} {units[i]}"
    except: return "N/A"

def retry_btn(id):
    return InlineKeyboardMarkup([[InlineKeyboardButton('Retry', f"start_public_{id}")]])
