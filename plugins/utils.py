# mistaldrin/fwd/fwd-DawnUltra/plugins/utils.py

import re
import random
import time as tm
import logging
import os
from uuid import uuid4
from database import db
from config import temp
from translation import Translation
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message
from pyrogram.errors import MessageNotModified
from PIL import Image

STATUS = {}
SYD = ["https://files.catbox.moe/3lwlbm.png"]
logger = logging.getLogger(__name__)

# Moved from test.py to avoid circular imports
BTN_URL_REGEX = re.compile(r"(\[([^\[]+?)]\[buttonurl:/{0,2}(.+?)(:same)?])")

def parse_buttons(text, markup=True):
    """Parses button markdown into a Pyrogram InlineKeyboardMarkup."""
    buttons = []
    if not text:
        return None
    for match in BTN_URL_REGEX.finditer(text):
        n_escapes = 0
        to_check = match.start(1) - 1
        while to_check > 0 and text[to_check] == "\\":
            n_escapes += 1
            to_check -= 1

        if n_escapes % 2 == 0:
            if bool(match.group(4)) and buttons:
                buttons[-1].append(InlineKeyboardButton(
                    text=match.group(2),
                    url=match.group(3).replace(" ", "")))
            else:
                buttons.append([InlineKeyboardButton(
                    text=match.group(2),
                    url=match.group(3).replace(" ", ""))])
    if markup and buttons:
       buttons = InlineKeyboardMarkup(buttons)
    return buttons if buttons else None

def get_readable_time(seconds: int) -> str:
    if seconds == 0:
        return "0s"
    result = ""
    (days, remainder) = divmod(seconds, 86400)
    days = int(days)
    if days != 0:
        result += f"{days}d "
    (hours, remainder) = divmod(remainder, 3600)
    hours = int(hours)
    if hours != 0:
        result += f"{hours}h "
    (minutes, seconds) = divmod(remainder, 60)
    minutes = int(minutes)
    if minutes != 0:
        result += f"{minutes}m "
    seconds = int(seconds)
    if seconds != 0:
        result += f"{seconds}s"
    return result.strip()

async def format_thumbnail(path):
    """
    Optimizes the thumbnail to meet Telegram's strict requirements:
    - Format: JPEG
    - Size: < 200 KB
    - Dimensions: Max 320px (width or height)
    """
    if not os.path.exists(path): return None
    
    try:
        img = Image.open(path)
        
        # Convert to RGB (handle PNG alpha)
        if img.mode != 'RGB':
            img = img.convert('RGB')
        
        # Resize/Crop logic
        img.thumbnail((320, 320))
        
        new_path = path.rsplit('.', 1)[0] + "_processed.jpg"
        
        # Compress until < 200KB
        quality = 95
        while True:
            img.save(new_path, "JPEG", quality=quality)
            if os.path.getsize(new_path) < 200 * 1024: # 200KB
                break
            quality -= 5
            if quality < 10: break # Safety break
            
        return new_path
    except Exception as e:
        logger.error(f"Thumbnail formatting failed: {e}")
        return path # Return original if processing fails

