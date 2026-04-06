"""
CFD Smart Signal Bot — Production Entry Point

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
from aiogram.types import BotCommand

from bot.config import cfg
from bot.handlers import router
from bot.admin import admin_router
from bot.scanner import run_scan_loop
from bot.outcome_tracker import run_outcome_tracker


async def main():
    bot = Bot(
        token=cfg.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()
    dp.include_router(admin_router)  # admin first so owner commands take priority
    dp.include_router(router)

    await bot.set_my_commands([
        BotCommand(command="signal",      description="Live signal for a symbol. Usage: /signal XAUUSD"),
        BotCommand(command="watchlist",   description="Your symbols and current signals"),
        BotCommand(command="add",         description="Add a symbol. Usage: /add XAUUSD"),
        BotCommand(command="remove",      description="Remove a symbol. Usage: /remove XAUUSD"),
        BotCommand(command="symbols",     description="Browse 80+ instruments by category"),
        BotCommand(command="news",        description="Latest news for a symbol. Usage: /news XAUUSD"),
        BotCommand(command="history",     description="Your last 10 received signals"),
        BotCommand(command="performance", description="Your win rate and signal stats"),
        BotCommand(command="alerts",      description="Toggle auto alerts. Usage: /alerts on or off"),
        BotCommand(command="timeframe",   description="Change scan timeframe. Usage: /timeframe 1h"),
        BotCommand(command="confluence",  description="Signal sensitivity. Usage: /confluence 3"),
        BotCommand(command="settings",    description="View your current settings"),
        BotCommand(command="calendar",     description="Economic calendar — today's high impact events"),
        BotCommand(command="hours",        description="Market trading hours in Israel time"),
        BotCommand(command="help",        description="Show all commands"),
    ])

    asyncio.create_task(run_scan_loop(bot, cfg.SCAN_INTERVAL_MINUTES))
    asyncio.create_task(run_outcome_tracker(bot))

    logger.info(f"Bot started | Owner: {cfg.OWNER_CHAT_ID} | Scan: {cfg.SCAN_INTERVAL_MINUTES}m")
    try:
        await bot.send_message(
            cfg.OWNER_CHAT_ID,
            "🤖 <b>CFD Signal Bot is online.</b>\n\n"
            f"📡 Scanning every <b>{cfg.SCAN_INTERVAL_MINUTES} minutes</b>.\n"
            "Use /help to see all commands.",
        )
    except Exception as e:
        logger.warning(f"Could not send startup message: {e} — send /start to the bot first.")

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
