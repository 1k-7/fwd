# plugins/unequify.py
import os
import asyncio
import io
import random
import time
import math
import logging
from uuid import uuid4
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from pyrogram.enums import ChatMemberStatus, ParseMode
from pyrogram.errors import FloodWait, ChannelInvalid, UsernameNotOccupied, UsernameInvalid, PeerIdInvalid, UserAlreadyParticipant, MessageNotModified

from .test import CLIENT
from .utils import start_range_selection, get_readable_time, edit_or_reply
from translation import Translation
from config import temp
import database

logger = logging.getLogger(__name__)

OPTION_LABELS = ["Text", "Photos/Videos", "Audio", "Documents", "Stickers"]
DEFAULT_STATE = "01010"
ZEN = ["https://files.catbox.moe/3lwlbm.png"]


def create_selection_keyboard(selection_state: str, session_id: str) -> InlineKeyboardMarkup:
    """Creates the interactive keyboard for selecting message types."""
    buttons = []
    state_list = list(selection_state)

    for i, label in enumerate(OPTION_LABELS):
        text = f"✓ {label}" if state_list[i] == '1' else label
        callback_data = f"uneq_toggle_{i}_{session_id}"
        buttons.append([InlineKeyboardButton(text, callback_data=callback_data)])

    buttons.append([
        InlineKeyboardButton("✓ Start Scan", callback_data=f"uneq_startscan_{selection_state}_{session_id}"),
        InlineKeyboardButton("« Cancel", callback_data=f"range_cancel_{session_id}")
    ])
    return InlineKeyboardMarkup(buttons)

async def prompt_type_selection(bot, query, session_id):
    """Sends the message with the type selection keyboard."""
    chat_id = query.from_user.id if isinstance(query, CallbackQuery) else query.chat.id
    keyboard = create_selection_keyboard(DEFAULT_STATE, session_id)
    text = "<b>Select Message Types</b>\n\nSelect the types of messages to find duplicates of."
    
    # Try to edit the existing message if possible (modern flow)
    if isinstance(query, CallbackQuery):
        await edit_or_reply(query.message, text, reply_markup=keyboard)
    else:
        await bot.send_photo(chat_id, photo=random.choice(ZEN), caption=text, reply_markup=keyboard)

    if session_id in temp.RANGE_SESSIONS:
        temp.RANGE_SESSIONS[session_id]['selection_state'] = DEFAULT_STATE


@Client.on_message(filters.command("unequify") & filters.private)
async def unequify_start(bot: Client, message: Message):
    user_id = message.from_user.id
    if temp.lock.get(user_id):
        return await message.reply("A task is already in progress. Please wait for it to complete.")

    temp.USER_STATES.pop(user_id, None)
    
    ban_status = await database.db.get_ban_status(user_id)
    if ban_status["is_banned"]:
        return await message.reply_text(f"Access denied.\n\nReason: {ban_status['ban_reason']}")

    userbots = [b for b in await database.db.get_bots(user_id) if not b.get('is_bot')]
    if not userbots:
        return await message.reply_text("Add a userbot to proceed.\n( >⁠.⁠< ) --> /settings")

    command_args = message.command[1:] if len(message.command) > 1 else []
    temp.USER_STATES[user_id] = {"command_args": command_args}

    if len(userbots) > 1:
        buttons = [[InlineKeyboardButton(ub['name'], callback_data=f"uneq_select_ub_{ub['id']}")] for ub in userbots]
        buttons.append([InlineKeyboardButton("« Cancel", callback_data="close_btn")])
        await message.reply_photo(photo=random.choice(ZEN), caption="<b>Select a Userbot</b>", reply_markup=InlineKeyboardMarkup(buttons))
        return

    await process_userbot_selection(bot, message, user_id, userbots[0]['id'])

@Client.on_callback_query(filters.regex("^uneq_select_ub_"))
async def cb_select_userbot_unequify(bot: Client, query: CallbackQuery):
    userbot_id = int(query.data.split('_')[-1])
    await process_userbot_selection(bot, query.message, query.from_user.id, userbot_id)

