import asyncio
import random
import logging
from database import db
from config import Config, temp
from translation import Translation
from pyrogram import Client, filters
from .test import get_configs, update_configs, CLIENT
from .utils import parse_buttons
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

CLIENT = CLIENT()
SYD = ["https://files.catbox.moe/3lwlbm.png"]
logger = logging.getLogger(__name__)


@Client.on_message(filters.private & filters.command(['settings']))
async def settings(client, message):
    user_id = message.from_user.id
    if temp.lock.get(user_id):
        return await message.reply("A task is already in progress. Please wait for it to complete before changing settings.")

    ban_status = await db.get_ban_status(user_id)
    if ban_status["is_banned"]:
        return await message.reply_text(f"Access denied.\n\nReason: {ban_status['ban_reason']}")

    text="<b>֎ Settings ֎</b>\n\nManage personal configurations."
    await message.reply_photo(
        photo=random.choice(SYD),
        caption=text,
        reply_markup=main_buttons(),
        quote=True
    )

@Client.on_callback_query(filters.regex(r'^settings'))
async def settings_query(bot, query):
    await query.answer()
    user_id = query.from_user.id

    if temp.lock.get(user_id):
        return await query.answer("A task is already in progress. Please wait for it to complete before changing settings.", show_alert=True)

    try:
        parts = query.data.split("#")
        type = parts[1]
        data = parts[2] if len(parts) > 2 else None

        if type == "main":
            await query.message.edit_text(
                "<b>֎ Settings ֎</b>\n\nManage personal configurations.",
                reply_markup=main_buttons()
            )

        elif type in ["caption", "button", "db_uri", "file_size", "size_limit", "extension", "keywords"]:
            prompt_text = {
                "caption": "Send your custom caption. Use placeholders like `{filename}`, `{size}`, and `{caption}`.",
                "button": "Send your button in the format: `[Button Text][buttonurl:https://example.com]`",
                "db_uri": "Send your MongoDB connection string to be used for duplicate checking.",
                "file_size": "Send the file size limit in MB.",
                "size_limit": "Choose whether to allow files 'above' or 'below' the size limit.",
                "extension": "Send a comma-separated list of file extensions to filter (e.g., `mkv,mp4,zip`).",
                "keywords": "Send a comma-separated list of keywords to filter. Use a `-` prefix to exclude messages with a keyword (e.g., `cat,-dog`)."
            }
            await query.message.delete()
            prompt = await bot.send_message(user_id, prompt_text[type] + "\n\n/cancel to abort. /reset to clear this setting.")
            temp.USER_STATES[user_id] = {
                "state": f"awaiting_setting_{type}",
                "prompt_message_id": prompt.id
            }

        elif type == "filters":
            await query.message.edit_text(
                "<b>֎ Message Filters ֎</b>\n\nToggle which message types to forward.",
                reply_markup=await get_filters_markup(user_id)
            )

        elif type == "toggle_filter":
            filter_key = data
            current_configs = await get_configs(user_id)
            current_filters = current_configs.get('filters', {})
            current_filters[filter_key] = not current_filters.get(filter_key, True)
            await update_configs(user_id, 'filters', current_filters)
            await query.message.edit_reply_markup(reply_markup=await get_filters_markup(user_id))

        elif type == "bots":
            buttons = []
            bots = await db.get_bots(user_id)
            for _bot in bots:
                if not _bot.get('id'): continue
                bot_name = _bot.get('name') or _bot.get('username', f"ID: {_bot['id']}")
                bot_id = _bot.get('id')
                buttons.append([InlineKeyboardButton(bot_name, callback_data=f"settings#editbot#{bot_id}")])
            buttons.append([InlineKeyboardButton('+ Add Bot', callback_data="settings#addbot")])
            buttons.append([InlineKeyboardButton('+ Add Userbot', callback_data="settings#adduserbot")])
            buttons.append([InlineKeyboardButton('« Back', callback_data="settings#main")])
            await query.message.edit_text(
                "<b>֎ Bots & Userbots ֎</b>\n\nManage connected bots and userbots.",
                reply_markup=InlineKeyboardMarkup(buttons)
            )
            
        elif type=="addbot":
           await query.message.delete()
           temp.USER_STATES[user_id] = {"state": "awaiting_bot_token"}
           await bot.send_message(user_id, "Forward the message from @BotFather containing the token, or just send the token string.\n\n/cancel - to abort.")

        elif type=="adduserbot":
           await query.message.delete()
           temp.USER_STATES[user_id] = {"state": "awaiting_user_session"}
           await bot.send_message(user_id, "Send the Pyrogram (v2) session string.\n\n/cancel - to cancel.")

        elif type.startswith("editbot"):
           bot_id = int(data)
           _bot = await db.get_bot(user_id, bot_id)
           if not _bot: return await query.message.edit_text("Bot not found.")
           bot_name = _bot.get('name', 'N/A'); bot_uname = _bot.get('username'); is_bot = _bot.get('is_bot', True)
           TEXT = Translation.BOT_DETAILS if is_bot else Translation.USER_DETAILS
           uname_display = f"@{bot_uname}" if bot_uname else "Not Set"
           buttons = [[InlineKeyboardButton('- Remove', callback_data=f"settings#removebot#{bot_id}")],
                      [InlineKeyboardButton('« Back', callback_data="settings#bots")]]
           await query.message.edit_text(TEXT.format(bot_name, bot_id, uname_display), reply_markup=InlineKeyboardMarkup(buttons))

        elif type.startswith("removebot"):
           bot_id = int(data)
           await db.remove_bot(user_id, bot_id)
           await query.message.edit_text("Bot removed. ✓", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('« Back', callback_data="settings#bots")]]))

        elif type == "channels":
            buttons = []
            channels = await db.get_user_channels(user_id)
            for channel in channels:
                buttons.append([InlineKeyboardButton(f"● {channel['title']}", callback_data=f"settings#editchannel#{channel['chat_id']}")])
            buttons.append([InlineKeyboardButton('+ Add Channel', callback_data="settings#addchannel")])
            buttons.append([InlineKeyboardButton('« Back', callback_data="settings#main")])
            await query.message.edit_text(
                "<b>֎ Target Channels ֎</b>\n\nManage target chats for forwarding.",
                reply_markup=InlineKeyboardMarkup(buttons))
        
        elif type == "addchannel":
           await query.message.delete()
           prompt_message = await bot.send_message(user_id, "<b>Set Target Chat</b>\n\nForward a message from the target chat.\n\n/cancel - to cancel.")
           temp.USER_STATES[user_id] = {
               "state": "awaiting_channel_forward",
               "prompt_message_id": prompt_message.id
           }
        
        elif type.startswith("editchannel"):
           chat_id = int(data)
           chat = await db.get_channel_details(user_id, chat_id)
           buttons = [[InlineKeyboardButton('- Remove', callback_data=f"settings#removechannel#{chat_id}")],
                      [InlineKeyboardButton('« Back', callback_data="settings#channels")]]
           await query.message.edit_text(f"<b>֎ Channel Details ֎</b>\n\n<b>Title:</b> <code>{chat['title']}</code>\n<b>ID:</b> <code>{chat['chat_id']}</code>\n<b>Username:</b> {chat['username']}", reply_markup=InlineKeyboardMarkup(buttons))

        elif type.startswith("removechannel"):
           chat_id = int(data)
           await db.remove_channel(user_id, chat_id)
           await query.message.edit_text("Channel removed. ✓", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('« Back', callback_data="settings#channels")]]))

    except Exception as e:
        logger.error(f"Error in settings_query: {e}", exc_info=True)

