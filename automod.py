"""Auto-mod: filters, heat-based spam scoring, strikes, mod commands."""
import hashlib
import re
import time
from collections import defaultdict
from datetime import timedelta

import discord
from discord import app_commands
from discord.ext import commands

from config import SCAM_PATTERNS
from utils import dm_user, mod_log, now_str, user_label

INVITE_RE = re.compile(
    r"(?:discord\.gg|discord\.com/invite|discordapp\.com/invite)/\S+", re.IGNORECASE
)
LINK_RE = re.compile(r"https?://\S+", re.IGNORECASE)


class AutoMod(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # (guild_id, user_id) -> {"ts": float, "score": float, "last_hash": str|None}
        self.heat = defaultdict(lambda: {"ts": 0.0, "score": 0.0, "last_hash": None})
        self.scam_res = [re.compile(p, re.IGNORECASE) for p in SCAM_PATTERNS]

    # ---------------- internal helpers ----------------

    def _settings(self, guild):
        return self.bot.store.guild(guild.id)["settings"]

    async def add_strike(self, guild, member, reason, source="Lynxo Bot (auto)"):
        """Add a strike; escalate to timeout/kick/ban at configured counts."""
        g = self.bot.store.guild(guild.id)
        s = g["settings"]
        rec = g["strikes"].setdefault(str(member.id), {"count": 0, "history": []})
        rec["count"] += 1
        rec["history"].append({"ts": now_str(), "reason": reason, "by": source})
        n = rec["count"]
        self.bot.store.save()

        await mod_log(
            self.bot, guild,
            f"STRIKE {n} — {user_label(member)} — reason: {reason} — by {source}",
        )

        action = None
        if n >= s["strikes_ban"]:
            action = ("ban", n, s["strikes_ban"])
        elif n >= s["strikes_kick"]:
            action = ("kick", n, s["strikes_kick"])
        elif n >= s["strikes_timeout"]:
            action = ("timeout", n, s["strikes_timeout"])

        if action:
            kind, count, needed = action
            why = f"Strike escalation: {count} strikes (reaches {kind} at {needed})"
            try:
                if kind == "timeout":
                    await member.timeout(
                        timedelta(minutes=s["escalation_timeout_minutes"]), reason=why
                    )
                elif kind == "kick":
                    await member.kick(reason=why)
                elif kind == "ban":
                    await member.ban(reason=why)
                await mod_log(
                    self.bot, guild,
                    f"{kind.upper()} — {user_label(member)} — {why} — by Lynxo Bot (strike escalation)",
                )
                await dm_user(
                    member,
                    f"You were {kind}ed in {guild.name}: {why}.",
                )
            except (discord.Forbidden, discord.HTTPException) as e:
                await mod_log(
                    self.bot, guild,
                    f"Could not {kind} {user_label(member)}: {e}",
                )
        return n

    def _has_banned_word(self, settings, content):
        lowered = content.lower()
        return any(w.lower() in lowered for w in settings["banned_words"])

    def _is_scam(self, content):
        return any(rx.search(content) for rx in self.scam_res)

    async def _filter_action(self, message, reason):
        """Delete the message, warn the user, add a strike, log it."""
        guild, member = message.guild, message.author
        try:
            await message.delete()
        except (discord.Forbidden, discord.HTTPException):
            pass
        n = await self.add_strike(guild, member, f"Auto-mod: {reason}")
        await dm_user(
            member,
            f"Your message in {guild.name} was removed: {reason}. "
            f"This is strike {n}. Further strikes lead to timeout, kick, then ban.",
        )

    async def _heat_check(self, message):
        guild, member = message.guild, message.author
        s = self._settings(guild)
        key = (guild.id, member.id)
        st = self.heat[key]
        now = time.monotonic()
        gap = now - st["ts"]

        # Decay old heat.
        if gap > 120:
            st["score"] = 0.0
        elif gap > 30:
            st["score"] *= 0.35

        content = message.content or ""
        score = st["score"]

        # Message rate.
        if gap < 1.5:
            score += 2
        # Duplicate messages.
        digest = hashlib.md5(content.strip().lower().encode()).hexdigest()
        if digest == st["last_hash"] and len(content.strip()) > 4:
            score += 5
        st["last_hash"] = digest
        # Mass mentions.
        mentions = len(message.mentions) + len(message.role_mentions)
        if mentions > 5:
            score += 7
        elif mentions >= 4:
            score += 3
        if message.mention_everyone and s["massping_filter"]:
            score += 6
        # Caps flood.
        alpha = [c for c in content if c.isalpha()]
        if len(alpha) > 12:
            caps = sum(1 for c in alpha if c.isupper())
            if caps / len(alpha) >= 0.7:
                score += 3
        # Links add a little heat (deletion is governed by the link filter).
        if LINK_RE.search(content):
            score += 2

        st["score"] = score
        st["ts"] = now

        if score >= s["spam_timeout_heat"]:
            try:
                await message.delete()
            except (discord.Forbidden, discord.HTTPException):
                pass
            try:
                await member.timeout(
                    timedelta(minutes=s["spam_timeout_minutes"]),
                    reason="Lynxo Bot: spam heat limit reached",
                )
                action = f"timed out for {s['spam_timeout_minutes']} minutes"
            except (discord.Forbidden, discord.HTTPException) as e:
                action = f"timeout failed ({e})"
            n = await self.add_strike(guild, member, "Auto-mod: spam (heat limit)")
            await mod_log(
                self.bot, guild,
                f"SPAM TIMEOUT — {user_label(member)} — {action} — strike {n}",
            )
            await dm_user(
                member,
                f"You were timed out in {guild.name} for spamming. "
                f"This is strike {n}.",
            )
            st["score"] = 0.0
        elif score >= s["spam_warn_heat"]:
            try:
                await message.delete()
            except (discord.Forbidden, discord.HTTPException):
                pass
            n = await self.add_strike(guild, member, "Auto-mod: spam (heat warning)")
            await mod_log(
                self.bot, guild,
                f"SPAM WARN — {user_label(member)} — message removed — strike {n}",
            )
            await dm_user(
                member,
                f"Slow down in {guild.name} — your message looked like spam and "
                f"was removed. This is strike {n}.",
            )
            st["score"] = 0.0

    # ---------------- message listener ----------------

    @commands.Cog.listener()
    async def on_message(self, message):
        if not message.guild or message.author.bot:
            return
        member = message.author
        if member.guild_permissions.manage_messages:
            return  # mods are exempt from auto-mod
        guild = message.guild
        s = self._settings(guild)
        content = message.content or ""

        if s["word_filter"] and self._has_banned_word(s, content):
            await self._filter_action(message, "banned word")
            return
        if s["invite_filter"] and INVITE_RE.search(content):
            await self._filter_action(message, "Discord invite link")
            return
        if self._is_scam(content):
            await self._filter_action(message, "suspected scam/phishing")
            return
        if s["link_filter"] and LINK_RE.search(content):
            await self._filter_action(message, "link (link filter is on)")
            return

        if s["ai_moderation"]:
            from ai import ai_moderate
            verdict = await ai_moderate(content)
            if verdict:
                await self._filter_action(message, f"AI flag: {verdict}")
                return

        await self._heat_check(message)

    # ---------------- mod slash commands ----------------

    @app_commands.command(name="warn", description="Warn a member (adds a strike).")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def warn(self, interaction: discord.Interaction,
                   member: discord.Member, reason: str = "No reason given"):
        n = await self.add_strike(
            interaction.guild, member, reason, source=str(interaction.user))
        await dm_user(member, f"You were warned in {interaction.guild.name}: {reason} (strike {n}).")
        await interaction.response.send_message(
            f"{member.mention} warned — strike {n}.", ephemeral=True)

    @app_commands.command(name="timeout", description="Time out a member.")
    @app_commands.checks.has_permissions(moderate_members=True)
    async def timeout(self, interaction: discord.Interaction,
                      member: discord.Member,
                      minutes: app_commands.Range[int, 1, 40320] = 10,
                      reason: str = "No reason given"):
        try:
            await member.timeout(timedelta(minutes=minutes), reason=reason)
        except (discord.Forbidden, discord.HTTPException) as e:
            await interaction.response.send_message(f"Could not time out: {e}", ephemeral=True)
            return
        await mod_log(self.bot, interaction.guild,
                      f"TIMEOUT — {user_label(member)} — {minutes} min — reason: {reason} — by {interaction.user}")
        await interaction.response.send_message(
            f"{member.mention} timed out for {minutes} minutes.", ephemeral=True)

    @app_commands.command(name="kick", description="Kick a member from the server.")
    @app_commands.checks.has_permissions(kick_members=True)
    async def kick(self, interaction: discord.Interaction,
                   member: discord.Member, reason: str = "No reason given"):
        try:
            await member.kick(reason=reason)
        except (discord.Forbidden, discord.HTTPException) as e:
            await interaction.response.send_message(f"Could not kick: {e}", ephemeral=True)
            return
        await mod_log(self.bot, interaction.guild,
                      f"KICK — {user_label(member)} — reason: {reason} — by {interaction.user}")
        await interaction.response.send_message(f"{member.mention} kicked.", ephemeral=True)

    @app_commands.command(name="ban", description="Ban a member from the server.")
    @app_commands.checks.has_permissions(ban_members=True)
    async def ban(self, interaction: discord.Interaction,
                  member: discord.Member,
                  delete_days: app_commands.Range[int, 0, 7] = 1,
                  reason: str = "No reason given"):
        try:
            await member.ban(reason=reason, delete_message_days=delete_days)
        except (discord.Forbidden, discord.HTTPException) as e:
            await interaction.response.send_message(f"Could not ban: {e}", ephemeral=True)
            return
        await mod_log(self.bot, interaction.guild,
                      f"BAN — {user_label(member)} — reason: {reason} — by {interaction.user}")
        await interaction.response.send_message(f"{member.mention} banned.", ephemeral=True)

    @app_commands.command(name="purge", description="Delete recent messages.")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def purge(self, interaction: discord.Interaction,
                    count: app_commands.Range[int, 1, 100],
                    member: discord.Member = None):
        await interaction.response.defer(ephemeral=True)
        def check(m):
            return member is None or m.author == member
        try:
            deleted = await interaction.channel.purge(limit=count, check=check)
        except (discord.Forbidden, discord.HTTPException) as e:
            await interaction.followup.send(f"Could not purge: {e}", ephemeral=True)
            return
        await mod_log(self.bot, interaction.guild,
                      f"PURGE — {len(deleted)} message(s) in #{interaction.channel.name} "
                      f"{'(from ' + str(member) + ') ' if member else ''}— by {interaction.user}")
        await interaction.followup.send(f"Deleted {len(deleted)} message(s).", ephemeral=True)

    @app_commands.command(name="strikes", description="Look up a member's strikes.")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def strikes(self, interaction: discord.Interaction, member: discord.Member):
        g = self.bot.store.guild(interaction.guild.id)
        rec = g["strikes"].get(str(member.id), {"count": 0, "history": []})
        lines = [f"{member.mention} has {rec['count']} strike(s)."]
        for h in rec["history"][-10:]:
            lines.append(f"- {h['ts']}: {h['reason']} (by {h['by']})")
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    @app_commands.command(name="clearstrikes", description="Clear a member's strikes.")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def clearstrikes(self, interaction: discord.Interaction, member: discord.Member):
        g = self.bot.store.guild(interaction.guild.id)
        g["strikes"].pop(str(member.id), None)
        self.bot.store.save()
        await mod_log(self.bot, interaction.guild,
                      f"STRIKES CLEARED — {user_label(member)} — by {interaction.user}")
        await interaction.response.send_message(
            f"Strikes cleared for {member.mention}.", ephemeral=True)

    @app_commands.command(name="quarantine", description="Strip a member's roles and isolate them.")
    @app_commands.checks.has_permissions(manage_roles=True)
    async def quarantine(self, interaction: discord.Interaction,
                        member: discord.Member, reason: str = "No reason given"):
        guild = interaction.guild
        g = self.bot.store.guild(guild.id)
        qrole_id = g["roles"].get("quarantined")
        qrole = guild.get_role(qrole_id) if qrole_id else None
        if qrole is None:
            await interaction.response.send_message(
                "Quarantined role not found — run /setup first.", ephemeral=True)
            return
        g["quarantined"][str(member.id)] = [r.id for r in member.roles if not r.is_default()]
        try:
            removable = [r for r in member.roles if not r.is_default()]
            if removable:
                await member.remove_roles(*removable,
                                          reason=f"Quarantine: {reason}")
            await member.add_roles(qrole, reason=f"Quarantine: {reason}")
        except (discord.Forbidden, discord.HTTPException) as e:
            await interaction.response.send_message(f"Could not quarantine: {e}", ephemeral=True)
            return
        self.bot.store.save()
        await mod_log(self.bot, guild,
                      f"QUARANTINE — {user_label(member)} — reason: {reason} — by {interaction.user}")
        await interaction.response.send_message(
            f"{member.mention} quarantined.", ephemeral=True)

    @app_commands.command(name="unquarantine", description="Release a member from quarantine.")
    @app_commands.checks.has_permissions(manage_roles=True)
    async def unquarantine(self, interaction: discord.Interaction, member: discord.Member):
        guild = interaction.guild
        g = self.bot.store.guild(guild.id)
        role_ids = g["quarantined"].pop(str(member.id), [])
        qrole = guild.get_role(g["roles"].get("quarantined", 0))
        try:
            if qrole and qrole in member.roles:
                await member.remove_roles(qrole, reason="Released from quarantine")
            restore = [guild.get_role(rid) for rid in role_ids]
            restore = [r for r in restore if r is not None]
            if restore:
                await member.add_roles(*restore, reason="Released from quarantine")
        except (discord.Forbidden, discord.HTTPException) as e:
            await interaction.response.send_message(f"Could not release: {e}", ephemeral=True)
            return
        self.bot.store.save()
        await mod_log(self.bot, guild,
                      f"UNQUARANTINE — {user_label(member)} — by {interaction.user}")
        await interaction.response.send_message(
            f"{member.mention} released from quarantine.", ephemeral=True)

    # ---------------- filter management ----------------

    filter = app_commands.Group(name="filter", description="Manage auto-mod filters.")

    @filter.command(name="add", description="Add a word to the banned-word list.")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def filter_add(self, interaction: discord.Interaction, word: str):
        s = self._settings(interaction.guild)
        word = word.lower().strip()
        if word and word not in s["banned_words"]:
            s["banned_words"].append(word)
            self.bot.store.save()
        await interaction.response.send_message(f"Added '{word}' to the banned list.", ephemeral=True)

    @filter.command(name="remove", description="Remove a word from the banned-word list.")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def filter_remove(self, interaction: discord.Interaction, word: str):
        s = self._settings(interaction.guild)
        word = word.lower().strip()
        if word in s["banned_words"]:
            s["banned_words"].remove(word)
            self.bot.store.save()
        await interaction.response.send_message(f"Removed '{word}' from the banned list.", ephemeral=True)

    @filter.command(name="list", description="Show the banned-word list.")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def filter_list(self, interaction: discord.Interaction):
        s = self._settings(interaction.guild)
        words = ", ".join(s["banned_words"]) or "(empty)"
        await interaction.response.send_message(f"Banned words: {words}", ephemeral=True)

    @filter.command(name="toggle_words", description="Turn the word filter on/off.")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def filter_toggle_words(self, interaction: discord.Interaction):
        s = self._settings(interaction.guild)
        s["word_filter"] = not s["word_filter"]
        self.bot.store.save()
        await interaction.response.send_message(
            f"Word filter is now {'ON' if s['word_filter'] else 'OFF'}.", ephemeral=True)

    @filter.command(name="toggle_invites", description="Turn the invite-link filter on/off.")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def filter_toggle_invites(self, interaction: discord.Interaction):
        s = self._settings(interaction.guild)
        s["invite_filter"] = not s["invite_filter"]
        self.bot.store.save()
        await interaction.response.send_message(
            f"Invite filter is now {'ON' if s['invite_filter'] else 'OFF'}.", ephemeral=True)

    @filter.command(name="toggle_links", description="Turn the all-links filter on/off.")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def filter_toggle_links(self, interaction: discord.Interaction):
        s = self._settings(interaction.guild)
        s["link_filter"] = not s["link_filter"]
        self.bot.store.save()
        await interaction.response.send_message(
            f"Link filter is now {'ON' if s['link_filter'] else 'OFF'}.", ephemeral=True)

    @filter.command(name="toggle_ai", description="Turn Groq AI moderation on/off (needs GROQ_API_KEY).")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def filter_toggle_ai(self, interaction: discord.Interaction):
        import os
        s = self._settings(interaction.guild)
        s["ai_moderation"] = not s["ai_moderation"]
        self.bot.store.save()
        key_note = "" if os.environ.get("GROQ_API_KEY") else " (no GROQ_API_KEY set — AI will stay idle)"
        await interaction.response.send_message(
            f"AI moderation is now {'ON' if s['ai_moderation'] else 'OFF'}{key_note}.", ephemeral=True)


async def setup(bot):
    await bot.add_cog(AutoMod(bot))