async def process_userbot_selection(bot: Client, message: Message, user_id: int, userbot_id: int):
    temp.UNEQUIFY_USERBOT_ID[user_id] = userbot_id
    state_info = temp.USER_STATES.get(user_id, {})
    command_args = state_info.get("command_args", [])

    if command_args:
        target = command_args[0]
        temp.USER_STATES.pop(user_id, None)
        await process_unequify_target(bot, message, user_id, userbot_id, target)
    else:
        temp.USER_STATES.pop(user_id, None)
        await unequify_continue(bot, message)

async def process_unequify_target(bot: Client, message: Message, user_id: int, userbot_id: int, target_channel_input: str):
    # Try to edit if message is valid, else send new
    status_msg = await edit_or_reply(message, "`Verifying target channel...`")
    
    try:
        userbot_config = await database.db.get_bot(user_id, userbot_id)
        if not userbot_config: 
            return await status_msg.edit("Selected userbot not found.")

        async with CLIENT().client(userbot_config) as temp_client:
            chat = await temp_client.get_chat(target_channel_input)
            last_msg_id = 0
            async for last_message in temp_client.get_chat_history(chat.id, limit=1):
                last_msg_id = last_message.id
                break
            
            # Pass status_msg to start_range_selection to continue editing the same message
            await start_range_selection(bot, status_msg, from_chat_id=chat.id, from_title=chat.title, to_chat_id=None, start_id=1, end_id=last_msg_id or 1, final_callback_prefix="uneq_final")
    except (UsernameInvalid, PeerIdInvalid, ChannelInvalid, UsernameNotOccupied) as e:
        await status_msg.edit(f"Could not find the chat: `{e}`. Please check the username/ID and ensure your userbot is a member.")
    except Exception as e:
        await status_msg.edit(f"An error occurred: {e}")


async def unequify_continue(bot: Client, message: Message):
    buttons = [
        [InlineKeyboardButton("Manual Input", callback_data="uneq_manual")],
        [InlineKeyboardButton("Select from Userbot Chats", callback_data="uneq_select_from_ub")]
    ]
    # Check if we can edit media (if previous was photo)
    try:
        await message.edit_media(InputMediaPhoto(random.choice(ZEN), caption=Translation.UNEQUIFY_START_TXT), reply_markup=InlineKeyboardMarkup(buttons))
    except:
        await message.reply_photo(photo=random.choice(ZEN), caption=Translation.UNEQUIFY_START_TXT, reply_markup=InlineKeyboardMarkup(buttons))


@Client.on_callback_query(filters.regex("^uneq_"))
async def unequify_callbacks(bot: Client, query: CallbackQuery):
    user_id = query.from_user.id

    if query.data.startswith("uneq_status_"):
        task_id = query.data.split("_", 2)[2]
        task_data = temp.ACTIVE_TASKS.get(query.from_user.id, {}).get(task_id)
        if not task_data:
            return await query.answer("This task has completed or been cancelled.", show_alert=True)
        
        stats = task_data.get("stats", {})
        scanned = stats.get("scanned", 0)
        total = stats.get("total", 0)
        deleted = stats.get("deleted", 0)
        start_time = stats.get("start_time", 0)
        status = stats.get("status", "running")
        
        diff = time.time() - start_time
        if diff == 0: diff = 1
        speed = scanned / diff
        eta = get_readable_time(int((total - scanned) / speed if speed > 0 else 0))
        percentage = "{:.2f}".format(scanned * 100 / total if total > 0 else 0.00)

        await query.answer(
            Translation.UNEQUIFY_STATUS_ALERT.format(
                status=status, scanned=scanned, total=total, deleted=deleted,
                percentage=percentage, eta=eta
            ), show_alert=True)
        return

    data = query.data.split("_", 1)[1]
    
    if data == "manual":
        prompt_message = await edit_or_reply(query.message, "Send the channel username or ID.")
        temp.USER_STATES[user_id] = {
            "state": "awaiting_unequify_manual_target",
            "prompt_message_id": prompt_message.id
        }

    elif data == "select_from_ub":
        userbot_id = temp.UNEQUIFY_USERBOT_ID.get(user_id)
        if not userbot_id: return await edit_or_reply(query.message, "Error: Bot selection lost. Please start over.")
        
        userbot_config = await database.db.get_bot(user_id, userbot_id)
        if not userbot_config: return await edit_or_reply(query.message, "Userbot not found.")
        
        status_msg = await edit_or_reply(query.message, "`⏳ Fetching chats...`")

        chats, serial, text = {}, 1, "Reply with the number or Chat ID of the target channel.\n\n"
        try:
            async with CLIENT().client(userbot_config) as userbot:
                async for dialog in userbot.get_dialogs(limit=50):
                    chats[str(serial)] = dialog.chat
                    chats[str(dialog.chat.id)] = dialog.chat
                    text += f"<b>{serial}.</b> {dialog.chat.title} (<code>{dialog.chat.id}</code>)\n"
                    serial += 1
            
            prompt_message = await status_msg.edit_text(text, parse_mode=ParseMode.HTML)
            temp.USER_STATES[user_id] = { 
                "state": "awaiting_unequify_chat_selection", 
                "chats": chats, 
                "prompt_message": prompt_message 
            }
        except Exception as e:
            await status_msg.edit(f"An error occurred: `{e}`")

    elif data.startswith("toggle_"):
        _, index_str, session_id = data.split("_")
        index = int(index_str)
        session = temp.RANGE_SESSIONS.get(session_id)
        if not session: return

        current_state = session.get('selection_state', DEFAULT_STATE)
        state_list = list(current_state)
        state_list[index] = '1' if state_list[index] == '0' else '0'
        new_state = "".join(state_list)
        session['selection_state'] = new_state

        try:
            await query.message.edit_reply_markup(create_selection_keyboard(new_state, session_id))
        except MessageNotModified: pass

    elif data.startswith("startscan_"):
        _, selection_state, session_id = data.split("_", 2)
        await start_deduplication(bot, query, selection_state, session_id)

