# MrSyD
# Telegram Channel @Bot_Cracker
# Developer @syd_xyz

import asyncio
import logging
import logging.config
import aiohttp
from datetime import datetime
from config import Config, temp
from database import db
from aiohttp import web
from plugins import web_server
from pyrogram import Client, __version__, idle
from pyrogram.raw.all import layer
from pyrogram.enums import ParseMode
from pyrogram.errors import FloodWait

logging.config.fileConfig('logging.conf')
logging.getLogger().setLevel(logging.INFO)
logging.getLogger("pyrogram").setLevel(logging.ERROR)

PORT = Config.PORT

class Bot(Client):
    def __init__(self):
        super().__init__(
            Config.BOT_SESSION,
            api_hash=Config.API_HASH,
            api_id=Config.API_ID,
            plugins={
                "root": "plugins"
            },
            bot_token=Config.BOT_TOKEN
        )
        self.log = logging

    async def start(self):
        try:
            await super().start()
        except FloodWait as e:
            self.log.warning(f"FloodWait on start: waiting for {e.value} seconds.")
            await asyncio.sleep(e.value)
            await super().start() # Retry start after waiting

        me = await self.get_me()
        logging.info(f"{me.first_name} with for pyrogram v{__version__} (Layer {layer}) started on @{me.username}.")
        self.id = me.id
        self.username = me.username
        self.first_name = me.first_name
        self.set_parse_mode(ParseMode.DEFAULT)

        # Load banned users on start
        temp.BANNED_USERS = await db.get_banned()

        # Start the web server and the ping task in the same process
        app_runner = web.AppRunner(await web_server())
        await app_runner.setup()
        bind_address = "0.0.0.0"
        site = web.TCPSite(app_runner, bind_address, PORT)
        await site.start()
        self.log.info(f"Web server started on {bind_address}:{PORT}")
        
        self.ping_task = asyncio.create_task(self.ping_server())
        
        # --- RESUME TASKS ---
        self.resume_task = asyncio.create_task(self.resume_tasks())

        # Keep the bot running
        await idle()
        logging.info("Bot has stopped.")

    async def stop(self, *args):
        if self.ping_task:
            self.ping_task.cancel()
        if self.resume_task:
            self.resume_task.cancel()
        msg = f"@{self.username} stopped. Bye."
        await super().stop()
        logging.info(msg)

    async def ping_server(self):
        await asyncio.sleep(60)
        while True:
            await asyncio.sleep(240)
            try:
                if Config.WEB_SERVER_URL:
                    async with aiohttp.ClientSession() as session:
                        async with session.get(Config.WEB_SERVER_URL) as resp:
                            self.log.info(f"Pinged server with status: {resp.status}")
                else:
                    self.log.warning("WEB_SERVER_URL is not set. Cannot ping server.")
            except Exception as e:
                self.log.error(f"Failed to ping server: {e}")

    async def resume_tasks(self):
        from plugins.regix import resume_forwarding
        self.log.info("Checking for unfinished tasks to resume...")
        await asyncio.sleep(5) # Wait for bot to fully initialize
        tasks_cursor = await db.get_all_tasks()
        resumed_count = 0
        async for task in tasks_cursor:
            try:
                self.log.info(f"Resuming task: {task.get('id')}")
                # Schedule the resume_forwarding coroutine to run
                asyncio.create_task(resume_forwarding(self, task))
                resumed_count += 1
            except Exception as e:
                self.log.error(f"Failed to resume task {task.get('id')}: {e}")
                await db.delete_task(task.get('id')) # Delete broken task
        if resumed_count > 0:
            self.log.info(f"Successfully resumed {resumed_count} tasks.")

# MrSyD
# Telegram Channel @Bot_Cracker
# Developer @syd_xyz
