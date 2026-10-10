"""Server hardening: webhook protection, malicious file blocking, account age gate.

Features (all toggleable via dashboard settings):
  1. Webhook protection — deletes messages sent via unauthorized webhooks.
     Webhooks created by admins are auto-approved; others are removed.
  2. Malicious file blocking — deletes attachments with dangerous extensions
     (.exe, .scr, .bat, .com, .pif, .vbs, .js, .jar, .msi, .ps1, .dll).
  3. Account age gate — deletes messages from accounts younger than the
     configured minimum age (default 3 days).

All actions are logged to the mod log. Staff (admin/manage) are exempt.
"""
from datetime import datetime, timezone

import discord
from discord.ext import commands

from utils import mod_log

# File extensions commonly used in malware / token stealers
BLOCKED_EXTENSIONS = {
    ".exe", ".scr", ".bat", ".com", ".pif", ".vbs", ".vbe", ".js", ".jse",
    ".wsf", ".wsh", ".msi", ".ps1", ".dll", ".jar", ".apk", ".dmg",
    ".pkg", ".deb", ".rpm", ".run", ".sh",
}

_DEFAULTS = {
    "webhook_protection": True,
    "malicious_file_block": True,
    "account_age_gate": True,
    "account_age_gate_days": 3,
}


def _s(guild_settings, key):
    v = guild_settings.get(key, _DEFAULTS[key])
    return v


class Hardening(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # guild_id -> set of authorized webhook IDs
        self.authorized_webhooks = set()

    def _is_staff(self, member):
        if member is None:
            return False
        p = member.guild_permissions
        return p.administrator or p.manage_guild or p.manage_webhooks

    @commands.Cog.listener()
    async def on_webhooks_update(self, channel):
        """When webhooks change, re-scan and remove unauthorized ones."""
        guild = channel.guild
        g = self.bot.store.guild(guild.id)
        s = g["settings"]
        if not _s(s, "webhook_protection"):
            return
        try:
            webhooks = await guild.webhooks()
        except (discord.Forbidden, discord.HTTPException):
            return
        for wh in webhooks:
            # Webhooks owned by the bot itself or created by admins are fine.
            # We can't easily tell who created a webhook, so we allow ones
            # that have been seen sending legitimate traffic (tracked below)
            # and remove any others that aren't the bot's own.
            if wh.id in self.authorized_webhooks:
                continue
            # Don't touch our own bot's webhooks
            if wh.user and wh.user.id == self.bot.user.id:
                self.authorized_webhooks.add(wh.id)
                continue
            # If the webhook creator is staff, authorize it
            try:
                if wh.user:
                    member = guild.get_member(wh.user.id)
                    if member and self._is_staff(member):
                        self.authorized_webhooks.add(wh.id)
                        continue
            except Exception:
                pass
            # Unauthorized webhook — delete it
            try:
                await wh.delete(reason="Lynxo Bot webhook protection")
                await mod_log(
                    self.bot, guild,
                    f"Deleted unauthorized webhook '{wh.name}' in "
                    f"{channel.mention}.",
                )
            except (discord.Forbidden, discord.HTTPException):
                pass

    @commands.Cog.listener()
    async def on_message(self, message):
        if not message.guild or message.author.bot:
            return
        guild = message.guild
        g = self.bot.store.guild(guild.id)
        s = g["settings"]
        author = message.author

        # Staff are exempt from all hardening checks
        if isinstance(author, discord.Member) and self._is_staff(author):
            # But still track their webhooks as authorized
            if message.webhook_id:
                self.authorized_webhooks.add(message.webhook_id)
            return

        # 1. Webhook protection — block messages from unauthorized webhooks
        if message.webhook_id and _s(s, "webhook_protection"):
            if message.webhook_id not in self.authorized_webhooks:
                try:
                    await message.delete()
                    await mod_log(
                        self.bot, guild,
                        f"Deleted message from unauthorized webhook in "
                        f"{message.channel.mention} (author: {author}).",
                    )
                except (discord.Forbidden, discord.HTTPException):
                    pass
                return

        # 2. Malicious file blocking
        if message.attachments and _s(s, "malicious_file_block"):
            for att in message.attachments:
                name = (att.filename or "").lower()
                if any(name.endswith(ext) for ext in BLOCKED_EXTENSIONS):
                    try:
                        await message.delete()
                        await mod_log(
                            self.bot, guild,
                            f"Deleted message from {author.mention} ({author}) "
                            f"with blocked file type: {att.filename} in "
                            f"{message.channel.mention}.",
                        )
                    except (discord.Forbidden, discord.HTTPException):
                        pass
                    return

        # 3. Account age gate
        if _s(s, "account_age_gate"):
            min_days = _s(s, "account_age_gate_days")
            try:
                min_days = int(min_days)
            except (TypeError, ValueError):
                min_days = 3
            age = datetime.now(timezone.utc) - author.created_at
            if age.days < min_days:
                try:
                    await message.delete()
                    await mod_log(
                        self.bot, guild,
                        f"Deleted message from {author.mention} ({author}) — "
                        f"account is only {age.days} day(s) old "
                        f"(minimum: {min_days}).",
                    )
                except (discord.Forbidden, discord.HTTPException):
                    pass
                return


async def setup(bot):
    await bot.add_cog(Hardening(bot))
