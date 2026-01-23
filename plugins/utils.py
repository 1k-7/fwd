import os
import logging
from pyrogram.errors import UserNotParticipant
from pyrogram.types import Message
from PIL import Image

logger = logging.getLogger(__name__)

# ... (Existing STS class and other utils remain here) ...

class STS:
    """
    Session Task Store (STS) - Manages forwarding task states and data.
    """
    def __init__(self, id):
        self.id = str(id)
        from config import temp
        if self.id not in temp.IS_FRWD_CHAT:
            temp.IS_FRWD_CHAT[self.id] = {
                'status': 'running',
                'fetched': 0,
                'total': 0,
                'total_files': 0,
                'failed': 0,
                'deleted': 0,
                'duplicate': 0,
                'filtered': 0,
                'start': 0,
                'start_id': 0,
                'end_id': 0,
                'From': 0,
                'to': 0
            }
        self.data = temp.IS_FRWD_CHAT

    def verify(self):
        return bool(self.data.get(self.id))

    def store(self, From, to, start_id, end_id):
        self.data[self.id].update({
            'From': From,
            'to': to,
            'start_id': int(start_id),
            'end_id': int(end_id),
            'start': 0  # Will be set when task actually starts
        })
        return self

    def get(self, full=False):
        class Struct:
            def __init__(self, **entries):
                self.__dict__.update(entries)
        
        if full:
            # Refresh data from dict
            return Struct(**self.data[self.id])
        return self.data[self.id]

    async def get_data(self, user_id, bot_id=None):
        from database import db
        # 1. Get Caption
        caption = await db.get_caption(user_id)
        
        # 2. Get Configs (filters, delay, thumbnail)
        configs = await db.get_configs(user_id)
        
        # 3. Get Bot
        bot = await db.get_bot(user_id, bot_id) if bot_id else None
        if not bot and bot_id: return None, None, None, None, None, None
        
        # 4. Button & Protect
        button = await db.get_button(user_id)
        protect = configs.get('protect_content', False) # Assuming this config exists or default False
        
        # 5. Forward Tag (Not used in new logic much, but kept for legacy)
        forward_tag = False 

        return bot, caption, forward_tag, configs, protect, button

    def set_status(self, status):
        self.data[self.id]['status'] = status

    def add(self, key, value=1):
        self.data[self.id][key] += value

    def get_readable_time(self, seconds):
        result = ""
        (days, remainder) = divmod(seconds, 86400)
        days = int(days)
        if days != 0: result += f"{days}d "
        (hours, remainder) = divmod(remainder, 3600)
        hours = int(hours)
        if hours != 0: result += f"{hours}h "
        (minutes, seconds) = divmod(remainder, 60)
        minutes = int(minutes)
        if minutes != 0: result += f"{minutes}m "
        seconds = int(seconds)
        result += f"{seconds}s"
        return result

# --- NEW HELPER FOR THUMBNAILS ---

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
        # Telegram Document thumbs are best at 320x320 max.
        # We resize preserving aspect ratio so the longest side is 320.
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

# --- EXISTING RANGE UTILS ---
# (Keeping these as they were in previous context, condensed for brevity)
async def start_range_selection(bot, message, from_chat_id, from_title, to_chat_id, start_id, end_id, mode="standard"):
    from config import temp
    from translation import Translation
    from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    
    session_id = str(message.id)
    temp.RANGE_SESSIONS[session_id] = {
        'user_id': message.chat.id,
        'from_chat_id': from_chat_id, 'from_title': from_title,
        'to_chat_id': to_chat_id,
        'start_id': start_id, 'end_id': end_id,
        'mode': mode,
        'order': 'asc',
        'final_callback': 'fwd_final'
    }
    await update_range_message(bot, session_id, message)

async def update_range_message(bot, session_id, message_to_edit=None):
    from config import temp
    from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    
    session = temp.RANGE_SESSIONS.get(session_id)
    if not session: return

    s, e = session['start_id'], session['end_id']
    start_fmt = f"{s} (First)" if s < e else f"{s} (Last)"
    end_fmt = f"{e} (Last)" if s < e else f"{e} (First)"
    
    text = (f"<b>Select Message Range</b>\n\n"
            f"<b>From:</b> {session['from_title']}\n"
            f"<b>Mode:</b> {session.get('mode', 'Standard').title()}\n"
            f"<b>Start ID:</b> <code>{s}</code>\n"
            f"<b>End ID:</b> <code>{e}</code>\n\n"
            f"<i>Current Range: {min(s, e)} - {max(s, e)}</i>")
    
    buttons = [
        [InlineKeyboardButton('Edit Start ID', callback_data=f"range_edit_start_{session_id}"),
         InlineKeyboardButton('Edit End ID', callback_data=f"range_edit_end_{session_id}")],
        [InlineKeyboardButton(f"Swap Order ({session['order'].upper()})", callback_data=f"range_swap_{session_id}")],
        [InlineKeyboardButton('Done ✓', callback_data=f"range_confirm_{session_id}"),
         InlineKeyboardButton('Cancel ✗', callback_data=f"range_cancel_{session_id}")]
    ]
    
    if message_to_edit:
        await message_to_edit.edit(text, reply_markup=InlineKeyboardMarkup(buttons))
    else:
        # Just send logic if needed, but usually we edit
        pass

def parse_buttons(text):
    return None # Placeholder
