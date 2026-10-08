"""Shoutouts: members recognize each other with /shoutout.
Monthly leaderboard with optional Member of the Month role."""
import datetime
import sys

import discord
from discord import app_commands
from discord.ext import commands

DEFAULTS = {
    "enabled": False,
    "channel_id": None,
    "daily_limit": 3,
    "monthly_reset": True,
    "motm_role_id": None,  # Member of the Month role
}


class Shoutouts(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    def _config(self, guild):
        g = self.bot.store.guild(guild.id)
        cfg = g.setdefault("shoutouts", {})
        for k, v in DEFAULTS.items():
            cfg.setdefault(k, v)
        # receiver_id -> {"total": n, "monthly": {"YYYY-MM": n}}
        g.setdefault("shoutout_counts", {})
        # giver_id -> {"date": "YYYY-MM-DD", "count": n, "targets": [...]}
        g.setdefault("shoutout_given", {})
        return cfg

    def _save(self):
        self.bot.store.save()

    def _month_key(self):
        return datetime.date.today().strftime("%Y-%m")

    def _today_key(self):
        return datetime.date.today().isoformat()

    @app_commands.command(name="shoutout",
                          description="Give another member a shoutout.")
    @app_commands.describe(member="Who deserves it?",
                           reason="Why? (optional)")
    async def shoutout_cmd(self, interaction: discord.Interaction,
                           member: discord.Member,
                           reason: str = ""):
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(
                "Use that inside the server.", ephemeral=True)
            return
        cfg = self._config(guild)
        if not cfg.get("enabled"):
            await interaction.response.send_message(
                "Shoutouts aren't turned on in this server.", ephemeral=True)
            return
        # Monthly rollover: award last month's winner on first use of new month.
        if cfg.get("monthly_reset"):
            mk = self._month_key()
            if cfg.get("last_reset_month") != mk:
                cfg["last_reset_month"] = mk
                self._save()
                try:
                    await self.do_monthly_reset(guild)
                except Exception:  # noqa: BLE001
                    pass
        giver = interaction.user
        if member.id == giver.id:
            await interaction.response.send_message(
                "You can't shout yourself out — pick someone else!",
                ephemeral=True)
            return
        if member.bot:
            await interaction.response.send_message(
                "Pick a real member, not a bot.", ephemeral=True)
            return
        g = self.bot.store.guild(guild.id)
        given = g.setdefault("shoutout_given", {})
        rec = given.setdefault(str(giver.id),
                               {"date": "", "count": 0, "targets": []})
        today = self._today_key()
        if rec.get("date") != today:
            rec["date"] = today
            rec["count"] = 0
            rec["targets"] = []
        limit = int(cfg.get("daily_limit") or 3)
        if rec["count"] >= limit:
            await interaction.response.send_message(
                f"You've used all {limit} of your shoutouts for today — "
                "come back tomorrow!", ephemeral=True)
            return
        if member.id in rec.get("targets", []):
            await interaction.response.send_message(
                f"You already shouted out {member.display_name} today — "
                "spread the love!", ephemeral=True)
            return
        # Record it.
        rec["count"] += 1
        rec["targets"].append(member.id)
        counts = g.setdefault("shoutout_counts", {})
        entry = counts.setdefault(str(member.id), {"total": 0, "monthly": {}})
        entry["total"] = int(entry.get("total", 0)) + 1
        mk = self._month_key()
        monthly = entry.setdefault("monthly", {})
        monthly[mk] = int(monthly.get(mk, 0)) + 1
        self._save()
        # Announce.
        reason = (reason or "").strip()[:500]
        text = (f"🎉 {giver.mention} gave {member.mention} a shoutout!"
                + (f" {reason}" if reason else ""))
        dest = None
        ch_id = cfg.get("channel_id")
        if ch_id:
            dest = guild.get_channel(int(ch_id))
        dest = dest or interaction.channel
        try:
            await dest.send(text)
        except (discord.Forbidden, discord.HTTPException):
            pass
        await interaction.response.send_message(
            f"Shoutout sent to {member.display_name}! 🎉", ephemeral=True)

    @app_commands.command(name="shoutout-top",
                          description="Show the shoutout leaderboard.")
    async def shoutout_top(self, interaction: discord.Interaction):
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(
                "Use that inside the server.", ephemeral=True)
            return
        cfg = self._config(guild)
        if not cfg.get("enabled"):
            await interaction.response.send_message(
                "Shoutouts aren't turned on in this server.", ephemeral=True)
            return
        g = self.bot.store.guild(guild.id)
        counts = g.get("shoutout_counts", {})
        mk = self._month_key()
        rows = []
        for uid, entry in counts.items():
            n = int((entry.get("monthly") or {}).get(mk, 0))
            if n > 0:
                member = guild.get_member(int(uid))
                name = member.display_name if member else f"user {uid}"
                rows.append((n, name))
        rows.sort(reverse=True)
        embed = discord.Embed(
            title=f"🎉 Shoutout Leaderboard — {mk}",
            color=0xFFD700,
        )
        if rows:
            lines = [f"{i+1}. **{name}** — {n} shoutout{'s' if n != 1 else ''}"
                     for i, (n, name) in enumerate(rows[:10])]
            embed.description = "\n".join(lines)
        else:
            embed.description = "No shoutouts yet this month — be the first!"
        await interaction.response.send_message(embed=embed)

    async def do_monthly_reset(self, guild):
        """Async wrapper: rotate the Member of the Month role."""
        cfg = self._config(guild)
        g = self.bot.store.guild(guild.id)
        counts = g.get("shoutout_counts", {})
        today = datetime.date.today()
        first = today.replace(day=1)
        last_month = (first - datetime.timedelta(days=1)).strftime("%Y-%m")
        best_uid, best_n = None, 0
        for uid, entry in counts.items():
            n = int((entry.get("monthly") or {}).get(last_month, 0))
            if n > best_n:
                best_uid, best_n = uid, n
        if not (best_uid and best_n > 0):
            return None
        member = guild.get_member(int(best_uid))
        if member is None:
            return None
        role_id = cfg.get("motm_role_id")
        if role_id:
            role = guild.get_role(int(role_id))
            if role:
                for m in list(role.members):
                    if m.id != member.id:
                        try:
                            await m.remove_roles(role, reason="New Member of the Month")
                        except (discord.Forbidden, discord.HTTPException):
                            pass
                try:
                    await member.add_roles(role, reason="Member of the Month")
                except (discord.Forbidden, discord.HTTPException):
                    pass
        # Announce.
        dest = None
        ch_id = cfg.get("channel_id")
        if ch_id:
            dest = guild.get_channel(int(ch_id))
        if dest:
            try:
                await dest.send(
                    f"🏆 **Member of the Month** for {last_month}: "
                    f"{member.mention} with {best_n} shoutouts! Congrats!")
            except (discord.Forbidden, discord.HTTPException):
                pass
        return member.display_name