async def get_filters_markup(user_id):
    configs = await get_configs(user_id)
    filters = configs.get('filters', {})
    buttons = []
    filter_keys = ['text', 'photo', 'video', 'document', 'audio', 'voice', 'sticker', 'animation', 'poll']
    for key in filter_keys:
        status = "✓" if filters.get(key, True) else "✗"
        buttons.append(InlineKeyboardButton(f"{status} {key.title()}", callback_data=f"settings#toggle_filter#{key}"))
    
    markup = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    markup.append([InlineKeyboardButton('« Back', callback_data="settings#main")])
    return InlineKeyboardMarkup(markup)

def main_buttons():
    buttons = [[
        InlineKeyboardButton('Bots & Userbots', callback_data='settings#bots'),
        InlineKeyboardButton('Channels', callback_data='settings#channels')
    ], [
        InlineKeyboardButton('Caption', callback_data='settings#caption'),
        InlineKeyboardButton('Button', callback_data='settings#button')
    ], [
        InlineKeyboardButton('Message Filters', callback_data='settings#filters'),
        InlineKeyboardButton('File Size Filter', callback_data='settings#file_size')
    ], [
        InlineKeyboardButton('Keyword Filter', callback_data='settings#keywords'),
        InlineKeyboardButton('Extension Filter', callback_data='settings#extension')
    ], [
        InlineKeyboardButton('Duplicate Check DB', callback_data='settings#db_uri')
    ],[
        InlineKeyboardButton('« Back', callback_data='back')
    ]]
    return InlineKeyboardMarkup(buttons)
