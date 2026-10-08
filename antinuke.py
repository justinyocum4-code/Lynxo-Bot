"""Anti-nuke + panic modes (Phase 2).

Watches for destructive guild changes via the audit log:
  - mass channel create/delete
  - mass role create/delete/update, and any role gaining Administrator
  - webhook creation spam
  - a member gaining Administrator
  - a bot being added to the server
  - mass member removals (kick / ban / prune)

The server owner and whitelisted users/roles are always exempt, as is the
bot itself (so /setup never trips the alarm).

On a trigger the bot strips (or bans) the offender, deletes rogue webhooks
it can identify, DMs the owner, logs in plain English, and optionally
restores the latest backup. A short cooldown keeps one incident from
spamming.

Panic commands: /panic (partial lockdown), /lockdown (full freeze),
/unlock (restore everything). Panic also pauses the verify buttons by
reusing the raid lockdown flag.
"""
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands

from utils import dm_user, mod_log, now_str, user_label

AUDIT_WINDOW = 90  # seconds: only trust audit entries this fresh

_PRUNE_ACTIONS = tuple(
    a for a in (
        getattr(discord.AuditLogAction, "kick", None),
        getattr(discord.AuditLogAction, "ban", None),
        getattr(discord.AuditLogAction, "member_prune", None),
    )
    if a is not None
)


