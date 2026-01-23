# plugins/settings.py
import asyncio
import random
import logging
from database import db
from config import Config, temp
from translation import Translation
from pyrogram import Client, filters, enums
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message, InputMediaPhoto
from .test import get_configs, update_configs, CLIENT
from .utils import parse_buttons, edit_or_reply

CLIENT = CLIENT()
ZEN = ["https://files.catbox.moe/3lwlbm.png"]
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
    
    # Use reply_photo but try to reuse if it was a callback loop (handled in query)
    await message.reply_photo(
        photo=random.choice(ZEN),
        caption=text,
        reply_markup=main_buttons(),
        quote=True
    )

@Client.on_callback_query(filters.regex(r'^settings'))
async def settings_query(bot, query):
    user_id = query.from_user.id

    if temp.lock.get(user_id):
        return await query.answer("A task is already in progress. Please wait for it to complete before changing settings.", show_alert=True)
    
    # Attempt to answer to stop spinner
    try: await query.answer() 
    except: pass

    try:
        parts = query.data.split("#")
        type = parts[1]
        data = parts[2] if len(parts) > 2 else None

        if type == "main":
            try:
                await query.message.edit_media(
                    media=InputMediaPhoto(random.choice(ZEN), caption="<b>֎ Settings ֎</b>\n\nManage personal configurations."),
                    reply_markup=main_buttons()
                )
            except:
                await query.message.edit_caption(
                    caption="<b>֎ Settings ֎</b>\n\nManage personal configurations.",
                    reply_markup=main_buttons()
                )

        elif type == "thumbnail":
            user_configs = await get_configs(user_id)
            current_thumb = user_configs.get('thumbnail')
            
            if current_thumb:
                text = "<b>🖼 Custom Thumbnail</b>\n\nYou have a custom thumbnail set."
                buttons = [
                    [InlineKeyboardButton("👁 View Current", callback_data="settings#viewthumb")],
                    [InlineKeyboardButton("✏️ Change", callback_data="settings#changethumb"),
                     InlineKeyboardButton("🗑 Delete", callback_data="settings#delthumb")],
                    [InlineKeyboardButton("« Back", callback_data="settings#main")]
                ]
            else:
                text = "<b>🖼 Custom Thumbnail</b>\n\nNo custom thumbnail set. Default bot thumbnail will be used (if any)."
                buttons = [
                    [InlineKeyboardButton("➕ Set Thumbnail", callback_data="settings#changethumb")],
                    [InlineKeyboardButton("« Back", callback_data="settings#main")]
                ]
            
            # Using edit_caption if message is media, else edit_text
            if query.message.photo:
                await query.message.edit_caption(caption=text, reply_markup=InlineKeyboardMarkup(buttons))
            else:
                await edit_or_reply(query.message, text, reply_markup=InlineKeyboardMarkup(buttons))

        elif type == "viewthumb":
            user_configs = await get_configs(user_id)
            thumb_id = user_configs.get('thumbnail')
            if not thumb_id:
                return await query.answer("Thumbnail not found!", show_alert=True)
            
            # Edit the current media message to show the thumb
            buttons = [[InlineKeyboardButton("« Back", callback_data="settings#thumbnail")]]
            try:
                await query.message.edit_media(
                    media=InputMediaPhoto(thumb_id, caption="<b>Current Custom Thumbnail</b>"),
                    reply_markup=InlineKeyboardMarkup(buttons)
                )
            except Exception as e:
                await query.message.reply_photo(thumb_id, caption="<b>Current Custom Thumbnail</b>", reply_markup=InlineKeyboardMarkup(buttons))

        elif type == "delthumb":
            await update_configs(user_id, 'thumbnail', None)
            await query.answer("Thumbnail deleted!", show_alert=True)
            # Return to thumbnail menu
            await settings_query(bot, query) # Recursive call to refresh menu (lazy way but works with state)
            # Or manually redirect:
            # Re-trigger the 'thumbnail' block logic:
            query.data = "settings#thumbnail"
            await settings_query(bot, query)

        elif type == "changethumb":
            prompt = await edit_or_reply(query.message, "<b>Send a Photo</b> to set as your custom thumbnail.\n\n/cancel to abort.")
            temp.USER_STATES[user_id] = {
                "state": "awaiting_setting_thumbnail",
                "prompt_message_id": prompt.id
            }

        elif type in ["caption", "button", "db_uri", "file_size", "size_limit", "extension", "keywords"]:
            prompt_text = {
                "caption": "Send your custom caption. Use placeholders like `{filename}`, `{size}`, and `{caption}`.",
                "button": "Send your button in the format: `[Button Text][buttonurl:https://example.com]`",
                "db_uri": "Send your MongoDB connection string to be used for duplicate checking.",
                "file_size": "Send the file size limit in MB.",
                "size_limit": "Choose whether to allow files 'above' or 'below' the size limit.",
                "extension": "Send a comma-separated list of file extensions to filter (e.g., `mkv,mp4,zip`).",
                "keywords": "Send a comma-separated list of keywords to filter. Use a `-` prefix to exclude messages with a keyword (e.g., `cat,-dog`).",
            }
            # We delete the menu message to clean up chat, and send a fresh prompt which will be deleted later
            await query.message.delete()
            prompt = await bot.send_message(user_id, prompt_text[type] + "\n\n/cancel to abort. /reset to clear this setting.")
            temp.USER_STATES[user_id] = {
                "state": f"awaiting_setting_{type}",
                "prompt_message_id": prompt.id
            }

        elif type == "filters":
            text = "<b>֎ Message Filters ֎</b>\n\nToggle which message types to forward."
            if query.message.photo:
                await query.message.edit_caption(text, reply_markup=await get_filters_markup(user_id))
            else:
                await edit_or_reply(query.message, text, reply_markup=await get_filters_markup(user_id))

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
            
            text = "<b>֎ Bots & Userbots ֎</b>\n\nManage connected bots and userbots."
            if query.message.photo:
                await query.message.edit_caption(text, reply_markup=InlineKeyboardMarkup(buttons))
            else:
                await edit_or_reply(query.message, text, reply_markup=InlineKeyboardMarkup(buttons))
            
        elif type=="addbot":
           await query.message.delete()
           temp.USER_STATES[user_id] = {"state": "awaiting_bot_token"}
           await bot.send_message(user_id, "Forward the message from @BotFather containing the token, or just send the token string.\n\n/cancel - to abort.")

        elif type=="adduserbot":
           await query.message.delete()
           temp.USER_STATES[user_id] = {"state": "awaiting_user_session"}
           await bot.send_message(user_id, "Send the Pyrogram (v2) session string.\n\nGet one from @mdsessiongenbot.\n\n/cancel - to cancel.")

        elif type.startswith("editbot"):
           bot_id = int(data)
           _bot = await db.get_bot(user_id, bot_id)
           if not _bot: return await edit_or_reply(query.message, "Bot not found.")
           bot_name = _bot.get('name', 'N/A'); bot_uname = _bot.get('username'); is_bot = _bot.get('is_bot', True)
           TEXT = Translation.BOT_DETAILS if is_bot else Translation.USER_DETAILS
           uname_display = f"@{bot_uname}" if bot_uname else "Not Set"
           buttons = [[InlineKeyboardButton('- Remove', callback_data=f"settings#removebot#{bot_id}")],
                      [InlineKeyboardButton('« Back', callback_data="settings#bots")]]
           
           if query.message.photo:
               await query.message.edit_caption(TEXT.format(bot_name, bot_id, uname_display), reply_markup=InlineKeyboardMarkup(buttons))
           else:
               await edit_or_reply(query.message, TEXT.format(bot_name, bot_id, uname_display), reply_markup=InlineKeyboardMarkup(buttons))

        elif type.startswith("removebot"):
           bot_id = int(data)
           await db.remove_bot(user_id, bot_id)
           # Go back to bot list
           query.data = "settings#bots"
           await settings_query(bot, query)

        elif type == "channels":
            buttons = []
            channels = await db.get_user_channels(user_id)
            for channel in channels:
                buttons.append([InlineKeyboardButton(f"● {channel['title']}", callback_data=f"settings#editchannel#{channel['chat_id']}")])
            buttons.append([InlineKeyboardButton('+ Add Channel', callback_data="settings#addchannel")])
            buttons.append([InlineKeyboardButton('« Back', callback_data="settings#main")])
            
            text = "<b>֎ Target Channels ֎</b>\n\nManage target chats for forwarding."
            if query.message.photo:
                await query.message.edit_caption(text, reply_markup=InlineKeyboardMarkup(buttons))
            else:
                await edit_or_reply(query.message, text, reply_markup=InlineKeyboardMarkup(buttons))
        
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
           text = f"<b>֎ Channel Details ֎</b>\n\n<b>Title:</b> <code>{chat['title']}</code>\n<b>ID:</b> <code>{chat['chat_id']}</code>\n<b>Username:</b> {chat['username']}"
           if query.message.photo:
                await query.message.edit_caption(text, reply_markup=InlineKeyboardMarkup(buttons))
           else:
                await edit_or_reply(query.message, text, reply_markup=InlineKeyboardMarkup(buttons))

        elif type.startswith("removechannel"):
           chat_id = int(data)
           await db.remove_channel(user_id, chat_id)
           # Go back to channel list
           query.data = "settings#channels"
           await settings_query(bot, query)

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
        InlineKeyboardButton('Custom Thumbnail', callback_data='settings#thumbnail')
    ], [
        InlineKeyboardButton('File Size Filter', callback_data='settings#file_size'),
        InlineKeyboardButton('Duplicate Check DB', callback_data='settings#db_uri')
    ], [
        InlineKeyboardButton('Keyword Filter', callback_data='settings#keywords'),
        InlineKeyboardButton('Extension Filter', callback_data='settings#extension')
    ], [
        InlineKeyboardButton('« Back', callback_data='back')
    ]]
    return InlineKeyboardMarkup(buttons)
