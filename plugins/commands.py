import os
import sys
import asyncio 
import random
from database import db, mongodb_version
from config import Config, temp
from platform import python_version
from translation import Translation
from pyrogram import Client, filters, enums, __version__ as pyrogram_version
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, InputMediaDocument, Message, CallbackQuery
from .test import update_configs, CLIENT

SYD = ["https://files.catbox.moe/3lwlbm.png"]

main_buttons = [[
        InlineKeyboardButton('Help', callback_data='help'),
        InlineKeyboardButton('About', callback_data='about')
]]



#===================Start Function===================#

@Client.on_message(filters.private & filters.command(['start']))
async def start(client, message):
    user = message.from_user
    try:
        if not await db.is_user_exist(user.id):
            await db.add_user(user.id, user.first_name)
    except Exception as e:
        print(f"Error in user registration: {e}")

    reply_markup = InlineKeyboardMarkup(main_buttons)
    text=Translation.START_TXT.format(user.mention)
    await message.reply_photo(
        photo=random.choice(SYD),
        caption=text,
        reply_markup=reply_markup
    )


#===================Reset Me Function===================#

@Client.on_message(filters.private & filters.command(['resetme']))
async def reset_user(client, message):
    user_id = message.from_user.id
    
    # Confirmation prompt
    await message.reply_text(
        "**This will delete all saved bots, userbots, and channel configurations.**\n\nThis action cannot be undone. Are you sure?",
        reply_markup=InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("✓ Yes, I am sure", callback_data="confirm_reset")],
                [InlineKeyboardButton("« Cancel", callback_data="close_btn")]
            ]
        )
    )

@Client.on_callback_query(filters.regex(r'^confirm_reset'))
async def confirm_reset_callback(bot, query):
    user_id = query.from_user.id
    try:
        await db.reset_user_data(user_id)
        await query.message.edit_text("✓ **Account has been reset.**\n\nYour bots, userbots, channels, and custom settings have been cleared.\n\nUse /start to begin again.")
    except Exception as e:
        await query.message.edit_text(f"An error occurred during reset: `{e}`")


#==================Restart Function==================#

@Client.on_message(filters.private & filters.command(['restart', "r"]) & filters.user(Config.OWNER_ID))
async def restart(client, message):
    msg = await message.reply_text(
        text="<i>Restarting...</i>",
        quote=True
    )
    await asyncio.sleep(3)
    await msg.edit("<i>Restarted.</i>")
    os.execl(sys.executable, sys.executable, *sys.argv)
    
@Client.on_message(filters.command("start") & filters.chat(-1002687879857))
async def sydstart(client, message):
    await message.reply_text(".")

#==================Callback Functions==================#

@Client.on_callback_query(filters.regex(r'^help'))
async def helpcb(bot, query):
    await query.message.edit_text(
        text=Translation.HELP_TXT,
        reply_markup=InlineKeyboardMarkup(
            [[
            InlineKeyboardButton('How to Use', callback_data='how_to_use')
            ],[
            InlineKeyboardButton('Settings', callback_data='settings#main'),
            InlineKeyboardButton('Stats', callback_data='status')
            ],[
            InlineKeyboardButton('Active Tasks', callback_data='active_tasks_cmd'),
            InlineKeyboardButton('« Back', callback_data='back')
            ]]
        ))

@Client.on_message(filters.private & filters.command(["forwardelay", "fd"]))
async def forward_delay(client, message):
    user_id = message.from_user.id
    
    ban_status = await db.get_ban_status(user_id)
    if ban_status["is_banned"]:
        return await message.reply_text(f"Access denied.\n\nReason: {ban_status['ban_reason']}")

    # Get current configs first to display the existing value
    user_configs = await db.get_configs(user_id)
    current_delay = user_configs.get('forward_delay', 0.5)

    if len(message.command) < 2:
        # If no new value is provided, show the help text with the current delay
        return await message.reply_text(Translation.FORWARDELAY_TXT.format(current_delay=current_delay))
    
    try:
        delay = float(message.command[1])
        if delay < 0:
            return await message.reply_text("The delay must be a positive number.")
        
        await update_configs(user_id, 'forward_delay', delay)
        await message.reply_text(f"✅ Forwarding delay has been updated to **{delay} seconds**.")
    except ValueError:
        await message.reply_text("Invalid input. Please provide a number (e.g., `0.5`, `1`, `2`).")

# --- New ubclist command ---
@Client.on_message(filters.private & filters.command("ubclist"))
async def ubclist_command(bot: Client, message: Message):
    user_id = message.from_user.id
    userbots = [b for b in await db.get_bots(user_id) if not b.get('is_bot')]
    
    if not userbots:
        return await message.reply_text("You haven't added any userbots. Please add one in /settings.")

    if len(userbots) > 1:
        buttons = [[InlineKeyboardButton(ub['name'], callback_data=f"ubclist_select_{ub['id']}")] for ub in userbots]
        buttons.append([InlineKeyboardButton("« Cancel", callback_data="close_btn")])
        await message.reply_text("<b>Select a Userbot to list its chats:</b>", reply_markup=InlineKeyboardMarkup(buttons))
    else:
        await list_userbot_chats(bot, message, user_id, userbots[0]['id'])

@Client.on_callback_query(filters.regex("^ubclist_select_"))
async def cb_select_userbot_ubclist(bot: Client, query: CallbackQuery):
    userbot_id = int(query.data.split('_')[-1])
    await query.message.delete()
    await list_userbot_chats(bot, query.message, query.from_user.id, userbot_id)

