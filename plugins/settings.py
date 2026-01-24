# plugins/settings.py
import asyncio
import random
import logging
import database
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

async def generate_setting_page(user_id, setting_key):
    """Generates the Text and Markup for a specific setting page."""
    configs = await get_configs(user_id)
    
    if setting_key == "thumbnail":
        thumb_id = configs.get('thumbnail')
        if thumb_id:
            text = "<b>Custom Thumbnail</b>\n\nYou have a custom thumbnail set."
            buttons = [
                [InlineKeyboardButton("View Current", callback_data="settings#viewthumb")],
                [InlineKeyboardButton("Change Thumbnail", callback_data="settings#changethumb"),
                 InlineKeyboardButton("Delete Thumbnail", callback_data="settings#delthumb")],
                [InlineKeyboardButton("Back", callback_data="settings#main")]
            ]
            return text, InlineKeyboardMarkup(buttons), thumb_id
        else:
            text = "<b>Custom Thumbnail</b>\n\nNo custom thumbnail set. Default bot thumbnail will be used (if any)."
            buttons = [
                [InlineKeyboardButton("Set Thumbnail", callback_data="settings#changethumb")],
                [InlineKeyboardButton("Back", callback_data="settings#main")]
            ]
            return text, InlineKeyboardMarkup(buttons), None

    elif setting_key == "file_size":
        size_limit = configs.get('file_size')
        mode = configs.get('size_limit', 'below')
        
        display_size = f"{float(size_limit)/1048576:.2f} MB" if size_limit else "Not Set"
        display_mode = "Below (Skip larger)" if mode == 'below' else "Above (Skip smaller)"
        
        text = (f"<b>FILE SIZE FILTER</b>\n\n"
                f"<b>Current Limit:</b> <code>{display_size}</code>\n"
                f"<b>Mode:</b> <code>{display_mode}</code>\n\n"
                f"Set a limit and choose whether to process files <b>Above</b> or <b>Below</b> that size.")
        
        buttons = []
        buttons.append([InlineKeyboardButton("Set Limit (MB)", callback_data="settings#set#file_size")])
        toggle_text = "Switch to 'Above'" if mode == 'below' else "Switch to 'Below'"
        buttons.append([InlineKeyboardButton(toggle_text, callback_data="settings#toggle_size_limit")])
        
        if size_limit:
            buttons.append([InlineKeyboardButton("Reset Limit", callback_data="settings#reset#file_size")])
        
        buttons.append([InlineKeyboardButton("Back", callback_data="settings#main")])
        return text, InlineKeyboardMarkup(buttons), None

    elif setting_key in SETTING_META:
        meta = SETTING_META[setting_key]
        val = configs.get(setting_key)
        
        text = f"<b>{meta['title']}</b>\n\n{meta['desc']}"
        buttons = []
        
        if val:
            buttons.append([InlineKeyboardButton("View Value", callback_data=f"settings#view#{setting_key}")])
            buttons.append([InlineKeyboardButton("Update Value", callback_data=f"settings#set#{setting_key}")])
            buttons.append([InlineKeyboardButton("Reset Value", callback_data=f"settings#reset#{setting_key}")])
        else:
            buttons.append([InlineKeyboardButton("Set Value", callback_data=f"settings#set#{setting_key}")])
        
        buttons.append([InlineKeyboardButton("Back", callback_data="settings#main")])
        return text, InlineKeyboardMarkup(buttons), None
    
    return "Error", None, None

@Client.on_message(filters.private & filters.command(['settings']))
async def settings(client, message):
    user_id = message.from_user.id
    if temp.lock.get(user_id):
        return await message.reply("A task is already in progress.")

    ban_status = await database.db.get_ban_status(user_id)
    if ban_status["is_banned"]:
        return await message.reply_text(f"Access denied.\n\nReason: {ban_status['ban_reason']}")

    await message.reply_photo(
        photo=random.choice(ZEN),
        caption="<b>Settings</b>\n\nManage personal configurations.",
        reply_markup=main_buttons(),
        quote=True
    )

