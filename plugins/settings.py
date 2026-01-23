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

# Config Metadata
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

@Client.on_message(filters.private & filters.command(['settings']))
async def settings(client, message):
    user_id = message.from_user.id
    if temp.lock.get(user_id):
        return await message.reply("A task is already in progress. Please wait for it to complete before changing settings.")

    ban_status = await db.get_ban_status(user_id)
    if ban_status["is_banned"]:
        return await message.reply_text(f"Access denied.\n\nReason: {ban_status['ban_reason']}")

    text="<b>Settings</b>\n\nManage personal configurations."
    
    await message.reply_photo(
        photo=random.choice(ZEN),
        caption=text,
        reply_markup=main_buttons(),
        quote=True
    )

@Client.on_callback_query(filters.regex(r'^settings'))
async def settings_query(bot, query):
    user_id = query.from_user.id
    
    # Clear previous state on navigation
    temp.USER_STATES.pop(user_id, None)

    if temp.lock.get(user_id):
        return await query.answer("A task is already in progress. Please wait for it to complete before changing settings.", show_alert=True)
    
    try: await query.answer() 
    except: pass

    try:
        parts = query.data.split("#")
        type = parts[1]
        data = parts[2] if len(parts) > 2 else None

        if type == "main":
            try:
                await query.message.edit_media(
                    media=InputMediaPhoto(random.choice(ZEN), caption="<b>Settings</b>\n\nManage personal configurations."),
                    reply_markup=main_buttons()
                )
            except:
                await edit_or_reply(query.message, "<b>Settings</b>\n\nManage personal configurations.", reply_markup=main_buttons())

        # --- SPECIAL MENU: FILE SIZE FILTER ---
        elif type == "file_size":
            configs = await get_configs(user_id)
            size_limit_bytes = configs.get('file_size')
            mode = configs.get('size_limit', 'below') # Default to below if not set
            
            size_display = "Not Set"
            if size_limit_bytes:
                mb_value = float(size_limit_bytes) / (1024 * 1024)
                size_display = f"{mb_value:.2f} MB"
            
            mode_display = "Below (Skip larger files)" if mode == 'below' else "Above (Skip smaller files)"
            
            text = (
                "<b>FILE SIZE FILTER</b>\n\n"
                f"<b>Current Limit:</b> <code>{size_display}</code>\n"
                f"<b>Mode:</b> <code>{mode_display}</code>\n\n"
                "Set a limit and choose whether to process files <b>Above</b> or <b>Below</b> that size."
            )
            
            buttons = []
            buttons.append([InlineKeyboardButton("Set Limit (MB)", callback_data="settings#set#file_size")])
            
            # Toggle Button
            toggle_text = "Switch to 'Above'" if mode == 'below' else "Switch to 'Below'"
            buttons.append([InlineKeyboardButton(toggle_text, callback_data="settings#toggle_size_limit")])
            
            if size_limit_bytes:
                buttons.append([InlineKeyboardButton("Reset Limit", callback_data="settings#reset#file_size")])
            
            buttons.append([InlineKeyboardButton("Back", callback_data="settings#main")])
            
            await edit_or_reply(query.message, text, reply_markup=InlineKeyboardMarkup(buttons))

        elif type == "toggle_size_limit":
            configs = await get_configs(user_id)
            current_mode = configs.get('size_limit', 'below')
            new_mode = 'above' if current_mode == 'below' else 'below'
            await update_configs(user_id, 'size_limit', new_mode)
            # Refresh the File Size menu
            query.data = "settings#file_size"
            await settings_query(bot, query)

        # --- STANDARD DYNAMIC MENUS ---
        elif type in SETTING_META:
            key = type
            meta = SETTING_META[key]
            configs = await get_configs(user_id)
            value = configs.get(key)
            
            buttons = []
            if value:
                buttons.append([InlineKeyboardButton(f"View Value", callback_data=f"settings#view#{key}")])
            
            btn_text = "Update Value" if value else f"Set Value"
            buttons.append([InlineKeyboardButton(btn_text, callback_data=f"settings#set#{key}")])
            
            if value:
                buttons.append([InlineKeyboardButton("Reset Value", callback_data=f"settings#reset#{key}")])
            
            buttons.append([InlineKeyboardButton("Back", callback_data="settings#main")])
            
            text = f"<b>{meta['title']}</b>\n\n{meta['desc']}"
            await edit_or_reply(query.message, text, reply_markup=InlineKeyboardMarkup(buttons))

        elif type == "view":
            key = data
            configs = await get_configs(user_id)
            value = configs.get(key)
            if value is None or value == "":
                await query.answer("No value set.", show_alert=True)
            else:
                await query.answer(f"Current Value:\n\n{str(value)[:180]}", show_alert=True)

        elif type == "set":
            key = data
            meta = SETTING_META.get(key, {})
            # Use specific text for file_size since it's now handled via the generic 'set' router but needs specific prompt
            if key == "file_size":
                text = "<b>FILE SIZE FILTER</b>\n\nSend the size limit in <b>MB</b> (e.g., <code>100</code> or <code>1.5</code>)."
                back_callback = "settings#file_size"
            else:
                text = f"<b>{meta.get('title', 'SETTING')}</b>\n\n{meta.get('desc', 'Send the new value.')}"
                back_callback = f"settings#{key}"
            
            buttons = [[InlineKeyboardButton("Cancel", callback_data=back_callback)]]
            
            prompt = await edit_or_reply(query.message, text, reply_markup=InlineKeyboardMarkup(buttons))
            temp.USER_STATES[user_id] = {
                "state": f"awaiting_setting_{key}",
                "prompt_message_id": prompt.id
            }

        elif type == "reset":
            key = data
            await update_configs(user_id, key, None)
            await query.answer("Value reset to default.", show_alert=True)
            # Return to appropriate menu
            if key == "file_size":
                query.data = "settings#file_size"
            else:
                query.data = f"settings#{key}"
            await settings_query(bot, query)

        # --- BOTS & USERBOTS ---
        elif type == "bots":
            bots = await db.get_bots(user_id)
            bot_buttons = []
            
            for _bot in bots:
                if not _bot.get('id'): continue
                bot_name = _bot.get('name') or _bot.get('username', f"ID: {_bot['id']}")
                bot_id = _bot.get('id')
                bot_buttons.append(InlineKeyboardButton(bot_name[:15], callback_data=f"settings#editbot#{bot_id}"))

            if len(bot_buttons) % 2 != 0:
                bot_buttons.append(InlineKeyboardButton("(｡•̀ᴗ-)", callback_data="settings#empty"))

            buttons = [bot_buttons[i:i + 2] for i in range(0, len(bot_buttons), 2)]
            
            buttons.append([
                InlineKeyboardButton('+ Add Bot', callback_data="settings#addbot"),
                InlineKeyboardButton('+ Add Userbot', callback_data="settings#adduserbot")
            ])
            
            buttons.append([InlineKeyboardButton('Back', callback_data="settings#main")])
            
            text = "<b>Bots & Userbots</b>\n\nManage connected bots and userbots."
            await edit_or_reply(query.message, text, reply_markup=InlineKeyboardMarkup(buttons))

        elif type == "empty":
            await query.answer("(｡•̀ᴗ-) Just a placeholder!", show_alert=True)

        elif type == "addbot":
           text = (
               "<b>BOT ADDITION</b>\n\n"
               "Forward the message from <b>@BotFather</b> containing the token, or just send the token string."
           )
           buttons = [[InlineKeyboardButton("Cancel", callback_data="settings#bots")]]
           prompt = await edit_or_reply(query.message, text, reply_markup=InlineKeyboardMarkup(buttons))
           temp.USER_STATES[user_id] = {
               "state": "awaiting_bot_token",
               "prompt_message_id": prompt.id
           }

        elif type == "adduserbot":
           text = (
               "<b>USERBOT ADDITION</b>\n\n"
               "Send the <b>Pyrogram (v2)</b> session string."
           )
           buttons = [[InlineKeyboardButton("Cancel", callback_data="settings#bots")]]
           prompt = await edit_or_reply(query.message, text, reply_markup=InlineKeyboardMarkup(buttons))
           temp.USER_STATES[user_id] = {
               "state": "awaiting_user_session",
               "prompt_message_id": prompt.id
           }

        # --- THUMBNAIL ---
        elif type == "thumbnail":
            user_configs = await get_configs(user_id)
            current_thumb = user_configs.get('thumbnail')
            
            if current_thumb:
                text = "<b>Custom Thumbnail</b>\n\nYou have a custom thumbnail set."
                buttons = [
                    [InlineKeyboardButton("View Current", callback_data="settings#viewthumb")],
                    [InlineKeyboardButton("Change", callback_data="settings#changethumb"),
                     InlineKeyboardButton("Delete", callback_data="settings#delthumb")],
                    [InlineKeyboardButton("Back", callback_data="settings#main")]
                ]
            else:
                text = "<b>Custom Thumbnail</b>\n\nNo custom thumbnail set. Default bot thumbnail will be used (if any)."
                buttons = [
                    [InlineKeyboardButton("Set Thumbnail", callback_data="settings#changethumb")],
                    [InlineKeyboardButton("Back", callback_data="settings#main")]
                ]
            
            await edit_or_reply(query.message, text, reply_markup=InlineKeyboardMarkup(buttons))

        elif type == "viewthumb":
            user_configs = await get_configs(user_id)
            thumb_id = user_configs.get('thumbnail')
            if not thumb_id:
                return await query.answer("Thumbnail not found!", show_alert=True)
            
            buttons = [[InlineKeyboardButton("Back", callback_data="settings#thumbnail")]]
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
            query.data = "settings#thumbnail"
            await settings_query(bot, query)

        elif type == "changethumb":
            buttons = [[InlineKeyboardButton("Cancel", callback_data="settings#thumbnail")]]
            prompt = await edit_or_reply(query.message, "<b>Send a Photo</b> to set as your custom thumbnail.", reply_markup=InlineKeyboardMarkup(buttons))
            temp.USER_STATES[user_id] = {
                "state": "awaiting_setting_thumbnail",
                "prompt_message_id": prompt.id
            }

        # --- FILTERS ---
        elif type == "filters":
            text = "<b>Message Filters</b>\n\nToggle which message types to forward."
            await edit_or_reply(query.message, text, reply_markup=await get_filters_markup(user_id))

        elif type == "toggle_filter":
            filter_key = data
            current_configs = await get_configs(user_id)
            current_filters = current_configs.get('filters', {})
            current_filters[filter_key] = not current_filters.get(filter_key, True)
            await update_configs(user_id, 'filters', current_filters)
            await query.message.edit_reply_markup(reply_markup=await get_filters_markup(user_id))

        # --- BOTS & CHANNELS SUBMENUS ---
        elif type.startswith("editbot"):
           bot_id = int(data)
           _bot = await db.get_bot(user_id, bot_id)
           if not _bot: return await edit_or_reply(query.message, "Bot not found.")
           bot_name = _bot.get('name', 'N/A'); bot_uname = _bot.get('username'); is_bot = _bot.get('is_bot', True)
           TEXT = Translation.BOT_DETAILS if is_bot else Translation.USER_DETAILS
           uname_display = f"@{bot_uname}" if bot_uname else "Not Set"
           buttons = [[InlineKeyboardButton('Remove', callback_data=f"settings#removebot#{bot_id}")],
                      [InlineKeyboardButton('Back', callback_data="settings#bots")]]
           
           await edit_or_reply(query.message, TEXT.format(bot_name, bot_id, uname_display), reply_markup=InlineKeyboardMarkup(buttons))

        elif type.startswith("removebot"):
           bot_id = int(data)
           await db.remove_bot(user_id, bot_id)
           query.data = "settings#bots"
           await settings_query(bot, query)

        elif type == "channels":
            buttons = []
            channels = await db.get_user_channels(user_id)
            for channel in channels:
                buttons.append([InlineKeyboardButton(f"● {channel['title']}", callback_data=f"settings#editchannel#{channel['chat_id']}")])
            buttons.append([InlineKeyboardButton('+ Add Channel', callback_data="settings#addchannel")])
            buttons.append([InlineKeyboardButton('Back', callback_data="settings#main")])
            
            text = "<b>Target Channels</b>\n\nManage target chats for forwarding."
            await edit_or_reply(query.message, text, reply_markup=InlineKeyboardMarkup(buttons))
        
        elif type == "addchannel":
           prompt_message = await edit_or_reply(query.message, "<b>Set Target Chat</b>\n\nForward a message from the target chat.\n\n/cancel - to cancel.")
           temp.USER_STATES[user_id] = {
               "state": "awaiting_channel_forward",
               "prompt_message_id": prompt_message.id
           }
        
        elif type.startswith("editchannel"):
           chat_id = int(data)
           chat = await db.get_channel_details(user_id, chat_id)
           buttons = [[InlineKeyboardButton('Remove', callback_data=f"settings#removechannel#{chat_id}")],
                      [InlineKeyboardButton('Back', callback_data="settings#channels")]]
           text = f"<b>Channel Details</b>\n\n<b>Title:</b> <code>{chat['title']}</code>\n<b>ID:</b> <code>{chat['chat_id']}</code>\n<b>Username:</b> {chat['username']}"
           await edit_or_reply(query.message, text, reply_markup=InlineKeyboardMarkup(buttons))

        elif type.startswith("removechannel"):
           chat_id = int(data)
           await db.remove_channel(user_id, chat_id)
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
    markup.append([InlineKeyboardButton('Back', callback_data="settings#main")])
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
        InlineKeyboardButton('Back', callback_data='back')
    ]]
    return InlineKeyboardMarkup(buttons)