async def list_userbot_chats(bot: Client, message: Message, user_id: int, userbot_id: int):
    status_msg = await message.reply_text("`Fetching chats, please wait...`")
    
    userbot_config = await db.get_bot(user_id, userbot_id)
    if not userbot_config:
        return await status_msg.edit("Userbot not found.")

    text, serial = "<b>List of recent chats (up to 50):</b>\n\n", 1
    try:
        async with CLIENT().client(userbot_config) as userbot:
            async for dialog in userbot.get_dialogs(limit=50):
                text += f"<b>{serial}.</b> {dialog.chat.title} (<code>{dialog.chat.id}</code>)\n"
                serial += 1
        await status_msg.edit_text(text, parse_mode=enums.ParseMode.HTML)
    except Exception as e:
        await status_msg.edit(f"An error occurred: `{e}`")


# --- New /tasks command and callbacks ---
@Client.on_message(filters.private & filters.command("tasks"))
async def active_tasks_command(bot, message):
    await active_tasks_cb(bot, message)

@Client.on_callback_query(filters.regex(r'^active_tasks_cmd'))
async def active_tasks_cb(bot, query_or_message):
    is_message = isinstance(query_or_message, Message)
    user_id = query_or_message.from_user.id
    message = query_or_message if is_message else query_or_message.message
    
    tasks = temp.ACTIVE_TASKS.get(user_id, {})
    
    if not tasks:
        text = "You have no active tasks."
        markup = InlineKeyboardMarkup([[InlineKeyboardButton('« Back', callback_data='help')]]) if not is_message else None
        if is_message:
            return await message.reply_text(text)
        else:
            return await message.edit_text(text, reply_markup=markup)

    text = "<b>Your Active Tasks:</b>\n\n"
    buttons = []
    for task_id, task_data in tasks.items():
        details = task_data.get("details", {})
        task_type = details.get("type", "Unknown Task")
        from_chat = details.get("from", "N/A")
        to_chat = details.get("to", "N/A")
        
        text += f"<b>Task ID:</b> <code>{task_id[:8]}...</code>\n"
        text += f"  - <b>Type:</b> {task_type}\n"
        text += f"  - <b>From:</b> {from_chat}\n"
        if to_chat != "N/A":
            text += f"  - <b>To:</b> {to_chat}\n\n"
        
        buttons.append([InlineKeyboardButton(f"❌ Cancel Task: {task_id[:8]}...", callback_data=f"cancel_task_{task_id}")])
    
    if not is_message:
        buttons.append([InlineKeyboardButton('« Back', callback_data='help')])
    
    if is_message:
        await message.reply_text(text, reply_markup=InlineKeyboardMarkup(buttons))
    else:
        await message.edit_text(text, reply_markup=InlineKeyboardMarkup(buttons))


# --- New Cancellation Workflow ---

@Client.on_callback_query(filters.regex(r'^cancel_task_'))
async def cancel_task_confirmation_cb(bot, query):
    """Asks the user to confirm the cancellation."""
    user_id = query.from_user.id
    task_id = query.data.split("_", 2)[2]
    
    if not temp.ACTIVE_TASKS.get(user_id, {}).get(task_id):
        await query.answer("This task is no longer active.", show_alert=True)
        return await active_tasks_cb(bot, query)

    await query.message.edit_text(
        f"<b>Are you sure you want to cancel this task?</b>\n\nTask ID: <code>{task_id[:8]}...</code>",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✓ Yes, cancel it", callback_data=f"confirm_cancel_{task_id}")],
            [InlineKeyboardButton("« No, go back", callback_data="active_tasks_cmd")]
        ])
    )

@Client.on_callback_query(filters.regex(r'^confirm_cancel_'))
async def confirm_cancel_task_cb(bot, query):
    """Sets the cancellation flag and provides immediate feedback."""
    user_id = query.from_user.id
    task_id = query.data.split("_", 2)[2]

    if temp.ACTIVE_TASKS.get(user_id, {}).get(task_id):
        temp.CANCEL[task_id] = True
        await query.message.edit_text(
            f"✅ **Cancellation signal sent for task <code>{task_id[:8]}...</code>**\n\nThe process will stop shortly."
        )
    else:
        await query.answer("This task was already completed or cancelled.", show_alert=True)
        await query.message.delete()

# --- End New Cancellation Workflow ---


@Client.on_callback_query(filters.regex(r'^how_to_use'))
async def how_to_use(bot, query):
    await query.message.edit_text(
        text=Translation.HOW_USE_TXT,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('« Back', callback_data='help')]]),
        disable_web_page_preview=True
    )



@Client.on_callback_query(filters.regex(r'^back'))
async def back(bot, query):
    reply_markup = InlineKeyboardMarkup(main_buttons)
    await query.message.edit_text(
       reply_markup=reply_markup,
       text=Translation.START_TXT.format(
                query.from_user.first_name))



@Client.on_callback_query(filters.regex(r'^about'))
async def about(bot, query):
    await query.message.edit_text(
        text=Translation.ABOUT_TXT.format(bot.me.mention),
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('« Back', callback_data='back')]]),
        disable_web_page_preview=True,
        parse_mode=enums.ParseMode.HTML,
    )



@Client.on_callback_query(filters.regex(r'^status'))
async def status(bot, query):
    users_count, bots_count = await db.total_users_bots_count()
    await query.message.edit_text(
        text=Translation.STATUS_TXT.format(users_count, bots_count, temp.forwardings),
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('« Back', callback_data='help')]]),
        parse_mode=enums.ParseMode.HTML,
        disable_web_page_preview=True,
    )