@Client.on_callback_query(filters.regex(r'^settings'))
async def settings_query(bot, query):
    user_id = query.from_user.id
    temp.USER_STATES.pop(user_id, None)

    if temp.lock.get(user_id):
        return await query.answer("Task in progress. Please wait.", show_alert=True)
    
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

        elif type in SETTING_META or type in ["file_size", "thumbnail"]:
            text, markup, _ = await generate_setting_page(user_id, type)
            await edit_or_reply(query.message, text, reply_markup=markup)

        elif type == "toggle_size_limit":
            configs = await get_configs(user_id)
            curr = configs.get('size_limit', 'below')
            new_mode = 'above' if curr == 'below' else 'below'
            await update_configs(user_id, 'size_limit', new_mode)
            text, markup, _ = await generate_setting_page(user_id, "file_size")
            await edit_or_reply(query.message, text, reply_markup=markup)

        elif type == "view":
            configs = await get_configs(user_id)
            val = configs.get(data)
            await query.answer(f"Value:\n{str(val)[:150]}" if val else "Empty", show_alert=True)

        elif type == "set":
            key = data
            if key == "file_size":
                text = "<b>FILE SIZE FILTER</b>\n\nSend the size limit in <b>MB</b> (e.g., <code>100</code> or <code>1.5</code>)."
                back_cb = "settings#file_size"
            else:
                meta = SETTING_META[key]
                text = f"<b>{meta['title']}</b>\n\n{meta['desc']}"
                back_cb = f"settings#{key}"
            
            prompt = await edit_or_reply(query.message, text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Cancel", callback_data=back_cb)]]))
            temp.USER_STATES[user_id] = {"state": f"awaiting_setting_{key}", "prompt_message_id": prompt.id}

        elif type == "reset":
            await update_configs(user_id, data, None)
            await query.answer("Reset!", show_alert=True)
            text, markup, _ = await generate_setting_page(user_id, data)
            await edit_or_reply(query.message, text, reply_markup=markup)

        elif type == "changethumb":
            prompt = await edit_or_reply(query.message, "<b>Send a Photo</b> to set as your custom thumbnail.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Cancel", callback_data="settings#thumbnail")]]))
            temp.USER_STATES[user_id] = {"state": "awaiting_setting_thumbnail", "prompt_message_id": prompt.id}

        elif type == "delthumb":
            await update_configs(user_id, 'thumbnail', None)
            await query.answer("Deleted!", show_alert=True)
            text, markup, _ = await generate_setting_page(user_id, "thumbnail")
            await edit_or_reply(query.message, text, reply_markup=markup)

        elif type == "viewthumb":
            configs = await get_configs(user_id)
            thumb = configs.get('thumbnail')
            if not thumb: return await query.answer("No thumbnail set", show_alert=True)
            try:
                await query.message.edit_media(media=InputMediaPhoto(thumb, caption="<b>Current Custom Thumbnail</b>"), reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Back", callback_data="settings#thumbnail")]]))
            except:
                await query.message.reply_photo(thumb, caption="<b>Current Custom Thumbnail</b>", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Back", callback_data="settings#thumbnail")]]))

        elif type == "filters":
            await edit_or_reply(query.message, "<b>Message Filters</b>\n\nToggle which message types to forward.", reply_markup=await get_filters_markup(user_id))

        elif type == "toggle_filter":
            current_configs = await get_configs(user_id)
            current_filters = current_configs.get('filters', {})
            current_filters[data] = not current_filters.get(data, True)
            await update_configs(user_id, 'filters', current_filters)
            await query.message.edit_reply_markup(reply_markup=await get_filters_markup(user_id))

        elif type == "bots":
            await show_bots_list(query.message, user_id)

        elif type == "empty":
            await query.answer("(｡•̀ᴗ-) Just a placeholder!", show_alert=True)

        elif type == "addbot":
           prompt = await edit_or_reply(query.message, "<b>ADD BOT</b>\n\n1. Open @BotFather\n2. Create a new bot.\n3. <b>Forward the message</b> with the token or send the token string here.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Cancel", callback_data="settings#bots")]]))
           temp.USER_STATES[user_id] = {"state": "awaiting_bot_token", "prompt_message_id": prompt.id}

        elif type == "adduserbot":
           prompt = await edit_or_reply(query.message, "<b>ADD USERBOT</b>\n\nSend the <b>Pyrogram (v2)</b> session string.\n\n<i>⚠️ Use a trusted string generator.</i>", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Cancel", callback_data="settings#bots")]]))
           temp.USER_STATES[user_id] = {"state": "awaiting_user_session", "prompt_message_id": prompt.id}

        elif type.startswith("editbot"):
           bot_id = int(data)
           _bot = await database.db.get_bot(user_id, bot_id)
           if not _bot: return await edit_or_reply(query.message, "Bot not found.")
           TEXT = Translation.BOT_DETAILS if _bot.get('is_bot', True) else Translation.USER_DETAILS
           uname = f"@{_bot.get('username')}" if _bot.get('username') else "Not Set"
           buttons = [[InlineKeyboardButton('Remove', callback_data=f"settings#removebot#{bot_id}")], [InlineKeyboardButton('Back', callback_data="settings#bots")]]
           await edit_or_reply(query.message, TEXT.format(_bot.get('name', 'N/A'), bot_id, uname), reply_markup=InlineKeyboardMarkup(buttons))

        elif type.startswith("removebot"):
           await database.db.remove_bot(user_id, int(data))
           await show_bots_list(query.message, user_id)

        elif type == "channels":
            await show_channels_list(query.message, user_id)
        
        elif type == "addchannel":
           prompt = await edit_or_reply(query.message, "<b>Set Target Chat</b>\n\nForward a message from the target chat.\n\n/cancel - to cancel.")
           temp.USER_STATES[user_id] = {"state": "awaiting_channel_forward", "prompt_message_id": prompt.id}
        
        elif type.startswith("editchannel"):
           chat = await database.db.get_channel_details(user_id, int(data))
           buttons = [[InlineKeyboardButton('Remove', callback_data=f"settings#removechannel#{data}")], [InlineKeyboardButton('Back', callback_data="settings#channels")]]
           await edit_or_reply(query.message, f"<b>Channel Details</b>\n\n<b>Title:</b> <code>{chat['title']}</code>\n<b>ID:</b> <code>{chat['chat_id']}</code>\n<b>Username:</b> {chat['username']}", reply_markup=InlineKeyboardMarkup(buttons))

        elif type.startswith("removechannel"):
           await database.db.remove_channel(user_id, int(data))
           await show_channels_list(query.message, user_id)

    except Exception as e:
        logger.error(f"Error in settings_query: {e}", exc_info=True)

async def show_bots_list(message, user_id):
    bots = await database.db.get_bots(user_id)
    bot_buttons = []
    for _bot in bots:
        if not _bot.get('id'): continue
        name = _bot.get('name') or _bot.get('username', f"ID: {_bot['id']}")
        bot_buttons.append(InlineKeyboardButton(name[:15], callback_data=f"settings#editbot#{_bot['id']}"))
    
    buttons = [bot_buttons[i:i + 2] for i in range(0, len(bot_buttons), 2)]
    if len(bot_buttons) % 2 != 0: buttons[-1].append(InlineKeyboardButton("(｡•̀ᴗ-)", callback_data="settings#empty"))
    
    buttons.append([InlineKeyboardButton('+ Add Bot', callback_data="settings#addbot"), InlineKeyboardButton('+ Add Userbot', callback_data="settings#adduserbot")])
    buttons.append([InlineKeyboardButton('Back', callback_data="settings#main")])
    await edit_or_reply(message, "<b>Bots & Userbots</b>\n\nManage connected bots and userbots.", reply_markup=InlineKeyboardMarkup(buttons))

async def show_channels_list(message, user_id):
    buttons = []
    channels = await database.db.get_user_channels(user_id)
    for channel in channels:
        buttons.append([InlineKeyboardButton(f"● {channel['title']}", callback_data=f"settings#editchannel#{channel['chat_id']}")])
    buttons.append([InlineKeyboardButton('+ Add Channel', callback_data="settings#addchannel")])
    buttons.append([InlineKeyboardButton('Back', callback_data="settings#main")])
    await edit_or_reply(message, "<b>Target Channels</b>\n\nManage target chats for forwarding.", reply_markup=InlineKeyboardMarkup(buttons))

async def get_filters_markup(user_id):
    configs = await get_configs(user_id)
    filters = configs.get('filters', {})
    buttons = []
    for key in ['text', 'photo', 'video', 'document', 'audio', 'voice', 'sticker', 'animation', 'poll']:
        status = "✓" if filters.get(key, True) else "✗"
        buttons.append(InlineKeyboardButton(f"{status} {key.title()}", callback_data=f"settings#toggle_filter#{key}"))
    markup = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    markup.append([InlineKeyboardButton('Back', callback_data="settings#main")])
    return InlineKeyboardMarkup(markup)

def main_buttons():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('Bots & Userbots', callback_data='settings#bots'), InlineKeyboardButton('Channels', callback_data='settings#channels')],
        [InlineKeyboardButton('Caption', callback_data='settings#caption'), InlineKeyboardButton('Button', callback_data='settings#button')],
        [InlineKeyboardButton('Message Filters', callback_data='settings#filters'), InlineKeyboardButton('Custom Thumbnail', callback_data='settings#thumbnail')],
        [InlineKeyboardButton('File Size Filter', callback_data='settings#file_size'), InlineKeyboardButton('Duplicate Check DB', callback_data='settings#db_uri')],
        [InlineKeyboardButton('Keyword Filter', callback_data='settings#keywords'), InlineKeyboardButton('Extension Filter', callback_data='settings#extension')],
        [InlineKeyboardButton('Back', callback_data='back')]
    ])

