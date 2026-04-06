"""
Admin commands — owner only.

/users       — list all users with status
/approve ID  — grant premium access
/revoke ID   — remove premium access
/broadcast MSG — send a message to all premium users
"""

import logging

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.config import cfg
from bot.db.session import AsyncSessionLocal
from bot.db.repositories.user_repo import get_all_users, set_premium

logger = logging.getLogger(__name__)
admin_router = Router()


def _is_owner(message: Message) -> bool:
    return message.from_user.id == cfg.OWNER_CHAT_ID


# ── /users ────────────────────────────────────────────────────────────────────

@admin_router.message(Command("users"))
async def cmd_users(message: Message):
    if not _is_owner(message):
        return

    async with AsyncSessionLocal() as session:
        users = await get_all_users(session)

    if not users:
        await message.answer("No users yet.", parse_mode="HTML")
        return

    lines = [f"<b>Members ({len(users)})</b>\n"]
    for u in users:
        status = "Owner" if u.id == cfg.OWNER_CHAT_ID else ("Active" if u.is_premium else "Pending")
        name = u.first_name or u.username or "Unknown"
        username = f"  @{u.username}" if u.username else ""
        lines.append(f"{name}{username}  -  {status}  -  /approve {u.id}")

    await message.answer("\n".join(lines), parse_mode="HTML")


# ── /approve ──────────────────────────────────────────────────────────────────

@admin_router.message(Command("approve"))
async def cmd_approve(message: Message):
    if not _is_owner(message):
        return

    parts = message.text.split()
    if len(parts) < 2 or not parts[1].isdigit():
        await message.answer("Usage: /approve USER_ID", parse_mode="HTML")
        return

    user_id = int(parts[1])
    async with AsyncSessionLocal() as session:
        found = await set_premium(session, user_id, True)

    if found:
        await message.answer(f"User <code>{user_id}</code> approved.", parse_mode="HTML")
        try:
            await message.bot.send_message(
                user_id,
                "✅ <b>Access Granted</b>\n\n"
                "Your request has been approved.\n"
                "Use /help to see all available commands.",
                parse_mode="HTML",
            )
        except Exception:
            pass
    else:
        await message.answer(f"User <code>{user_id}</code> not found. They must /start the bot first.", parse_mode="HTML")


# ── /revoke ───────────────────────────────────────────────────────────────────

@admin_router.message(Command("revoke"))
async def cmd_revoke(message: Message):
    if not _is_owner(message):
        return

    parts = message.text.split()
    if len(parts) < 2 or not parts[1].isdigit():
        await message.answer("Usage: /revoke USER_ID", parse_mode="HTML")
        return

    user_id = int(parts[1])
    async with AsyncSessionLocal() as session:
        found = await set_premium(session, user_id, False)

    if found:
        await message.answer(f"Access revoked for <code>{user_id}</code>.", parse_mode="HTML")
        try:
            await message.bot.send_message(
                user_id,
                "Your access to CFD Signal Bot has been revoked.",
                parse_mode="HTML",
            )
        except Exception:
            pass
    else:
        await message.answer(f"User <code>{user_id}</code> not found.", parse_mode="HTML")


# ── /broadcast ────────────────────────────────────────────────────────────────

@admin_router.message(Command("broadcast"))
async def cmd_broadcast(message: Message):
    if not _is_owner(message):
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Usage: /broadcast Your message here", parse_mode="HTML")
        return

    text = parts[1]
    async with AsyncSessionLocal() as session:
        users = await get_all_users(session)

    recipients = [u for u in users if u.is_premium or u.id == cfg.OWNER_CHAT_ID]
    sent = 0
    for u in recipients:
        try:
            await message.bot.send_message(u.id, f"📢 {text}", parse_mode="HTML")
            sent += 1
        except Exception:
            pass

    await message.answer(f"Broadcast sent to <b>{sent}</b> users.", parse_mode="HTML")


# ── Approve/Reject via inline buttons (from access requests) ──────────────────

@admin_router.callback_query(F.data.startswith("approve_user:"))
async def cb_approve_user(callback: CallbackQuery):
    if callback.from_user.id != cfg.OWNER_CHAT_ID:
        await callback.answer("Not authorized.", show_alert=True)
        return

    user_id = int(callback.data.split(":")[1])
    async with AsyncSessionLocal() as session:
        found = await set_premium(session, user_id, True)

    if found:
        await callback.message.edit_text(
            callback.message.text + "\n\n✅ <b>Approved</b>",
            parse_mode="HTML",
        )
        try:
            await callback.bot.send_message(
                user_id,
                "✅ <b>Access Granted</b>\n\n"
                "Your request has been approved.\n"
                "Use /help to get started.",
                parse_mode="HTML",
            )
        except Exception:
            pass
    await callback.answer("Access approved.")


@admin_router.callback_query(F.data.startswith("reject_user:"))
async def cb_reject_user(callback: CallbackQuery):
    if callback.from_user.id != cfg.OWNER_CHAT_ID:
        await callback.answer("Not authorized.", show_alert=True)
        return

    user_id = int(callback.data.split(":")[1])
    await callback.message.edit_text(
        callback.message.text + "\n\n❌ <b>Rejected</b>",
        parse_mode="HTML",
    )
    try:
        await callback.bot.send_message(
            user_id,
            "Sorry, your access request was not approved at this time.",
        )
    except Exception:
        pass
    await callback.answer("Rejected.")
