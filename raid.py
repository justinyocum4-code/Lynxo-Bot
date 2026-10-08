"""Anti-raid: watches join rate, auto-locks down on spikes."""
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

import discord
from discord.ext import commands

from utils import dm_user, mod_log


class Raid(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.joins = defaultdict(deque)   # guild_id -> deque of join datetimes
        self.lockdown_until = {}          # guild_id -> datetime

    def is_lockdown(self, guild_id):
        until = self.lockdown_until.get(guild_id)
        return bool(until and datetime.now(timezone.utc) < until)

    @commands.Cog.listener()
    async def on_member_join(self, member):
        guild = member.guild
        g = self.bot.store.guild(guild.id)
        s = g["settings"]
        now = datetime.now(timezone.utc)
        dq = self.joins[guild.id]
        window = s["raid_join_window"]
        while dq and (now - dq[0]).total_seconds() > window:
            dq.popleft()
        dq.append(now)
        if len(dq) >= s["raid_join_threshold"] and not self.is_lockdown(guild.id):
            await self.trigger(guild, len(dq))

    async def trigger(self, guild, count):
        g = self.bot.store.guild(guild.id)
        s = g["settings"]
        self.lockdown_until[guild.id] = (
            datetime.now(timezone.utc) + timedelta(minutes=s["raid_lockdown_minutes"])
        )
        self.bot.store.save()

        deleted = 0
        try:
            for invite in await guild.invites():
                try:
                    await invite.delete(reason="Lynxo Bot anti-raid lockdown")
                    deleted += 1
                except discord.HTTPException:
                    pass
        except (discord.Forbidden, discord.HTTPException):
            pass

        msg = (
            f"RAID PROTECTION TRIGGERED — {count} joins in {s['raid_join_window']} "
            f"seconds. Lockdown is on for {s['raid_lockdown_minutes']} minutes: "
            f"deleted {deleted} invite(s), verification buttons are paused. "
            f"Please review the recent joins."
        )
        await mod_log(self.bot, guild, msg)
        if guild.owner:
            await dm_user(guild.owner, f"[{guild.name}] {msg}")


async def setup(bot):
    await bot.add_cog(Raid(bot))