class STS:
    def __init__(self, id):
        self.id = id
        self.data = STATUS

    def verify(self):
        return self.data.get(self.id)

    def store(self, From, to, start_id, end_id):
        self.data[self.id] = {
            "id": self.id,
            "FROM": From, 'TO': to, 'total_files': 0,
            'start_id': start_id, 'end_id': end_id,
            'fetched': 0, 'filtered': 0, 'deleted': 0, 'failed': 0,
            'duplicate': 0, 'total': abs(end_id - start_id) + 1,
            'start': tm.time(), 'status': 'running', 'batch': []
        }
        self.get(full=True)
        return STS(self.id)

    def get(self, value=None, full=False):
        values = self.data.get(self.id)
        if not values: return None
        if not full:
           return values.get(value)
        for k, v in values.items():
            setattr(self, k, v)
        return self
    
    def set_status(self, status):
        if self.id in self.data:
            self.data[self.id]['status'] = status
    
    def get_readable_time(self, seconds: int) -> str:
        return get_readable_time(seconds)

    def add(self, key=None, value=1):
        if self.id in self.data and key in self.data[self.id]:
            self.data[self.id][key] += value

    async def get_data(self, user_id, bot_id=None):
        if not bot_id:
            bot_id = temp.FORWARD_BOT_ID.get(user_id)
            if not bot_id:
                bot_id = temp.UNEQUIFY_USERBOT_ID.get(user_id)
                if not bot_id:
                    raise ValueError("Bot ID not found in session for new task.")

        bot = await db.get_bot(user_id, bot_id)
        if not bot:
            raise ValueError(f"Bot with ID {bot_id} not found in database for user {user_id}.")
            
        configs = await db.get_configs(user_id)
        filters = await db.get_filters(user_id)
        
        # Use the locally defined parse_buttons
        button = parse_buttons(configs.get('button', ''))
        
        return bot, configs.get('caption'), configs.get('forward_tag', False), {
            'filters': filters,
            'forward_delay': configs.get('forward_delay', 0.5),
            'thumbnail': configs.get('thumbnail')
        }, configs.get('protect'), button

async def start_range_selection(bot, message: Message, from_chat_id, from_title, to_chat_id, start_id, end_id, final_callback_prefix="fwd_final", mode="standard"):
    session_id = str(uuid4())
    temp.RANGE_SESSIONS[session_id] = {
        'user_id': message.chat.id,
        'chat_id': message.chat.id,
        'from_chat_id': from_chat_id,
        'from_title': from_title,
        'to_chat_id': to_chat_id,
        'start_id': start_id,
        'end_id': end_id,
        'order': 'asc',
        'final_callback': final_callback_prefix,
        'original_message_id': message.id,
        'message_id': None,
        'mode': mode 
    }
    await update_range_message(bot, session_id)

async def update_range_message(bot, session_id, message_to_edit=None):
    session = temp.RANGE_SESSIONS.get(session_id)
    if not session: return

    order_text = "Oldest ➔ Newest" if session['order'] == 'asc' else "Newest ➔ Oldest"
    text = Translation.RANGE_SELECTION_TXT.format(
        start=min(session['start_id'], session['end_id']), 
        end=max(session['start_id'], session['end_id']))
    display_button_text = f"Range: {session['start_id']} ➔ {session['end_id']} ({order_text})"
    confirm_cb = f"range_confirm_{session['final_callback']}_{session_id}"

    buttons = [
        [InlineKeyboardButton(display_button_text, callback_data=f"range_info_{session_id}")],
        [InlineKeyboardButton("✎ Edit Start", callback_data=f"range_edit_start_{session_id}"),
         InlineKeyboardButton("✎ Edit End", callback_data=f"range_edit_end_{session_id}")],
        [InlineKeyboardButton("⇄ Swap Order", callback_data=f"range_swap_{session_id}")],
        [InlineKeyboardButton("✓ Confirm Range", callback_data=confirm_cb)],
        [InlineKeyboardButton("« Cancel", callback_data=f"range_cancel_{session_id}")]]
    
    reply_markup = InlineKeyboardMarkup(buttons)
    message_id_to_edit = message_to_edit.id if message_to_edit else session.get('message_id')
    
    try:
        if message_id_to_edit:
            new_message = await bot.edit_message_text(
                chat_id=session['chat_id'], message_id=message_id_to_edit,
                text=text, reply_markup=reply_markup)
        else:
            new_message = await bot.send_message(
                chat_id=session['chat_id'], text=text, reply_markup=reply_markup,
                reply_to_message_id=session['original_message_id'])
        session['message_id'] = new_message.id
    except MessageNotModified: pass
    except Exception as e:
        logger.error(f"Error editing range message, sending new one: {e}", exc_info=False)
        try:
            new_message = await bot.send_message(
                chat_id=session['chat_id'], text=text, reply_markup=reply_markup,
                reply_to_message_id=session['original_message_id'])
            session['message_id'] = new_message.id
        except Exception as ie:
            logger.error(f"Failed to send fallback range message: {ie}")
