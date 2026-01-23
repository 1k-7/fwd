import os

class Config:
    API_ID = os.environ.get("API_ID", "")
    API_HASH = os.environ.get("API_HASH", "")
    BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
    BOT_SESSION = os.environ.get("BOT_SESSION", "forward-bot")
    DB_URL = os.environ.get("DB_URL", "")
    PORT = os.environ.get("PORT", "8080")
    DB_NAME = os.environ.get("DB_NAME", "cluster0")
    OWNER_ID = [int(id) for id in os.environ.get("OWNER_ID", '').split()]
    WEB_SERVER_URL = os.environ.get("WEB_SERVER_URL", "") # Add your Render URL here


class temp(object):
    lock = {}
    CANCEL = {}
    forwardings = 0
    BANNED_USERS = []
    SUDO_USERS = [] # Fixed: Added missing attribute
    IS_FRWD_CHAT = []
    # Dictionaries for managing interactive sessions
    RANGE_SESSIONS = {}
    USER_STATES = {} # For stateful conversations
    # For tracking active tasks
    ACTIVE_TASKS = {} # {user_id: {task_id: {"process": message_obj, "details": {}}}}
    # User-specific bot selections for concurrent operations
    FORWARD_BOT_ID = {}
    UNEQUIFY_USERBOT_ID = {}

