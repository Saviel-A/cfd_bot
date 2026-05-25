"""
CFD Smart Signal Bot: Production Entry Point

Usage:
    .venv/bin/python bot_app.py
"""

import asyncio
import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import (
    BotCommand,
    BotCommandScopeDefault,
    BotCommandScopeAllPrivateChats,
    BotCommandScopeAllGroupChats,
    BotCommandScopeChat,
)

from bot.config import cfg
from bot.handlers import router, set_bot_username
from bot.admin import admin_router
from bot.scanner import run_scan_loop
from bot.outcome_tracker import run_outcome_tracker


async def main():
    bot = Bot(
        token=cfg.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()
    dp.include_router(admin_router)
    dp.include_router(router)

    # Clear all scopes to remove stale cached commands
    for scope in (BotCommandScopeDefault(), BotCommandScopeAllPrivateChats(), BotCommandScopeAllGroupChats()):
        try:
            await bot.delete_my_commands(scope=scope)
        except Exception:
            pass

    user_commands = [
        BotCommand(command="start", description="Subscribe"),
    ]
    owner_commands = [
        BotCommand(command="start", description="Owner Console"),
        BotCommand(command="help", description="Owner Console"),
        BotCommand(command="signal", description="Check Signal"),
        BotCommand(command="scan", description="Scan Channel"),
        BotCommand(command="chart", description="Chart"),
        BotCommand(command="stats", description="Stats"),
        BotCommand(command="history", description="History"),
        BotCommand(command="watchlist", description="Watchlist"),
        BotCommand(command="symbols", description="Instruments"),
        BotCommand(command="add", description="Add Symbol"),
        BotCommand(command="remove", description="Remove Symbol"),
        BotCommand(command="market", description="News"),
        BotCommand(command="calendar", description="Calendar"),
        BotCommand(command="hours", description="Market Hours"),
        BotCommand(command="users", description="Subscribers"),
        BotCommand(command="approve", description="Invite Subscriber"),
    ]
    await bot.set_my_commands(user_commands, scope=BotCommandScopeDefault())
    await bot.set_my_commands(user_commands, scope=BotCommandScopeAllPrivateChats())
    await bot.set_my_commands(owner_commands, scope=BotCommandScopeChat(chat_id=cfg.OWNER_CHAT_ID))

    # Store bot username for deep link generation
    me = await bot.get_me()
    set_bot_username(me.username)
    logger.info(f"Bot started | @{me.username} | Owner: {cfg.OWNER_CHAT_ID} | Scan: {cfg.SCAN_INTERVAL_MINUTES}m | Channel: {cfg.BROADCAST_CHANNEL_ID}")

    share_link = f"https://t.me/{me.username}" if me.username else ""

    try:
        from bot.handlers import _menu_markup_main, _commands_text
        name = "Owner"
        await bot.send_message(
            cfg.OWNER_CHAT_ID,
            f"🤖 <b>CFD Bot Online</b>\n\n"
            f"Scan: every <b>{cfg.SCAN_INTERVAL_MINUTES}m</b>\n"
            f"Channel: <code>{cfg.BROADCAST_CHANNEL_ID}</code>\n"
            f"Link: <code>{share_link}</code>",
            reply_markup=_menu_markup_main(),
        )
    except Exception as e:
        logger.warning(f"Could not send startup message: {e}")

    asyncio.create_task(run_scan_loop(bot, cfg.SCAN_INTERVAL_MINUTES))
    asyncio.create_task(run_outcome_tracker(bot))

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
