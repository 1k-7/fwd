from database import initialize_database

# Initialize the database BEFORE importing other modules that depend on it.
initialize_database()

from bot import Bot

if __name__ == "__main__":
    # Create and run the bot instance.
    app = Bot()
    app.run()
