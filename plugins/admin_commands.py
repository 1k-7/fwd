import asyncio
from pyrogram import Client, filters
from pyrogram.types import Message
from config import Config, temp
from database import db

def is_admin(user_id):
    return user_id in Config.OWNER_ID or user_id in temp.SUDO_USERS

@Client.on_message(filters.command("addsudo") & filters.user(Config.OWNER_ID))
async def add_sudo(client: Client, message: Message):
    """
    Usage: /addsudo [user_id | username | reply_to_message]
    """
    if not message.reply_to_message and len(message.command) == 1:
        return await message.reply_text("Reply to a user, or provide a user ID/username to add them to the sudo users list.")

    try:
        if message.reply_to_message:
            user_id = message.reply_to_message.from_user.id
            user_first_name = message.reply_to_message.from_user.first_name
        else:
            if len(message.command) > 1:
                user_input = message.command[1]
                if user_input.isdigit():
                    user = await client.get_users(int(user_input))
                    user_id = user.id
                    user_first_name = user.first_name
                else:
                    user = await client.get_users(user_input)
                    user_id = user.id
                    user_first_name = user.first_name
            else:
                return await message.reply_text("Please provide a user ID or username.")

        if user_id in Config.OWNER_ID:
            return await message.reply_text(f"**{user_first_name}** is already the bot owner.")
        
        if user_id in temp.SUDO_USERS:
            return await message.reply_text(f"**{user_first_name}** is already a sudo user.")

        temp.SUDO_USERS.append(user_id)
        await message.reply_text(f"**{user_first_name}** has been added to the sudo users list.")

    except Exception as e:
        await message.reply_text(str(e))


@Client.on_message(filters.command("rmsudo") & filters.user(Config.OWNER_ID))
async def rm_sudo(client: Client, message: Message):
    """
    Usage: /rmsudo [user_id | username | reply_to_message]
    """
    if not message.reply_to_message and len(message.command) == 1:
        return await message.reply_text("Reply to a user, or provide a user ID/username to remove them from the sudo users list.")

    try:
        if message.reply_to_message:
            user_id = message.reply_to_message.from_user.id
            user_first_name = message.reply_to_message.from_user.first_name
        else:
            if len(message.command) > 1:
                user_input = message.command[1]
                if user_input.isdigit():
                    user = await client.get_users(int(user_input))
                    user_id = user.id
                    user_first_name = user.first_name
                else:
                    user = await client.get_users(user_input)
                    user_id = user.id
                    user_first_name = user.first_name
            else:
                return await message.reply_text("Please provide a user ID or username.")

        if user_id not in temp.SUDO_USERS:
            return await message.reply_text(f"**{user_first_name}** is not a sudo user.")

        temp.SUDO_USERS.remove(user_id)
        await message.reply_text(f"**{user_first_name}** has been removed from the sudo users list.")

    except Exception as e:
        await message.reply_text(str(e))

@Client.on_message(filters.command("ban"))
async def ban_user(client: Client, message: Message):
    if not is_admin(message.from_user.id):
        return await message.reply_text("You don't have permission to use this command.")
    
    if not message.reply_to_message and len(message.command) == 1:
        return await message.reply_text("Reply to a user or provide a user ID/username to ban.")

    reason = ""
    if len(message.command) > 1 and not message.command[1].isdigit() and not message.command[1].startswith('@'):
        reason = " ".join(message.command[1:])

    try:
        user_to_ban = None
        if message.reply_to_message:
            user_to_ban = message.reply_to_message.from_user
        else:
            user_input = message.command[1] if reason == "" else message.command[1]
            if user_input.isdigit():
                 user_to_ban = await client.get_users(int(user_input))
            else:
                 user_to_ban = await client.get_users(user_input)

        if not user_to_ban:
            return await message.reply_text("Cannot find the user.")

        if user_to_ban.id in Config.OWNER_ID or user_to_ban.id in temp.SUDO_USERS:
            return await message.reply_text("I can't ban an admin!")

        await db.ban_user(user_to_ban.id, reason)
        temp.BANNED_USERS.append(user_to_ban.id)
        await message.reply_text(f"**{user_to_ban.first_name}** has been banned. Reason: {reason or 'No reason provided.'}")
    
    except Exception as e:
        await message.reply_text(f"An error occurred: {e}")

@Client.on_message(filters.command("unban"))
async def unban_user(client: Client, message: Message):
    if not is_admin(message.from_user.id):
        return await message.reply_text("You don't have permission to use this command.")

    if not message.reply_to_message and len(message.command) == 1:
        return await message.reply_text("Reply to a user or provide a user ID/username to unban.")

    try:
        user_to_unban = None
        if message.reply_to_message:
            user_to_unban = message.reply_to_message.from_user
        else:
            user_input = message.command[1]
            if user_input.isdigit():
                 user_to_unban = await client.get_users(int(user_input))
            else:
                 user_to_unban = await client.get_users(user_input)
        
        if not user_to_unban:
            return await message.reply_text("Cannot find the user.")
            
        await db.remove_ban(user_to_unban.id)
        if user_to_unban.id in temp.BANNED_USERS:
            temp.BANNED_USERS.remove(user_to_unban.id)
        await message.reply_text(f"**{user_to_unban.first_name}** has been unbanned.")

    except Exception as e:
        await message.reply_text(f"An error occurred: {e}")