# --- SETTINGS INPUT HANDLER ---
# CHANGED GROUP TO -2 TO ENSURE IT RUNS BEFORE PUBLIC.PY HANDLER
@Client.on_message(filters.private & filters.incoming, group=-2)
async def settings_input_handler(bot: Client, message: Message):
    if message.edit_date: return

    user_id = message.from_user.id
    state_info = temp.USER_STATES.get(user_id)
    if not state_info: return

    current_state = state_info.get("state")
    prompt_id = state_info.get("prompt_message_id")
    
    # Check if this handler is responsible
    relevant_states = ["awaiting_bot_token", "awaiting_user_session", "awaiting_channel_forward"]
    is_setting = current_state and current_state.startswith("awaiting_setting_")
    
    if not (is_setting or current_state in relevant_states):
        return # Pass to public.py

    # --- HANDLE CANCEL ---
    if message.text and message.text.lower() == "/cancel":
        if prompt_id:
            try: await bot.delete_messages(user_id, prompt_id)
            except: pass
        temp.USER_STATES.pop(user_id, None)
        try: await message.delete() 
        except: pass
        await message.reply(Translation.CANCEL)
        message.stop_propagation()
        return

    # --- SETTINGS INPUT ---
    if is_setting:
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
             await bot.send_message(user_id, error_msg)
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
            if await database.db.in_channel(user_id, chat_id): await bot.send_message(user_id, "This channel has already been added.")
            else: 
                await database.db.add_channel(user_id, chat_id, title, username)
                await bot.send_message(user_id, "Channel added. ✓")
        temp.USER_STATES.pop(user_id, None)
    
    elif current_state == "awaiting_bot_token":
        try: await message.delete()
        except: pass
        if prompt_id:
             try: await bot.delete_messages(user_id, prompt_id)
             except: pass
        # FIXED: Removed parens so it calls the method on the instance `CLIENT`
        await CLIENT.add_bot(bot, message)
        temp.USER_STATES.pop(user_id, None)
    
    elif current_state == "awaiting_user_session":
        try: await message.delete()
        except: pass
        if prompt_id:
             try: await bot.delete_messages(user_id, prompt_id)
             except: pass
        # FIXED: Removed parens so it calls the method on the instance `CLIENT`
        await CLIENT.add_session(bot, message)
        temp.USER_STATES.pop(user_id, None)

    message.stop_propagation()