async def start_deduplication(bot: Client, callback_query: CallbackQuery, selection_state: str, session_id: str):
    user_id = callback_query.from_user.id
    
    # Reuse the message for status
    status_message = callback_query.message
    await status_message.edit_text("`🚀 Starting deduplication process...`")

    task_id = str(uuid4())
    range_session = temp.RANGE_SESSIONS.pop(session_id, None)
    if not range_session: return await status_message.edit("Error: Session expired or invalid.")

    userbot_id = temp.UNEQUIFY_USERBOT_ID.get(user_id)
    if not userbot_id: return await status_message.edit("Error: Bot selection lost.")

    userbot_config = await database.db.get_bot(user_id, userbot_id)
    if not userbot_config: return await status_message.edit("Error: Userbot not found.")

    target_channel, start_id, end_id = range_session['from_chat_id'], min(range_session['start_id'], range_session['end_id']), max(range_session['start_id'], range_session['end_id'])
    
    if user_id not in temp.ACTIVE_TASKS: temp.ACTIVE_TASKS[user_id] = {}
    temp.ACTIVE_TASKS[user_id][task_id] = { "process": status_message, "details": {"type": "Deduplication", "from": range_session['from_title'], "to": "N/A"}, "stats": {} }
    temp.CANCEL[task_id] = False
    temp.lock[user_id] = True

    seen_identifiers, duplicates_to_delete = set(), []
    total_scanned, total_deleted = 0, 0
    total_in_range = abs(end_id - start_id) + 1
    start_time = time.time()
    last_edit_time = time.time()

    try:
        async with CLIENT().client(userbot_config) as userbot:
            message_ids_to_scan = list(range(start_id, end_id + 1))
            await edit_unequify_progress(status_message, 0, 0, total_in_range, start_time, task_id, "running")

            for i in range(0, len(message_ids_to_scan), 200):
                if temp.CANCEL.get(task_id): break
                
                chunk = message_ids_to_scan[i:i+200]
                messages = await userbot.get_messages(target_channel, chunk)

                for msg in messages:
                    if not msg: continue
                    total_scanned += 1
                    identifier = None
                    
                    if selection_state[0] == '1' and msg.text: identifier = msg.text.strip()
                    elif selection_state[1] == '1' and (msg.photo or msg.video): identifier = (msg.photo or msg.video).file_unique_id
                    elif selection_state[2] == '1' and msg.audio: identifier = msg.audio.file_unique_id
                    elif selection_state[3] == '1' and msg.document: identifier = msg.document.file_unique_id
                    elif selection_state[4] == '1' and msg.sticker: identifier = msg.sticker.file_unique_id

                    if identifier and identifier in seen_identifiers: duplicates_to_delete.append(msg.id)
                    elif identifier: seen_identifiers.add(identifier)

                if len(duplicates_to_delete) >= 100:
                    try:
                        deleted_chunk = await userbot.delete_messages(chat_id=target_channel, message_ids=duplicates_to_delete)
                        total_deleted += deleted_chunk if isinstance(deleted_chunk, int) else len(duplicates_to_delete)
                    except Exception as e:
                        print(f"Error deleting batch: {e}")
                    finally:
                        duplicates_to_delete.clear()
                    await asyncio.sleep(5)

                current_time = time.time()
                if current_time - last_edit_time > 15:
                    await edit_unequify_progress(status_message, total_scanned, total_deleted, total_in_range, start_time, task_id, "running")
                    last_edit_time = current_time

            if duplicates_to_delete and not temp.CANCEL.get(task_id):
                try:
                    deleted_chunk = await userbot.delete_messages(chat_id=target_channel, message_ids=duplicates_to_delete)
                    total_deleted += deleted_chunk if isinstance(deleted_chunk, int) else len(duplicates_to_delete)
                except Exception as e:
                    print(f"Error deleting final batch: {e}")

            final_status = "cancelled" if temp.CANCEL.get(task_id) else "completed"
            await edit_unequify_progress(status_message, total_scanned, total_deleted, total_in_range, start_time, task_id, final_status)

    except Exception as e:
        await status_message.edit(f"❌ **An unexpected error occurred.**\n\n`{e}`")
    finally:
        if temp.ACTIVE_TASKS.get(user_id, {}).get(task_id): del temp.ACTIVE_TASKS[user_id][task_id]
        temp.CANCEL.pop(task_id, None)
        temp.lock.pop(user_id, None)


