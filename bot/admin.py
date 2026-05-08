"""
Admin commands: owner only.

/admin         : owner control panel
/users         : list all users who have messaged the bot
/approve       : pick a user from a list, or /approve @username / /approve ID
"""

import logging

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.config import cfg
from bot.db.session import AsyncSessionLocal
from bot.db.repositories.user_repo import get_all_users, get_user_by_username

logger = logging.getLogger(__name__)
admin_router = Router()


def _is_owner(uid: int) -> bool:
    return uid == cfg.OWNER_CHAT_ID


def _user_display(user) -> str:
    label = user.first_name or user.username or str(user.id)
    if user.username:
        label += f" (@{user.username})"
    return label


def _user_status(user) -> str:
    if getattr(user, "is_premium", False):
        return "Premium"
    if getattr(user, "is_invited", False):
        return "Invited"
    return "Pending"


# /admin
@admin_router.message(Command("admin"))
async def cmd_admin(message: Message):
    if not _is_owner(message.from_user.id):
        return

    async with AsyncSessionLocal() as session:
        users = await get_all_users(session)

    non_owner = [u for u in users if u.id != cfg.OWNER_CHAT_ID]
    pending = sum(1 for u in non_owner if not getattr(u, "is_invited", False))
    invited = sum(1 for u in non_owner if getattr(u, "is_invited", False))

    builder = InlineKeyboardBuilder()
    builder.button(text="👥 Users", callback_data="home:users")
    builder.button(text="✉️ Invite", callback_data="home:approve")
    builder.button(text="📊 Stats", callback_data="home:stats")
    builder.button(text="📜 History", callback_data="home:history")
    builder.button(text="📲 Share Link", callback_data="home:sharelink")
    builder.button(text="🏠 Home", callback_data="home:menu")
    builder.adjust(2)

    await message.answer(
        "⚙️ <b>Admin Panel</b>\n\n"
        f"Users: <b>{len(non_owner)}</b>\n"
        f"Pending: <b>{pending}</b>\n"
        f"Invited: <b>{invited}</b>\n\n"
        "<b>Commands</b>\n"
        "/users  Manage subscribers\n"
        "/approve @username  Send invite\n"
        "/stats  Performance\n"
        "/history  Signal history",
        parse_mode="HTML",
        reply_markup=builder.as_markup(),
    )


# /users
@admin_router.message(Command("users"))
async def cmd_users(message: Message):
    if not _is_owner(message.from_user.id):
        return

    async with AsyncSessionLocal() as session:
        users = await get_all_users(session)

    non_owner = [u for u in users if u.id != cfg.OWNER_CHAT_ID]
    if not non_owner:
        builder = InlineKeyboardBuilder()
        builder.button(text="🏠 Home", callback_data="home:menu")
        await message.answer(
            "👥 <b>Users</b>\n\nNo users yet.\n\nAsk them to open the bot and tap Start.",
            parse_mode="HTML",
            reply_markup=builder.as_markup(),
        )
        return

    lines = [f"👥 <b>Users ({len(non_owner)})</b>", ""]
    pending = [u for u in non_owner if not getattr(u, "is_invited", False)]
    approved = [u for u in non_owner if getattr(u, "is_invited", False)]
    lines.append(f"Pending: <b>{len(pending)}</b>")
    lines.append(f"Invited: <b>{len(approved)}</b>")
    lines.append("")
    lines.append("Tap Invite, Kick, or Delete below.")
    for u in non_owner[:12]:
        lines.append(f"{'✅' if getattr(u, 'is_invited', False) else '⏳'} {_user_display(u)}  <i>{_user_status(u)}</i>")

    builder = InlineKeyboardBuilder()
    for u in non_owner:
        status = "✅" if getattr(u, "is_invited", False) else "⏳"
        label = _user_display(u)
        builder.button(text=f"{status} {label}", callback_data=f"approve_user:{u.id}")
        builder.button(text="🚫 Kick", callback_data=f"kick_user:{u.id}")
        builder.button(text="🗑 Delete", callback_data=f"delete_user:{u.id}")
    builder.button(text="🏠 Home", callback_data="home:menu")
    builder.adjust(*([3] * len(non_owner) + [1]))

    await message.answer("\n".join(lines), parse_mode="HTML", reply_markup=builder.as_markup())


# /approve — direct approve or picker
@admin_router.message(Command("approve"))
async def cmd_approve(message: Message):
    if not _is_owner(message.from_user.id):
        return

    parts = message.text.split(maxsplit=1)

    # /approve @username or /approve 123456789
    if len(parts) == 2:
        arg = parts[1].strip()
        from bot.handlers import _send_invite
        async with AsyncSessionLocal() as session:
            if arg.startswith("@") or not arg.isdigit():
                db_user = await get_user_by_username(session, arg)
            else:
                from sqlalchemy import select
                from bot.db.models.user import User
                result = await session.execute(select(User).where(User.id == int(arg)))
                db_user = result.scalar_one_or_none()

        if not db_user:
            await message.answer(
                f"❌ User <code>{arg}</code> not found. They must start the bot first.",
                parse_mode="HTML",
            )
            return

        display = db_user.first_name or f"@{db_user.username}" or str(db_user.id)
        try:
            invite_link = await _send_invite(message.bot, db_user.id)
            await message.answer(
                f"✅ <b>Invite sent to {display}</b>\n\n"
                f"Link (in case they need it manually):\n{invite_link}",
                parse_mode="HTML",
            )
        except Exception as e:
            await message.answer(f"❌ Failed: <code>{e}</code>", parse_mode="HTML")
        return

    # /approve with no args — show picker
    async with AsyncSessionLocal() as session:
        users = await get_all_users(session)

    non_owner = [u for u in users if u.id != cfg.OWNER_CHAT_ID]
    if not non_owner:
        builder = InlineKeyboardBuilder()
        builder.button(text="🏠 Home", callback_data="home:menu")
        await message.answer("👥 <b>Users</b>\n\nNo users yet. Ask them to open the bot and tap Start.", parse_mode="HTML", reply_markup=builder.as_markup())
        return

    builder = InlineKeyboardBuilder()
    for u in non_owner:
        status = "✅" if u.is_invited else "⏳"
        label = u.first_name or u.username or str(u.id)
        if u.username:
            label += f" (@{u.username})"
        builder.button(text=f"✉️ {status} {label}", callback_data=f"approve_user:{u.id}")
    builder.button(text="🏠 Home", callback_data="home:menu")
    builder.adjust(1)

    await message.answer(
        f"👥 <b>Users ({len(non_owner)})</b>\n\nTap a user to send them the channel invite:",
        reply_markup=builder.as_markup(),
        parse_mode="HTML",
    )
