"""Achievements: members earn badges for activity.
Configured from the dashboard Achievements tab."""
import datetime
import sys
import uuid

import discord
from discord import app_commands
from discord.ext import commands

ACHIEVEMENT_TYPES = {
    "messages": "Messages sent",
    "level": "Level reached",
    "days": "Days active",
    "roles": "Reaction roles claimed",
}

DEFAULTS = {
    "enabled": False,
    "announce_channel_id": None,  # None = same channel as the action
}


class Achievements(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    def _config(self, guild):
        g = self.bot.store.guild(guild.id)
        cfg = g.setdefault("achievements", {})
        for k, v in DEFAULTS.items():
            cfg.setdefault(k, v)
        cfg.setdefault("defs", [])
        # Per-user stats: uid -> {messages, days: [...], roles}
        g.setdefault("ach_stats", {})
        # Per-user earned: uid -> [achievement_id, ...]
        g.setdefault("ach_earned", {})
        return cfg

    def _save(self):
        self.bot.store.save()

    def _stats(self, guild, user_id):
        g = self.bot.store.guild(guild.id)
        stats = g.setdefault("ach_stats", {})
        st = stats.setdefault(str(user_id), {"messages": 0, "days": [], "roles": 0})
        return st

    def _level_of(self, guild, user_id):
        g = self.bot.store.guild(guild.id)
        xp = int((g.get("level_xp") or {}).get(str(user_id), 0))
        # Triangular: 100, 300, 600... (mirrors community._level_for_xp)
        n = 0
        while 100 * (n + 1) * (n + 2) // 2 <= xp:
            n += 1
        return n

    def _value_for(self, guild, user_id, ach_type):
        st = self._stats(guild, user_id)
        if ach_type == "messages":
            return int(st.get("messages", 0))
        if ach_type == "level":
            return self._level_of(guild, user_id)
        if ach_type == "days":
            return len(st.get("days", []))
        if ach_type == "roles":
            return int(st.get("roles", 0))
        return 0

    async def _check(self, guild, member, channel=None):
        """Award any newly-earned achievements. Returns list of defs."""
        cfg = self._config(guild)
        if not cfg.get("enabled"):
            return []
        uid = str(member.id)
        g = self.bot.store.guild(guild.id)
        earned = g.setdefault("ach_earned", {}).setdefault(uid, [])
        newly = []
        for d in cfg.get("defs", []):
            aid = str(d.get("id"))
            if aid in earned:
                continue
            ach_type = d.get("type")
            try:
                threshold = int(d.get("threshold", 0))
            except (TypeError, ValueError):
                continue
            if threshold <= 0:
                continue
            if self._value_for(guild, member.id, ach_type) >= threshold:
                earned.append(aid)
                newly.append(d)
        if newly:
            self._save()
            dest = None
            ann_id = cfg.get("announce_channel_id")
            if ann_id:
                dest = guild.get_channel(int(ann_id))
            dest = dest or channel
            if dest is not None:
                for d in newly:
                    emoji = str(d.get("emoji") or "🏆")
                    name = str(d.get("name") or "Achievement")
                    try:
                        await dest.send(
                            f"{emoji} {member.mention} earned the **{name}** achievement!")
                    except (discord.Forbidden, discord.HTTPException):
                        pass
        return newly

    def record_message(self, guild, user_id):
        st = self._stats(guild, user_id)
        st["messages"] = int(st.get("messages", 0)) + 1
        today = datetime.date.today().isoformat()
        days = st.setdefault("days", [])
        if today not in days:
            days.append(today)
            # Keep it bounded.
            if len(days) > 365:
                del days[:-365]
        self._save()

    def record_role_claim(self, guild, user_id):
        st = self._stats(guild, user_id)
        st["roles"] = int(st.get("roles", 0)) + 1
        self._save()

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.guild is None or message.author.bot:
            return
        try:
            cfg = self._config(message.guild)
            if not cfg.get("enabled"):
                return
            self.record_message(message.guild, message.author.id)
            await self._check(message.guild, message.author, message.channel)
        except Exception as e:  # noqa: BLE001
            print(f"Achievements: on_message failed: {e}", file=sys.stderr,
                  flush=True)

    @app_commands.command(name="achievements",
                          description="Show your earned achievements.")
    async def achievements_cmd(self, interaction: discord.Interaction):
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(
                "Use that inside the server.", ephemeral=True)
            return
        cfg = self._config(guild)
        if not cfg.get("enabled"):
            await interaction.response.send_message(
                "Achievements aren't turned on in this server.", ephemeral=True)
            return
        uid = str(interaction.user.id)
        g = self.bot.store.guild(guild.id)
        earned_ids = set((g.get("ach_earned") or {}).get(uid, []))
        defs = {str(d.get("id")): d for d in cfg.get("defs", [])}
        lines = []
        for aid in earned_ids:
            d = defs.get(aid)
            if d:
                lines.append(f"{d.get('emoji') or '🏆'} **{d.get('name')}** — "
                             f"{d.get('description') or ''}")
        # Progress toward the next ones.
        upcoming = []
        for d in cfg.get("defs", []):
            aid = str(d.get("id"))
            if aid in earned_ids:
                continue
            ach_type = d.get("type")
            try:
                threshold = int(d.get("threshold", 0))
            except (TypeError, ValueError):
                continue
            if threshold <= 0:
                continue
            val = self._value_for(guild, interaction.user.id, ach_type)
            label = ACHIEVEMENT_TYPES.get(ach_type, ach_type)
            upcoming.append(
                f"{d.get('emoji') or '🏆'} {d.get('name')} — "
                f"{val}/{threshold} {label.lower()}")
            if len(upcoming) >= 5:
                break
        embed = discord.Embed(
            title=f"{interaction.user.display_name}'s Achievements",
            color=0xFFD700,
        )
        embed.add_field(
            name=f"Earned ({len(lines)})",
            value="\n".join(lines) if lines else "None yet — keep chatting!",
            inline=False,
        )
        if upcoming:
            embed.add_field(name="In progress", value="\n".join(upcoming),
                            inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)
