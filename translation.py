# mistaldrin/fwd/fwd-dawn-improve-v2/translation.py
import os
from config import Config

class Translation(object):
  START_TXT = """Hello there, {}.

A personal assistant for message forwarding.

Select 'Help' for a list of commands.
(o･ω･o)"""


  HELP_TXT = """<b>֎ Help Menu ֎</b>

Available commands:

● /start - Check if alive.
● /forward - Forward a range of messages.
● /unequify - Remove duplicates from a chat.
● /tasks - View and manage active tasks.
● /ubclist - List all userbot chats.
● /settings - Open the configuration menu.
● /resetme - A fresh start. Wipes all settings.
● /forwardelay - Set a custom forward delay.

<b>Features:</b>
▸ Custom message ranges.
▸ Interactive chat selection.
▸ Forwards from public & private channels.
▸ Custom captions & buttons.
▸ Skips duplicates automatically.
▸ High-speed concurrent forwarding with adjustable delay.
"""
  
  HOW_USE_TXT = """<b>֎ How to Use ֎</b>

A quick checklist to get started:

1.  <b>Add Bot/Userbot:</b> Add a bot token or userbot session in /settings.
2.  <b>Add Channels:</b> Add target channels where the bot/userbot is an admin.
3.  <b>Check Permissions:</b>
    - Userbot must be in private source channels.
    - Bot/userbot needs admin rights in target channels.

Ready? Use /forward to begin. (ﾉ´ヮ`)ﾉ*:･ﾟ✧"""
  
  ABOUT_TXT = """<b>֎ About ֎</b>

● <b>Name:</b> {}
● <b>Language:</b> Python
● <b>Library:</b> Pyrogram
● <b>Developer:</b> <a href='https://t.me/partDevil'>partDevil</a>"""
  
  STATUS_TXT = """<b>֎ Bot Status ֎</b>
  
● <b>Total Users:</b> <code>{}</code>
● <b>Total Bots & Userbots:</b> <code>{}</code>
● <b>Active Forwards:</b> <code>{}</code>
"""
  
  FROM_MSG = "<b>Source Chat?</b>\n\nTo set the range, forward the <b>last message</b> you want to forward from the source chat, or send its link.\n\nThe range will start from the beginning (message 1) by default.\n\n/cancel - Abort mission."
  TO_MSG = "<b>Target Chat?</b>\n\nSelect a target chat from the buttons below.\n\n/cancel - Abort mission."
  
  RANGE_SELECTION_TXT = """<b>֎ Message Range ֎</b>

The range is set from message <code>{start}</code> to <code>{end}</code>.

You can confirm to start, or edit the range below."""

  UNEQUIFY_START_TXT = """<b>֎ Deduplicator ֎</b>

Select the target channel.

You can use /ubclist to find the Channel ID.

Manual input: `/unequify [channel_username_or_id]`"""
  
  CANCEL = "Process cancelled. (o˘◡˘o)"
  BOT_DETAILS = "<b>֎ Bot Details ֎</b>\n\n● <b>Name:</b> <code>{}</code>\n● <b>ID:</b> <code>{}</code>\n● <b>Username:</b> {}"
  USER_DETAILS = "<b>֎ Userbot Details ֎</b>\n\n● <b>Name:</b> <code>{}</code>\n● <b>ID:</b> <code>{}</code>\n● <b>Username:</b> {}"  
         
  TEXT = """<b>Status:</b> <code>{status}</code>

<b>Fetched:</b> <code>{fetched} / {total}</code>
<b>Forwarded:</b> <code>{forwarded}</code>
<b>Skipped:</b> <code>{skipped}</code>
<b>Failed:</b> <code>{failed}</code>
<b>Duplicates:</b> <code>{duplicates}</code>

{progress_bar}
<b>Progress:</b> <code>{percentage}%</code>
<b>ETA:</b> <code>{eta}</code>
"""

  UNEQUIFY_TEXT = """<b>Status:</b> <code>{status}</code>

<b>Scanned:</b> <code>{scanned} / {total}</code>
<b>Deleted:</b> <code>{deleted}</code>

{progress_bar}
<b>Progress:</b> <code>{percentage}%</code>
<b>ETA:</b> <code>{eta}</code>
"""

  # --- Corrected Status Alerts (Concise for Telegram's 200 character popup limit) ---

  STATUS_ALERT = """Processed: {fetched}/{total} ({percentage}%)
Forwarded: {forwarded} | Failed: {failed}
Skipped: {skipped} | Status: {status}
ETA: {eta}"""
  
  UNEQUIFY_STATUS_ALERT = """Scanned: {scanned}/{total} ({percentage}%)
Deleted: {deleted} | Status: {status}
ETA: {eta}"""
  
  # --------------------------------------------------------------------------
  
  DOUBLE_CHECK = """<b>֎ Final Check ֎</b>

Here's the plan:

● <b>Using:</b> [{botname}](t.me/{botuname})
● <b>From:</b> <code>{from_chat}</code>
● <b>To:</b> <code>{to_chat}</code>
● <b>Range:</b> <code>{message_range}</code>

<i>Ensure [{botname}](t.me/{botuname}) is an admin in the target chat!</i>

<b>Proceed?</b>"""
  
  FORWARDELAY_TXT = """<b>֎ Forward Delay ֎</b>

Set a custom delay between forwards. Helps avoid API limits.

<b>Current Delay:</b> <code>{current_delay} seconds</code>

<b>Usage:</b> `/forwardelay [seconds]`
<b>Example:</b> `/forwardelay 0.5`

Default is 0.5 seconds in high-speed mode."""