class AntiNuke(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # guild_id -> kind -> deque of (timestamp, actor_id)
        self.events = defaultdict(lambda: defaultdict(deque))
        self.removals = defaultdict(deque)   # guild_id -> deque of timestamps
        self.cooldown_until = {}             # guild_id -> datetime
        self.panic_state = {}                # guild_id -> saved pre-panic overwrites
        # Reload any panic state saved before a restart.
        for gid, g in self.bot.store.data.get("guilds", {}).items():
            if g.get("panic"):
                try:
                    self.panic_state[int(gid)] = g["panic"]
                except (ValueError, TypeError):
                    pass

    # ---------------- helpers ----------------

    def _settings(self, guild):
        return self.bot.store.guild(guild.id)["settings"]

    def _exempt(self, guild, user):
        """True if this user may never be punished by anti-nuke."""
        if user is None:
            return True
        if user.id == guild.owner_id:
            return True
        if guild.me and user.id == guild.me.id:
            return True
        g = self.bot.store.guild(guild.id)
        wl = g.get("nuke_whitelist", {}) or {}
        if user.id in wl.get("users", []):
            return True
        member = guild.get_member(user.id)
        if member:
            wroles = set(wl.get("roles", []))
            if any(r.id in wroles for r in member.roles):
                return True
        return False

    async def _audit_entry(self, guild, action):
        """Most recent audit-log entry for this action, if fresh."""
        try:
            async for entry in guild.audit_logs(limit=3, action=action):
                age = (datetime.now(timezone.utc) - entry.created_at).total_seconds()
                if age <= AUDIT_WINDOW:
                    return entry
                break
        except (discord.Forbidden, discord.HTTPException):
            pass
        return None

    async def _recent_audit_user(self, guild, actions, seconds=90):
        """Find who did one of these actions most recently, else None."""
        try:
            async for entry in guild.audit_logs(limit=10):
                if entry.action not in actions:
                    continue
                age = (datetime.now(timezone.utc) - entry.created_at).total_seconds()
                if age <= seconds:
                    return entry.user
                break
        except (discord.Forbidden, discord.HTTPException):
            pass
        return None

    def _in_cooldown(self, guild):
        until = self.cooldown_until.get(guild.id)
        return bool(until and datetime.now(timezone.utc) < until)

    async def _nuke_trigger(self, guild, actor, summary, rogue_webhook=None):
        """Punish the offender (unless exempt), alert, optionally restore."""
        s = self._settings(guild)
        if self._in_cooldown(guild.id):
            return
        self.cooldown_until[guild.id] = (
            datetime.now(timezone.utc) + timedelta(minutes=s["nuke_cooldown_minutes"])
        )

        if actor is None or self._exempt(guild, actor):
            msg = (f"NUKE ALERT — {summary} — no action taken "
                   f"(actor unknown or exempt). Please review by hand.")
            await mod_log(self.bot, guild, msg)
            if guild.owner:
                await dm_user(guild.owner, f"[{guild.name}] {msg}")
            return

        member = guild.get_member(actor.id)
        action = s["nuke_action"]
        did = "nothing (could not reach them)"
        try:
            if action == "ban":
                target = member or await self.bot.fetch_user(actor.id)
                await guild.ban(target, reason=f"Lynxo Bot anti-nuke: {summary}")
                did = "banned"
            else:
                if member:
                    removable = [r for r in member.roles if not r.is_default()]
                    if removable:
                        await member.remove_roles(*removable,
                                                  reason=f"Lynxo Bot anti-nuke: {summary}")
                    did = "stripped of all roles"
        except (discord.Forbidden, discord.HTTPException, discord.NotFound) as e:
            did = f"punishment failed ({e})"

        if rogue_webhook is not None:
            try:
                await rogue_webhook.delete(reason="Lynxo Bot anti-nuke")
                did += "; rogue webhook deleted"
            except (discord.Forbidden, discord.HTTPException, discord.NotFound):
                pass

        msg = (f"NUKE TRIGGERED — {summary} — offender {user_label(actor)} "
               f"was {did}.")
        await mod_log(self.bot, guild, msg)
        if guild.owner:
            await dm_user(guild.owner, f"[{guild.name}] {msg}")

        # Rollback: restore the latest backup if there is one and it's on.
        if s["nuke_auto_restore"]:
            backups = self.bot.get_cog("Backups")
            if backups:
                path = backups.latest_backup_path(guild.id)
                if path:
                    await mod_log(self.bot, guild,
                                  "NUKE ROLLBACK — restoring the latest backup. "
                                  "This can take a minute.")
                    try:
                        report = await backups.restore_snapshot(
                            guild, backups.load_snapshot(path),
                            by_label="anti-nuke auto-restore")
                        await mod_log(self.bot, guild,
                                      f"NUKE ROLLBACK DONE — {report}")
                    except Exception as e:  # noqa: BLE001
                        await mod_log(self.bot, guild,
                                      f"NUKE ROLLBACK FAILED — {e}. "
                                      f"Restore the backup by hand with /restore.")
                else:
                    await mod_log(self.bot, guild,
                                  "NUKE ROLLBACK — no backup exists, so nothing "
                                  "could be restored. Run /backup regularly.")

    async def _count_event(self, guild, kind, action_enum, threshold, window,
                           describe, actor=None, entry=None):
        """Count one destructive event; trigger when the threshold is hit."""
        if actor is None and entry is not None:
            actor = entry.user
        if actor is not None and self._exempt(guild, actor):
            return
        now = datetime.now(timezone.utc)
        dq = self.events[guild.id][kind]
        key = actor.id if actor else 0
        dq.append((now, key))
        recent = [t for (t, k) in dq
                  if k == key and (now - t).total_seconds() <= window]
        if len(recent) >= threshold:
            dq.clear()
            rogue = getattr(entry, "target", None) if kind == "webhook" else None
            await self._nuke_trigger(
                guild, actor,
                f"{describe} ({len(recent)} times in {window}s)",
                rogue_webhook=rogue if isinstance(rogue, discord.Webhook) else None)

    # ---------------- watchers ----------------

    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel):
        guild = channel.guild
        s = self._settings(guild)
        entry = await self._audit_entry(guild, discord.AuditLogAction.channel_create)
        await self._count_event(guild, "channel", discord.AuditLogAction.channel_create,
                                s["nuke_channel_threshold"], s["nuke_channel_window"],
                                f"mass channel creation (#{channel.name})", entry=entry)

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel):
        guild = channel.guild
        s = self._settings(guild)
        entry = await self._audit_entry(guild, discord.AuditLogAction.channel_delete)
        await self._count_event(guild, "channel", discord.AuditLogAction.channel_delete,
                                s["nuke_channel_threshold"], s["nuke_channel_window"],
                                f"mass channel deletion (#{channel.name})", entry=entry)

    @commands.Cog.listener()
    async def on_guild_role_create(self, role):
        guild = role.guild
        s = self._settings(guild)
        entry = await self._audit_entry(guild, discord.AuditLogAction.role_create)
        await self._count_event(guild, "role", discord.AuditLogAction.role_create,
                                s["nuke_role_threshold"], s["nuke_role_window"],
                                f"mass role creation (@{role.name})", entry=entry)

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role):
        guild = role.guild
        s = self._settings(guild)
        entry = await self._audit_entry(guild, discord.AuditLogAction.role_delete)
        await self._count_event(guild, "role", discord.AuditLogAction.role_delete,
                                s["nuke_role_threshold"], s["nuke_role_window"],
                                f"mass role deletion (@{role.name})", entry=entry)

    @commands.Cog.listener()
    async def on_guild_role_update(self, before, after):
        guild = after.guild
        # Single most dangerous event: a role gaining Administrator.
        if after.permissions.administrator and not before.permissions.administrator:
            entry = await self._audit_entry(guild, discord.AuditLogAction.role_update)
            actor = entry.user if entry else None
            await self._nuke_trigger(
                guild, actor,
                f"role @{after.name} was given Administrator permission")
            return
        s = self._settings(guild)
        entry = await self._audit_entry(guild, discord.AuditLogAction.role_update)
        await self._count_event(guild, "role", discord.AuditLogAction.role_update,
                                s["nuke_role_threshold"], s["nuke_role_window"],
                                f"mass role edits (@{after.name})", entry=entry)

    @commands.Cog.listener()
    async def on_member_update(self, before, after):
        # Someone who didn't have admin now does.
        if (not before.guild_permissions.administrator
                and after.guild_permissions.administrator):
            guild = after.guild
            actor = await self._recent_audit_user(
                guild, (discord.AuditLogAction.member_role_update,), seconds=90)
            await self._nuke_trigger(
                guild, actor,
                f"{user_label(after)} was given Administrator permission")

    @commands.Cog.listener()
    async def on_webhooks_update(self, channel):
        guild = channel.guild
        s = self._settings(guild)
        entry = await self._audit_entry(guild, discord.AuditLogAction.webhook_create)
        if entry is None:
            return
        await self._count_event(guild, "webhook", discord.AuditLogAction.webhook_create,
                                s["nuke_webhook_threshold"], s["nuke_webhook_window"],
                                "webhook creation spam", entry=entry)

    @commands.Cog.listener()
    async def on_member_join(self, member):
        # A bot was added to the server.
        if not member.bot or (member.guild.me and member.id == member.guild.me.id):
            return
        guild = member.guild
        actor = await self._recent_audit_user(
            guild, (discord.AuditLogAction.bot_add,), seconds=90)
        if actor is not None and self._exempt(guild, actor):
            return
        try:
            await member.kick(reason="Lynxo Bot anti-nuke: unauthorized bot added")
            kicked = "and the bot was kicked"
        except (discord.Forbidden, discord.HTTPException):
            kicked = "but the bot could not be kicked"
        await self._nuke_trigger(
            guild, actor,
            f"bot {user_label(member)} was added to the server {kicked}")

    @commands.Cog.listener()
    async def on_member_remove(self, member):
        # Mass removals usually mean a kick/ban spree or a prune.
        guild = member.guild
        s = self._settings(guild)
        now = datetime.now(timezone.utc)
        dq = self.removals[guild.id]
        window = s["nuke_remove_window"]
        while dq and (now - dq[0]).total_seconds() > window:
            dq.popleft()
        dq.append(now)
        if len(dq) >= s["nuke_remove_threshold"]:
            dq.clear()
            actor = await self._recent_audit_user(guild, _PRUNE_ACTIONS, seconds=120)
            await self._nuke_trigger(
                guild, actor,
                f"mass member removal ({s['nuke_remove_threshold']}+ left in {window}s)")

    # ---------------- panic internals (also used by the dashboard API) ----------------

    def _panic_targets(self, guild):
        g = self.bot.store.guild(guild.id)
        roles = [guild.default_role]
        member_role = guild.get_role(g["roles"].get("member", 0))
        if member_role:
            roles.append(member_role)
        return roles

    async def apply_panic(self, guild, full, by):
        """Lock the server down. full=True also freezes voice channels."""
        if guild.id in self.panic_state:
            return "The server is already in panic mode. Use /unlock first."
        targets = self._panic_targets(guild)
        saved = {"text": {}, "voice": {}}
        problems = 0

        for ch in guild.text_channels:
            ch_saved = {}
            for role in targets:
                ow = ch.overwrites_for(role)
                ch_saved[str(role.id)] = {"send_messages": ow.send_messages}
                try:
                    await ch.set_permissions(role, send_messages=False,
                                             reason=f"Lynxo Bot panic by {by}")
                except (discord.Forbidden, discord.HTTPException):
                    problems += 1
            saved["text"][str(ch.id)] = ch_saved

        if full:
            for ch in guild.voice_channels:
                ch_saved = {}
                for role in targets:
                    ow = ch.overwrites_for(role)
                    ch_saved[str(role.id)] = {"connect": ow.connect,
                                              "speak": ow.speak}
                    try:
                        await ch.set_permissions(role, connect=False, speak=False,
                                                 reason=f"Lynxo Bot lockdown by {by}")
                    except (discord.Forbidden, discord.HTTPException):
                        problems += 1
                saved["voice"][str(ch.id)] = ch_saved

        # Delete invites so nobody new can get in.
        deleted = 0
        try:
            for invite in await guild.invites():
                try:
                    await invite.delete(reason=f"Lynxo Bot panic by {by}")
                    deleted += 1
                except discord.HTTPException:
                    pass
        except (discord.Forbidden, discord.HTTPException):
            pass

        # Pause the verify buttons by reusing the raid lockdown flag.
        raid = self.bot.get_cog("Raid")
        if raid is not None:
            raid.lockdown_until[guild.id] = (
                datetime.now(timezone.utc) + timedelta(hours=24))

        state = {"full": full, "by": str(by), "at": now_str(),
                 "channels": saved}
        self.panic_state[guild.id] = state
        g = self.bot.store.guild(guild.id)
        g["panic"] = state
        self.bot.store.save()

        mode = "LOCKDOWN (full freeze)" if full else "PANIC (partial lockdown)"
        msg = (f"{mode} — {by} locked the server. "
               f"{'Nobody can talk in text or join voice. ' if full else 'Nobody can talk in text channels. '}"
               f"Deleted {deleted} invite(s); verification buttons are paused."
               + (f" ({problems} channel(s) could not be locked)" if problems else ""))
        await mod_log(self.bot, guild, msg)
        if guild.owner:
            await dm_user(guild.owner, f"[{guild.name}] {msg}")
        return ("Lockdown is on. " if full else "Panic mode is on. ") + \
               "Use /unlock when things are calm again."

    async def release(self, guild, by):
        """Undo /panic or /lockdown, restoring saved permissions."""
        state = self.panic_state.get(guild.id)
        if not state:
            return "The server is not in panic mode."
        saved = state.get("channels", {})
        problems = 0

        for ch_id, roles in saved.get("text", {}).items():
            ch = guild.get_channel(int(ch_id))
            if ch is None:
                continue
            for role_id, perms in roles.items():
                role = guild.get_role(int(role_id))
                if role is None:
                    # @everyone may fail get_role lookup by id; fall back.
                    role = guild.default_role if int(role_id) == guild.id else None
                if role is None:
                    continue
                try:
                    await ch.set_permissions(
                        role, send_messages=perms.get("send_messages"),
                        reason=f"Lynxo Bot unlock by {by}")
                except (discord.Forbidden, discord.HTTPException):
                    problems += 1

        for ch_id, roles in saved.get("voice", {}).items():
            ch = guild.get_channel(int(ch_id))
            if ch is None:
                continue
            for role_id, perms in roles.items():
                role = guild.get_role(int(role_id))
                if role is None:
                    role = guild.default_role if int(role_id) == guild.id else None
                if role is None:
                    continue
                try:
                    await ch.set_permissions(
                        role, connect=perms.get("connect"), speak=perms.get("speak"),
                        reason=f"Lynxo Bot unlock by {by}")
                except (discord.Forbidden, discord.HTTPException):
                    problems += 1

        # Unpause the verify buttons.
        raid = self.bot.get_cog("Raid")
        if raid is not None:
            raid.lockdown_until.pop(guild.id, None)

        self.panic_state.pop(guild.id, None)
        g = self.bot.store.guild(guild.id)
        g["panic"] = {}
        self.bot.store.save()

        msg = (f"UNLOCK — {by} released panic mode. Talking permissions were "
               f"restored."
               + (f" ({problems} channel(s) could not be restored — check them by hand)"
                  if problems else ""))
        await mod_log(self.bot, guild, msg)
        return "Panic mode is off — everything is back to normal."

    # ---------------- slash commands ----------------

    @app_commands.command(name="panic", description="Emergency: lock text channels and kill invites.")
    @app_commands.checks.has_permissions(administrator=True)
    async def panic(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        msg = await self.apply_panic(interaction.guild, full=False,
                                     by=interaction.user)
        await interaction.followup.send(msg, ephemeral=True)

    @app_commands.command(name="lockdown", description="Emergency: full freeze, text AND voice.")
    @app_commands.checks.has_permissions(administrator=True)
    async def lockdown(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        msg = await self.apply_panic(interaction.guild, full=True,
                                     by=interaction.user)
        await interaction.followup.send(msg, ephemeral=True)

    @app_commands.command(name="unlock", description="Release panic/lockdown and restore permissions.")
    @app_commands.checks.has_permissions(administrator=True)
    async def unlock(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        msg = await self.release(interaction.guild, by=interaction.user)
        await interaction.followup.send(msg, ephemeral=True)

    # ---------------- whitelist / tuning ----------------

    nuke = app_commands.Group(name="nuke", description="Anti-nuke controls.")

    @nuke.command(name="status", description="Show anti-nuke settings and state.")
    @app_commands.checks.has_permissions(administrator=True)
    async def nuke_status(self, interaction: discord.Interaction):
        s = self._settings(interaction.guild)
        g = self.bot.store.guild(interaction.guild.id)
        wl = g.get("nuke_whitelist", {}) or {}
        panicking = interaction.guild.id in self.panic_state
        lines = [
            f"Anti-nuke is ON. Punishment: {s['nuke_action']}. "
            f"Auto-restore from backup: {'ON' if s['nuke_auto_restore'] else 'OFF'}.",
            f"Triggers: {s['nuke_channel_threshold']} channel changes / "
            f"{s['nuke_channel_window']}s, {s['nuke_role_threshold']} role changes / "
            f"{s['nuke_role_window']}s, {s['nuke_webhook_threshold']} webhooks / "
            f"{s['nuke_webhook_window']}s, {s['nuke_remove_threshold']} removals / "
            f"{s['nuke_remove_window']}s.",
            f"Exempt: {len(wl.get('users', []))} user(s), {len(wl.get('roles', []))} role(s) "
            f"(plus the owner and the bot itself).",
            f"Panic mode: {'ACTIVE' if panicking else 'off'}.",
        ]
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    @nuke.command(name="exempt_add", description="Exempt a user or role from anti-nuke.")
    @app_commands.checks.has_permissions(administrator=True)
    async def nuke_exempt_add(self, interaction: discord.Interaction,
                              user: discord.User = None, role: discord.Role = None):
        if user is None and role is None:
            await interaction.response.send_message(
                "Pick a user or a role to exempt.", ephemeral=True)
            return
        g = self.bot.store.guild(interaction.guild.id)
        wl = g.setdefault("nuke_whitelist", {"users": [], "roles": []})
        what = ""
        if user is not None and user.id not in wl["users"]:
            wl["users"].append(user.id)
            what = f"user {user}"
        if role is not None and role.id not in wl["roles"]:
            wl["roles"].append(role.id)
            what = f"role @{role.name}" if not what else what + f" and role @{role.name}"
        self.bot.store.save()
        await interaction.response.send_message(
            f"Exempted {what} from anti-nuke.", ephemeral=True)

    @nuke.command(name="exempt_remove", description="Remove an anti-nuke exemption.")
    @app_commands.checks.has_permissions(administrator=True)
    async def nuke_exempt_remove(self, interaction: discord.Interaction,
                                 user: discord.User = None, role: discord.Role = None):
        g = self.bot.store.guild(interaction.guild.id)
        wl = g.setdefault("nuke_whitelist", {"users": [], "roles": []})
        if user is not None and user.id in wl["users"]:
            wl["users"].remove(user.id)
        if role is not None and role.id in wl["roles"]:
            wl["roles"].remove(role.id)
        self.bot.store.save()
        await interaction.response.send_message("Exemption removed.", ephemeral=True)

    @nuke.command(name="exempt_list", description="List anti-nuke exemptions.")
    @app_commands.checks.has_permissions(administrator=True)
    async def nuke_exempt_list(self, interaction: discord.Interaction):
        g = self.bot.store.guild(interaction.guild.id)
        wl = g.get("nuke_whitelist", {}) or {}
        lines = ["Exempt users:"]
        for uid in wl.get("users", []):
            u = self.bot.get_user(uid)
            lines.append(f"- {u} ({uid})" if u else f"- ({uid})")
        lines.append("Exempt roles:")
        for rid in wl.get("roles", []):
            r = interaction.guild.get_role(rid)
            lines.append(f"- @{r.name}" if r else f"- ({rid})")
        lines.append("(The server owner and the bot are always exempt.)")
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    @nuke.command(name="action", description="What to do to a nuker: strip roles or ban.")
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.choices(choice=[
        app_commands.Choice(name="Strip roles", value="strip"),
        app_commands.Choice(name="Ban", value="ban"),
    ])
    async def nuke_action(self, interaction: discord.Interaction, choice: str):
        s = self._settings(interaction.guild)
        s["nuke_action"] = choice
        self.bot.store.save()
        await interaction.response.send_message(
            f"Anti-nuke punishment is now: {choice}.", ephemeral=True)

    @nuke.command(name="auto_restore", description="Toggle auto-restore from backup after a nuke.")
    @app_commands.checks.has_permissions(administrator=True)
    async def nuke_auto_restore(self, interaction: discord.Interaction):
        s = self._settings(interaction.guild)
        s["nuke_auto_restore"] = not s["nuke_auto_restore"]
        self.bot.store.save()
        await interaction.response.send_message(
            f"Auto-restore is now {'ON' if s['nuke_auto_restore'] else 'OFF'}.",
            ephemeral=True)


async def setup(bot):
    await bot.add_cog(AntiNuke(bot))
