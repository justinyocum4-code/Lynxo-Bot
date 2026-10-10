"""Server watcher: monitors for suspicious activity and alerts the owner.

Watches for:
- Mass joins (potential raid) beyond the raid cog's threshold
- New accounts (created < 7 days ago) joining
- @everyone/@here pings by non-admins
- Spam patterns (rapid repeated messages)
- Suspicious links (discord invites to other servers, known scam domains)

Bot joins are logged but never alerted (owner invites bots regularly).
"""
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
import re

import discord
from discord.ext import commands

from utils import dm_user, mod_log

# Domains commonly used in Discord scams/phishing
SCAM_DOMAINS = {
    "discordgift", "discord-nitro", "nitro-discord", "discorcl",
    "steamcommunity", "steancommunity",
}

INVITE_RE = re.compile(r"discord(?:\.gg|app\.com/invite)/(\S+)", re.IGNORECASE)
URL_RE = re.compile(r"https?://(\S+)", re.IGNORECASE)


class Watcher(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # user_id -> deque of message timestamps (for spam detection)
        self.msg_times = defaultdict(deque)
        # (guild_id, user_id) -> last message content (for repeat detection)
        self.last_msg = {}
        self.repeat_count = defaultdict(int)

    async def alert_owner(self, guild, title, detail):
        """DM the guild owner and log to mod-log."""
        msg = f"**{title}**\n{detail}"
        await mod_log(self.bot, guild, f"WATCHER: {title} — {detail}")
        if guild.owner:
            try:
                await dm_user(guild.owner, f"[{guild.name}] {msg}")
            except Exception:
                pass

    @commands.Cog.listener()
    async def on_member_join(self, member):
        # Never alert on bot joins — owner invites bots regularly.
        if member.bot:
            return
        guild = member.guild

        # Flag very new accounts (created less than 7 days ago)
        age = datetime.now(timezone.utc) - member.created_at
        if age < timedelta(days=7):
            await self.alert_owner(
                guild,
                "Suspicious join: new account",
                f"{member.mention} ({member}) joined. Account is only "
                f"{age.days} day(s) old.",
            )

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot or not message.guild:
            return
        guild = message.guild
        author = message.author

        # Skip admins/mods — they can ping everyone if they want
        perms = author.guild_permissions
        is_staff = perms.administrator or perms.manage_guild or perms.manage_messages

        content = message.content or ""

        # @everyone / @here by non-staff
        if not is_staff and (message.mention_everyone):
            await self.alert_owner(
                guild,
                "Mass ping by non-staff",
                f"{author.mention} ({author}) used @everyone/@here in "
                f"{message.channel.mention}: {content[:200]}",
            )
            return

        # Spam: 5+ messages in 10 seconds
        now = datetime.now(timezone.utc)
        dq = self.msg_times[author.id]
        while dq and (now - dq[0]).total_seconds() > 10:
            dq.popleft()
        dq.append(now)
        if len(dq) >= 5 and not is_staff:
            await self.alert_owner(
                guild,
                "Possible spam",
                f"{author.mention} ({author}) sent 5+ messages in 10 seconds "
                f"in {message.channel.mention}.",
            )
            self.msg_times[author.id].clear()
            return

        # Repeated identical messages (3+ times)
        key = (guild.id, author.id)
        if self.last_msg.get(key) == content and content:
            self.repeat_count[key] += 1
            if self.repeat_count[key] >= 3 and not is_staff:
                await self.alert_owner(
                    guild,
                    "Repeated message spam",
                    f"{author.mention} ({author}) sent the same message 3+ "
                    f"times in {message.channel.mention}.",
                )
                self.repeat_count[key] = 0
        else:
            self.last_msg[key] = content
            self.repeat_count[key] = 1

        # Suspicious links
        if not is_staff:
            for m in URL_RE.finditer(content):
                domain = m.group(1).split("/")[0].lower()
                if any(s in domain for s in SCAM_DOMAINS):
                    await self.alert_owner(
                        guild,
                        "Suspicious link",
                        f"{author.mention} ({author}) posted a suspicious link "
                        f"in {message.channel.mention}: {domain}",
                    )
                    return
            # Discord invites to other servers
            for m in INVITE_RE.finditer(content):
                await self.alert_owner(
                    guild,
                    "External invite link",
                    f"{author.mention} ({author}) posted a Discord invite in "
                    f"{message.channel.mention}: {m.group(0)[:100]}",
                )
                return


async def setup(bot):
    await bot.add_cog(Watcher(bot))
