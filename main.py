# main.py
from bot import Bot
# Database is now initialized on import in database.py
# No need to call initialize_database() here.

if __name__ == "__main__":
    # Create and run the bot instance.
    app = Bot()
    app.run()