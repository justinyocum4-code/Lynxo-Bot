"""Shared helpers: mod logging, timestamps, DMs."""
from datetime import datetime
from zoneinfo import ZoneInfo

import discord

CT = ZoneInfo("America/Chicago")


def now_str():
    return datetime.now(CT).strftime("%Y-%m-%d %I:%M %p CDT")


async def mod_log(bot, guild, text):
    """Post a plain-English line to the guild's #mod-logs channel, if set up."""
    g = bot.store.guild(guild.id)
    # Keep an in-memory copy for the dashboard API (recent first, capped).
    buf = getattr(bot, "recent_logs", None)
    if buf is not None:
        buf[guild.id].append({"ts": now_str(), "text": text})
    cid = g["channels"].get("mod_logs")
    if not cid:
        return
    ch = guild.get_channel(cid)
    if ch is None:
        return
    try:
        await ch.send(f"[{now_str()}] {text}")
    except discord.HTTPException:
        pass


async def dm_user(user, text):
    """Try to DM a user. Returns True on success, False if DMs are closed."""
    try:
        await user.send(text)
        return True
    except discord.HTTPException:
        return False


def user_label(member):
    return f"{member} ({member.id})"