async def edit_unequify_progress(msg, scanned, deleted, total, start_time, task_id, status):
    if temp.ACTIVE_TASKS.get(msg.chat.id, {}).get(task_id):
        temp.ACTIVE_TASKS[msg.chat.id][task_id]["stats"] = { "scanned": scanned, "deleted": deleted, "total": total, "start_time": start_time, "status": status }
    
    button = None
    if status not in ["cancelled", "completed"]:
        diff = time.time() - start_time
        if diff == 0: diff = 1
        speed = scanned / diff
        eta = get_readable_time(int((total - scanned) / speed if speed > 0 else 0))
        percentage = "{:.2f}".format(scanned * 100 / total if total > 0 else 0.00)
        progress_bar = "▰{0}▱{1}".format('▰' * math.floor(float(percentage) / 10), '▱' * (10 - math.floor(float(percentage) / 10)))

        text = Translation.UNEQUIFY_TEXT.format(
            status=status, scanned=scanned, total=total, deleted=deleted,
            percentage=percentage, eta=eta, progress_bar=progress_bar
        )
        # FIX: Added callback_data= for Cancel button
        button = InlineKeyboardMarkup([[InlineKeyboardButton(f"📊 Status: {percentage}%", callback_data=f'uneq_status_{task_id}')], [InlineKeyboardButton('❌ Cancel ❌', callback_data=f'cancel_task_{task_id}')]])
    else:
        # Modern Completion Card
        icon = "✅" if status == "completed" else "❌"
        title = f"{icon} <b>Deduplication {status.title()}</b>"
        time_taken = get_readable_time(int(time.time() - start_time))
        
        text = (
            f"{title}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"⏱ <b>Time Taken:</b> {time_taken}\n\n"
            f"📊 <b>Statistics</b>\n"
            f"├ 🔍 <b>Scanned:</b> {scanned}\n"
            f"└ 🗑 <b>Deleted:</b> {deleted}"
        )
        button = InlineKeyboardMarkup([[InlineKeyboardButton("Done", callback_data="close_btn")]])
            
    try:
        await msg.edit_text(text, reply_markup=button)
    except MessageNotModified: pass
    except Exception as e: print(f"Error updating unequify progress: {e}")