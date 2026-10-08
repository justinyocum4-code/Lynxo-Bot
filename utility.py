"""Utility features for Lynxo Bot: custom commands, auto-responder, AFK,
reminders, server stats channel, and a ticket system.

Runtime state lives in the guild blob under the "utility" key. The
configurable settings (custom_commands, autoresponders, stats_enabled,
tickets_enabled) live in the standard blob["settings"] dict so the
dashboard settings API can edit them; _config() mirrors them in for
reads/writes. This file is otherwise self-contained.
"""
import copy
import re
import sys
import time

import discord
from discord import app_commands
from discord.ext import commands, tasks

MAX_TRIGGER_LEN = 32
MAX_RESPONSE_LEN = 1500
MAX_REMINDERS_PER_GUILD = 25
AUTORESPOND_COOLDOWN = 5      # seconds per author
AFK_NOTICE_COOLDOWN = 60      # seconds per AFK user being mentioned


class Utility(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._ar_cooldown = {}    # user_id -> last auto-response epoch
        self._afk_notice_cd = {}  # afk user_id -> last notice epoch

    # ---------------- config ----------------

    def _config(self, guild):
        """Guild blob section for utility features, with local defaults.

        The configurable settings (custom_commands, autoresponders,
        stats_enabled, tickets_enabled) live in the standard settings
        dict so the dashboard settings API can edit them. They are
        mirrored here as the same object, so the rest of this file reads
        and writes them unchanged. Runtime state (afk, reminders,
        tickets, stats_channel_id) stays under the "utility" key.
        """
        g = self.bot.store.guild(guild.id)
        cfg = g.setdefault("utility", {})
        s = g.setdefault("settings", {})
        for k, v in (("custom_commands", {}), ("autoresponders", {}),
                     ("stats_enabled", False), ("tickets_enabled", False)):
            s.setdefault(k, copy.deepcopy(v))
            cfg[k] = s[k]
        cfg.setdefault("afk", {})               # user_id str -> {reason, since}
        cfg.setdefault("reminders", [])          # [{id, user_id, channel_id, due_at, text}]
        cfg.setdefault("stats_channel_id", None)
        cfg.setdefault("tickets", {})           # user_id str -> channel_id
        return cfg

    def _save(self):
        self.bot.store.save()

    # ---------------- message listener ----------------

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot or message.guild is None:
            return
        try:
            cfg = self._config(message.guild)
            content = message.content or ""

            # 1. Custom commands: "!trigger"
            if content.startswith("!"):
                parts = content[1:].split()
                if parts:
                    trigger = parts[0].lower()
                    response = cfg["custom_commands"].get(trigger)
                    if response:
                        await message.reply(response, mention_author=False)
                return  # never run the auto-responder on !commands

            # 2. AFK: author came back
            uid = str(message.author.id)
            if uid in cfg["afk"]:
                cfg["afk"].pop(uid, None)
                self._save()
                try:
                    note = await message.channel.send(
                        f"Welcome back {message.author.mention}!")
                    await note.delete(delay=10)
                except (discord.Forbidden, discord.HTTPException):
                    pass

            # 3. Auto-responder (5s per-author cooldown)
            now = time.time()
            if now - self._ar_cooldown.get(message.author.id, 0) >= AUTORESPOND_COOLDOWN:
                lowered = content.lower()
                for phrase, response in cfg["autoresponders"].items():
                    if phrase and phrase in lowered:
                        self._ar_cooldown[message.author.id] = now
                        await message.reply(response, mention_author=False)
                        break

            # 4. AFK mentions (60s cooldown per AFK user)
            for user in message.mentions:
                entry = cfg["afk"].get(str(user.id))
                if not entry:
                    continue
                if now - self._afk_notice_cd.get(user.id, 0) < AFK_NOTICE_COOLDOWN:
                    continue
                self._afk_notice_cd[user.id] = now
                reason = entry.get("reason") or "AFK"
                await message.channel.send(
                    f"{user.display_name} is AFK: {reason}")
        except Exception as e:  # noqa: BLE001 - never break message flow
            print(f"Utility.on_message failed: {e}", file=sys.stderr, flush=True)

    # ---------------- custom commands ----------------

    @app_commands.command(name="addcmd", description="Add a custom !command (mods only).")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def addcmd(self, interaction: discord.Interaction,
                     trigger: str, response: str):
        trigger = trigger.lower().strip()
        if not trigger or " " in trigger:
            await interaction.response.send_message(
                "The trigger must be one word with no spaces.", ephemeral=True)
            return
        if len(trigger) > MAX_TRIGGER_LEN:
            await interaction.response.send_message(
                f"Keep the trigger under {MAX_TRIGGER_LEN} characters.", ephemeral=True)
            return
        if len(response) > MAX_RESPONSE_LEN:
            await interaction.response.send_message(
                f"Keep the response under {MAX_RESPONSE_LEN} characters.", ephemeral=True)
            return
        cfg = self._config(interaction.guild)
        updated = trigger in cfg["custom_commands"]
        cfg["custom_commands"][trigger] = response
        self._save()
        await interaction.response.send_message(
            f"Custom command !{trigger} {'updated' if updated else 'added'}.", ephemeral=True)

    @app_commands.command(name="delcmd", description="Delete a custom !command (mods only).")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def delcmd(self, interaction: discord.Interaction, trigger: str):
        trigger = trigger.lower().strip()
        cfg = self._config(interaction.guild)
        if cfg["custom_commands"].pop(trigger, None) is None:
            await interaction.response.send_message(
                f"There is no custom command !{trigger}.", ephemeral=True)
            return
        self._save()
        await interaction.response.send_message(
            f"Custom command !{trigger} deleted.", ephemeral=True)

    # ---------------- auto-responder ----------------

    @app_commands.command(name="addresponse", description="Add an auto-reply trigger phrase (mods only).")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def addresponse(self, interaction: discord.Interaction,
                          trigger: str, response: str):
        trigger = trigger.lower().strip()
        if not trigger:
            await interaction.response.send_message(
                "Give me a trigger phrase.", ephemeral=True)
            return
        if len(trigger) > 100:
            await interaction.response.send_message(
                "Keep the trigger phrase under 100 characters.", ephemeral=True)
            return
        if len(response) > MAX_RESPONSE_LEN:
            await interaction.response.send_message(
                f"Keep the response under {MAX_RESPONSE_LEN} characters.", ephemeral=True)
            return
        cfg = self._config(interaction.guild)
        updated = trigger in cfg["autoresponders"]
        cfg["autoresponders"][trigger] = response
        self._save()
        await interaction.response.send_message(
            f"Auto-response for '{trigger}' {'updated' if updated else 'added'}.",
            ephemeral=True)

    @app_commands.command(name="delresponse", description="Delete an auto-reply trigger phrase (mods only).")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def delresponse(self, interaction: discord.Interaction, trigger: str):
        trigger = trigger.lower().strip()
        cfg = self._config(interaction.guild)
        if cfg["autoresponders"].pop(trigger, None) is None:
            await interaction.response.send_message(
                f"There is no auto-response for '{trigger}'.", ephemeral=True)
            return
        self._save()
        await interaction.response.send_message(
            f"Auto-response for '{trigger}' deleted.", ephemeral=True)

    # ---------------- AFK ----------------

    @app_commands.command(name="afk", description="Mark yourself AFK with an optional reason.")
    async def afk(self, interaction: discord.Interaction, reason: str = "AFK"):
        cfg = self._config(interaction.guild)
        cfg["afk"][str(interaction.user.id)] = {
            "reason": reason[:200], "since": time.time()}
        self._save()
        await interaction.response.send_message(
            f"You are now AFK: {reason[:200]}", ephemeral=True)

    # ---------------- reminders ----------------

    @app_commands.command(name="remind", description="Remind you of something in a number of minutes.")
    async def remind(self, interaction: discord.Interaction,
                     minutes: app_commands.Range[int, 1, 10080], text: str):
        cfg = self._config(interaction.guild)
        active = [r for r in cfg["reminders"]
                  if r.get("user_id") == interaction.user.id]
        if len(active) >= MAX_REMINDERS_PER_GUILD:
            await interaction.response.send_message(
                f"You already have {MAX_REMINDERS_PER_GUILD} reminders. "
                "Wait for one to go off first.", ephemeral=True)
            return
        rid = max([r.get("id", 0) for r in cfg["reminders"]], default=0) + 1
        cfg["reminders"].append({
            "id": rid,
            "user_id": interaction.user.id,
            "channel_id": interaction.channel_id,
            "due_at": time.time() + minutes * 60,
            "text": text[:500],
        })
        self._save()
        when = "1 minute" if minutes == 1 else f"{minutes} minutes"
        await interaction.response.send_message(
            f"Got it — I will remind you in {when}.", ephemeral=True)

    @tasks.loop(minutes=1)
    async def reminder_loop(self):
        for gid in self.bot.store.setup_guilds():
            guild = self.bot.get_guild(gid)
            if guild is None:
                continue
            try:
                await self._fire_reminders(guild)
            except Exception as e:  # noqa: BLE001
                print(f"Utility.reminder_loop failed for {guild.name}: {e}",
                      file=sys.stderr, flush=True)

    @reminder_loop.before_loop
    async def _before_reminders(self):
        await self.bot.wait_until_ready()

    async def _fire_reminders(self, guild):
        cfg = self._config(guild)
        now = time.time()
        due = [r for r in cfg["reminders"] if r.get("due_at", 0) <= now]
        if not due:
            return
        cfg["reminders"] = [r for r in cfg["reminders"] if r.get("due_at", 0) > now]
        self._save()
        for r in due:
            channel = guild.get_channel(r.get("channel_id") or 0)
            if channel is None:
                continue
            try:
                await channel.send(f"<@{r['user_id']}> Reminder: {r.get('text', '')}")
            except (discord.Forbidden, discord.HTTPException) as e:
                print(f"Utility: could not send reminder in {guild.name}: {e}",
                      file=sys.stderr, flush=True)

    # ---------------- server stats ----------------

    @tasks.loop(minutes=10)
    async def stats_loop(self):
        for gid in self.bot.store.setup_guilds():
            guild = self.bot.get_guild(gid)
            if guild is None:
                continue
            try:
                await self._update_stats(guild)
            except Exception as e:  # noqa: BLE001
                print(f"Utility.stats_loop failed for {guild.name}: {e}",
                      file=sys.stderr, flush=True)

    @stats_loop.before_loop
    async def _before_stats(self):
        await self.bot.wait_until_ready()

    async def _update_stats(self, guild):
        cfg = self._config(guild)
        if not cfg.get("stats_enabled"):
            return
        want = f"\U0001f465 Members: {guild.member_count}"
        channel = guild.get_channel(cfg.get("stats_channel_id") or 0)
        if channel is None or not isinstance(channel, discord.VoiceChannel):
            channel = await guild.create_voice_channel(want, position=0)
            cfg["stats_channel_id"] = channel.id
            self._save()
            return
        if channel.name != want:
            await channel.edit(name=want)

    # ---------------- tickets ----------------

    def _ticket_channel_name(self, member):
        base = re.sub(r"[^a-z0-9]+", "-", member.name.lower()).strip("-") or "user"
        name = f"ticket-{base[:90]}"
        existing = {c.name for c in member.guild.channels}
        if name not in existing:
            return name
        n = 2
        while f"{name}-{n}" in existing:
            n += 1
        return f"{name}-{n}"

    @app_commands.command(name="newticket", description="Open a private support ticket.")
    async def newticket(self, interaction: discord.Interaction, topic: str = "No topic given"):
        guild = interaction.guild
        cfg = self._config(guild)
        if not cfg.get("tickets_enabled"):
            await interaction.response.send_message(
                "Tickets are not turned on in this server.", ephemeral=True)
            return
        old_id = cfg["tickets"].get(str(interaction.user.id))
        if old_id and guild.get_channel(old_id) is not None:
            await interaction.response.send_message(
                "You already have an open ticket.", ephemeral=True)
            return
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user: discord.PermissionOverwrite(
                view_channel=True, send_messages=True, read_message_history=True),
        }
        for role in guild.roles:
            if role == guild.default_role:
                continue
            perms = role.permissions
            if perms.administrator or perms.manage_channels or perms.manage_guild:
                overwrites[role] = discord.PermissionOverwrite(
                    view_channel=True, send_messages=True, read_message_history=True)
        category = guild.categories[0] if guild.categories else None
        try:
            channel = await guild.create_text_channel(
                self._ticket_channel_name(interaction.user),
                category=category,
                overwrites=overwrites,
                reason=f"Ticket opened by {interaction.user}")
        except (discord.Forbidden, discord.HTTPException) as e:
            await interaction.response.send_message(
                f"Could not open a ticket: {e}", ephemeral=True)
            return
        cfg["tickets"][str(interaction.user.id)] = channel.id
        self._save()
        await channel.send(
            f"Ticket opened by {interaction.user.mention}: {topic[:500]}")
        await interaction.response.send_message(
            f"Your ticket is open: {channel.mention}", ephemeral=True)

    @app_commands.command(name="closeticket", description="Close this ticket channel.")
    async def closeticket(self, interaction: discord.Interaction):
        guild = interaction.guild
        channel = interaction.channel
        cfg = self._config(guild)
        owner_id = None
        for uid, cid in cfg["tickets"].items():
            if cid == channel.id:
                owner_id = uid
                break
        is_mod = interaction.user.guild_permissions.manage_channels
        if owner_id is None:
            await interaction.response.send_message(
                "This is not a ticket channel.", ephemeral=True)
            return
        if not is_mod and str(interaction.user.id) != owner_id:
            await interaction.response.send_message(
                "Only the ticket owner or a mod can close this ticket.",
                ephemeral=True)
            return
        cfg["tickets"].pop(owner_id, None)
        self._save()
        try:
            await channel.send("Ticket closed.")
            await channel.delete(delay=5, reason="Ticket closed")
        except (discord.Forbidden, discord.HTTPException):
            pass
        try:
            await interaction.response.send_message(
                "Ticket closed.", ephemeral=True)
        except discord.HTTPException:
            pass

    # ---------------- lifecycle ----------------

    async def cog_load(self):
        self.reminder_loop.start()
        self.stats_loop.start()

    async def cog_unload(self):
        self.reminder_loop.cancel()
        self.stats_loop.cancel()
