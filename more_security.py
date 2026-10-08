"""More security + extra mod commands (Phase 4).

Self-contained cog; does not modify any other file. New settings keys are
read with .get() fallbacks so old guild blobs keep working without a
config.py change.

Features:
  1. Mass user-mention filter (N+ distinct user mentions in one message).
  2. Voice raid protection (mass disconnect/mute/deafen via audit log).
  3. Auto-slowmode when a channel gets too hot, reset when it calms down.
  4. Raid pattern detection (similar usernames joining in a short window).
  5. Mod commands: /tempban, /softban, /unban, /slowmode.
"""
import re
import sys
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands, tasks

from utils import dm_user, mod_log, user_label

# Fallback defaults for settings keys that may not exist in old blobs.
_DEFAULTS = {
    "mass_mention_filter": True,
    "mass_mention_count": 10,
    "voice_raid_protection": True,
    "heat_slowmode": True,
    "heat_slowmode_seconds": 10,
    "raid_pattern_check": True,
}

VOICE_TRIPWIRE_EVENTS = 25   # voice state changes in the window -> audit scan
VOICE_TRIPWIRE_WINDOW = 60   # seconds
VOICE_AUDIT_WINDOW = 300     # seconds of audit log to scan
VOICE_OFFENDER_THRESHOLD = 10  # actions by one user in the window -> nuke path
VOICE_SCAN_COOLDOWN = 300    # seconds between audit scans per guild

SLOWMODE_WINDOW = 30         # seconds: message-rate window for slowmode trigger
SLOWMODE_CALM_WINDOW = 60    # seconds: rate window checked before lifting
SLOWMODE_CALM_COUNT = 5      # fewer than this many msgs in the calm window -> lift

PATTERN_JOIN_WINDOW = 600    # seconds: join window for pattern detection
PATTERN_JOIN_COUNT = 5       # joins inside the window before checking names
PATTERN_NAME_HITS = 3        # similar names needed to raise the alert
PATTERN_ALERT_COOLDOWN = 1800  # seconds between pattern alerts per guild
RANDOM_NAME_RE = re.compile(r"^[a-z]{2,}\d{3,}$")


class MoreSecurity(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # guild_id -> deque of monotonic timestamps (voice state changes)
        self._voice_events = defaultdict(deque)
        self._voice_scan_at = {}          # guild_id -> monotonic time of last scan
        # channel_id -> deque of monotonic timestamps (message activity)
        self._chan_activity = defaultdict(deque)
        self._slowed = {}                 # channel_id -> slowmode seconds we set
        # guild_id -> deque of (monotonic time, lowercase username)
        self._joins = defaultdict(deque)
        self._pattern_alerted_at = {}     # guild_id -> monotonic time

    # ---------------- settings helper ----------------

    def _get(self, guild, key):
        s = self.bot.store.guild(guild.id)["settings"]
        return s.get(key, _DEFAULTS[key])

    def _slow_threshold(self, guild):
        s = self.bot.store.guild(guild.id)["settings"]
        # None means "automatic" — fall through to the AutoMod heat value.
        if s.get("heat_slowmode_threshold") is not None:
            return s["heat_slowmode_threshold"]
        am = self.bot.get_cog("AutoMod")
        if am is not None:
            try:
                return am._settings(guild)["spam_warn_heat"]
            except Exception:  # noqa: BLE001
                pass
        return 5.0

    async def cog_load(self):
        self.slowmode_reset.start()
        self.tempban_sweep.start()

    def cog_unload(self):
        self.slowmode_reset.cancel()
        self.tempban_sweep.cancel()

    # ---------------- 1. mass user-mention filter ----------------

    @commands.Cog.listener()
    async def on_message(self, message):
        guild = message.guild
        if guild is None or message.author.bot:
            return
        member = message.author
        if member.guild_permissions.manage_messages:
            return  # mods are exempt, same as the other filters
        try:
            await self._mass_mention_check(message)
        except Exception as e:  # noqa: BLE001
            print(f"more_security mass-mention error: {e!r}", flush=True)
        try:
            await self._slowmode_track(message)
        except Exception as e:  # noqa: BLE001
            print(f"more_security slowmode error: {e!r}", flush=True)

    async def _mass_mention_check(self, message):
        guild = message.guild
        if not self._get(guild, "mass_mention_filter"):
            return
        # message.mentions is already de-duplicated by discord.py.
        if len(message.mentions) < self._get(guild, "mass_mention_count"):
            return
        am = self.bot.get_cog("AutoMod")
        if am is not None:
            # Don't let the delete event double-strike as a ghost ping.
            am._everyone_pings.pop(message.id, None)
        # Delete first: if it's already gone, auto-mod's heat check already
        # handled it — return without striking twice.
        try:
            await message.delete()
        except (discord.Forbidden, discord.HTTPException):
            return
        member = message.author
        n = 0
        if am is not None:
            n = await am.add_strike(guild, member, "Auto-mod: mass user mentions")
        await dm_user(
            member,
            f"Your message in {guild.name} was removed for pinging too many "
            f"people at once." + (f" This is strike {n}." if n else ""),
        )

    # ---------------- 3. auto-slowmode on heat ----------------

    async def _slowmode_track(self, message):
        guild = message.guild
        if not self._get(guild, "heat_slowmode"):
            return
        ch = message.channel
        if not isinstance(ch, discord.TextChannel):
            return
        now = time.monotonic()
        dq = self._chan_activity[ch.id]
        while dq and now - dq[0] > SLOWMODE_CALM_WINDOW:
            dq.popleft()
        dq.append(now)
        recent = sum(1 for t in dq if now - t <= SLOWMODE_WINDOW)
        if recent < self._slow_threshold(guild):
            return
        if ch.slowmode_delay != 0:
            return  # a mod already set slowmode; don't fight them
        me = guild.me
        if me is None or not ch.permissions_for(me).manage_channels:
            return
        seconds = self._get(guild, "heat_slowmode_seconds")
        try:
            await ch.edit(slowmode_delay=seconds,
                          reason="Lynxo Bot: auto-slowmode on spam heat")
        except (discord.Forbidden, discord.HTTPException):
            return
        self._slowed[ch.id] = seconds
        try:
            await ch.send("Slowing this channel down for a bit — too much heat.")
        except discord.HTTPException:
            pass
        await mod_log(self.bot, guild,
                      f"AUTO-SLOWMODE — #{ch.name} set to {seconds}s "
                      f"({recent} messages in {SLOWMODE_WINDOW}s).")

    @tasks.loop(seconds=60)
    async def slowmode_reset(self):
        """Lift auto-slowmode once a channel calms down."""
        for ch_id, seconds in list(self._slowed.items()):
            ch = self.bot.get_channel(ch_id)
            if ch is None or not isinstance(ch, discord.TextChannel):
                self._slowed.pop(ch_id, None)
                continue
            now = time.monotonic()
            dq = self._chan_activity[ch_id]
            while dq and now - dq[0] > SLOWMODE_CALM_WINDOW:
                dq.popleft()
            # Only touch it if it's still the value we set (a mod may have
            # changed it by hand since).
            if len(dq) < SLOWMODE_CALM_COUNT and ch.slowmode_delay == seconds:
                me = ch.guild.me if ch.guild else None
                if me is not None and ch.permissions_for(me).manage_channels:
                    try:
                        await ch.edit(slowmode_delay=0,
                                      reason="Lynxo Bot: chat calmed down")
                        await mod_log(self.bot, ch.guild,
                                      f"AUTO-SLOWMODE OFF — #{ch.name} is back to normal.")
                    except (discord.Forbidden, discord.HTTPException):
                        pass
                self._slowed.pop(ch_id, None)

    @slowmode_reset.before_loop
    async def _slowmode_before(self):
        await self.bot.wait_until_ready()

    # ---------------- 2. voice raid protection ----------------

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        guild = member.guild
        if guild is None:
            return
        try:
            if not self._get(guild, "voice_raid_protection"):
                return
            # Ignore no-op updates.
            if (before.channel == after.channel
                    and before.mute == after.mute
                    and before.deaf == after.deaf
                    and before.self_mute == after.self_mute
                    and before.self_deaf == after.self_deaf):
                return
            now = time.monotonic()
            dq = self._voice_events[guild.id]
            while dq and now - dq[0] > VOICE_TRIPWIRE_WINDOW:
                dq.popleft()
            dq.append(now)
            if len(dq) < VOICE_TRIPWIRE_EVENTS:
                return
            if now - self._voice_scan_at.get(guild.id, 0) < VOICE_SCAN_COOLDOWN:
                return
            self._voice_scan_at[guild.id] = now
            await self._voice_raid_scan(guild)
        except Exception as e:  # noqa: BLE001
            print(f"more_security voice error: {e!r}", file=sys.stderr, flush=True)

    @staticmethod
    def _is_voice_mute_entry(entry):
        """True if a member_update audit entry changed server mute/deaf."""
        for attr in ("mute", "deaf"):
            b = getattr(entry.before, attr, None)
            a = getattr(entry.after, attr, None)
            if b is not None and a is not None and b != a:
                return True
        return False

    async def _voice_raid_scan(self, guild):
        """Find one user doing mass voice kicks/mutes and route to anti-nuke."""
        counts = defaultdict(int)
        users = {}
        now = datetime.now(timezone.utc)
        member_disconnect = getattr(discord.AuditLogAction, "member_disconnect", None)
        member_move = getattr(discord.AuditLogAction, "member_move", None)
        member_update = getattr(discord.AuditLogAction, "member_update", None)
        try:
            async for entry in guild.audit_logs(limit=30):
                age = (now - entry.created_at).total_seconds()
                if age > VOICE_AUDIT_WINDOW:
                    break
                act = entry.action
                if act == member_disconnect or act == member_move:
                    voice_action = True
                elif act == member_update and self._is_voice_mute_entry(entry):
                    voice_action = True
                else:
                    continue
                if voice_action and entry.user is not None:
                    counts[entry.user.id] += 1
                    users[entry.user.id] = entry.user
        except (discord.Forbidden, discord.HTTPException) as e:
            print(f"more_security voice audit read failed: {e!r}",
                  file=sys.stderr, flush=True)
            return
        except Exception as e:  # noqa: BLE001
            print(f"more_security voice scan error: {e!r}",
                  file=sys.stderr, flush=True)
            return
        for uid, n in counts.items():
            if n >= VOICE_OFFENDER_THRESHOLD:
                an = self.bot.get_cog("AntiNuke")
                if an is None:
                    await mod_log(
                        self.bot, guild,
                        f"VOICE RAID? — {user_label(users[uid])} did {n} voice "
                        f"kicks/mutes in 5 minutes. Anti-nuke cog is missing, "
                        f"so nothing was done automatically — please check.")
                    return
                await an._nuke_trigger(
                    guild, users[uid],
                    f"mass voice actions ({n} disconnects/mutes in 5 minutes)")
                return

    # ---------------- 4. raid pattern detection ----------------

    @commands.Cog.listener()
    async def on_member_join(self, member):
        if member.bot:
            return
        guild = member.guild
        try:
            if not self._get(guild, "raid_pattern_check"):
                return
            now = time.monotonic()
            dq = self._joins[guild.id]
            while dq and now - dq[0][0] > PATTERN_JOIN_WINDOW:
                dq.popleft()
            dq.append((now, member.name.lower()))
            if len(dq) < PATTERN_JOIN_COUNT:
                return
            if now - self._pattern_alerted_at.get(guild.id, 0) < PATTERN_ALERT_COOLDOWN:
                return
            names = [n for _, n in dq]
            prefixes = defaultdict(int)
            for n in names:
                if len(n) >= 6:
                    prefixes[n[:6]] += 1
            randomish = sum(1 for n in names if RANDOM_NAME_RE.match(n))
            if randomish < PATTERN_NAME_HITS and not any(
                    c >= PATTERN_NAME_HITS for c in prefixes.values()):
                return
            self._pattern_alerted_at[guild.id] = now
            sample = ", ".join(names[:5])
            msg = (f"RAID PATTERN ALERT — {len(dq)} joins in the last 10 minutes "
                   f"with similar usernames (e.g. {sample}). Possible raid — "
                   f"please review the member list.")
            await mod_log(self.bot, guild, msg)
            if guild.owner:
                await dm_user(guild.owner, f"[{guild.name}] {msg}")
            raid = self.bot.get_cog("Raid")
            if raid is not None and not raid.is_lockdown(guild.id):
                await raid.trigger(guild, len(dq))
        except Exception as e:  # noqa: BLE001
            print(f"more_security pattern error: {e!r}", file=sys.stderr, flush=True)

    # ---------------- 5. mod commands ----------------

    @app_commands.command(name="tempban", description="Ban a member for a set number of minutes.")
    @app_commands.checks.has_permissions(ban_members=True)
    async def tempban(self, interaction: discord.Interaction,
                      member: discord.Member,
                      duration_minutes: app_commands.Range[int, 1, 10080],
                      reason: str = "No reason given"):
        await interaction.response.defer(ephemeral=True)
        try:
            await member.ban(
                reason=f"Tempban ({duration_minutes}m) by {interaction.user}: {reason}",
                delete_message_days=1)
        except (discord.Forbidden, discord.HTTPException) as e:
            await interaction.followup.send(f"Could not ban: {e}", ephemeral=True)
            return
        g = self.bot.store.guild(interaction.guild.id)
        tb = g.setdefault("tempbans", {})
        until = datetime.now(timezone.utc) + timedelta(minutes=duration_minutes)
        tb[str(member.id)] = {"until": until.isoformat(), "reason": reason,
                              "by": str(interaction.user)}
        self.bot.store.save()
        await mod_log(self.bot, interaction.guild,
                      f"TEMPBAN — {user_label(member)} — {duration_minutes} minute(s) "
                      f"— reason: {reason} — by {interaction.user}")
        await interaction.followup.send(
            f"{member.mention} banned for {duration_minutes} minutes.", ephemeral=True)

    @app_commands.command(name="softban", description="Ban then instantly unban (wipes their messages).")
    @app_commands.checks.has_permissions(ban_members=True)
    async def softban(self, interaction: discord.Interaction,
                      member: discord.Member, reason: str = "No reason given"):
        await interaction.response.defer(ephemeral=True)
        try:
            await member.ban(
                reason=f"Softban by {interaction.user}: {reason}",
                delete_message_days=7)
            await interaction.guild.unban(member, reason="Softban release")
        except (discord.Forbidden, discord.HTTPException) as e:
            await interaction.followup.send(f"Could not softban: {e}", ephemeral=True)
            return
        await mod_log(self.bot, interaction.guild,
                      f"SOFTBAN — {user_label(member)} — reason: {reason} "
                      f"— by {interaction.user}")
        await interaction.followup.send(
            f"{member.mention} softbanned (their recent messages were wiped).",
            ephemeral=True)

    @app_commands.command(name="unban", description="Unban a user by their ID.")
    @app_commands.checks.has_permissions(ban_members=True)
    async def unban(self, interaction: discord.Interaction,
                    user_id: str, reason: str = "No reason given"):
        await interaction.response.defer(ephemeral=True)
        try:
            uid = int(user_id.strip())
        except (ValueError, AttributeError):
            await interaction.followup.send(
                "That doesn't look like a user ID — it should be all numbers.",
                ephemeral=True)
            return
        try:
            await interaction.guild.unban(
                discord.Object(id=uid),
                reason=f"Unbanned by {interaction.user}: {reason}")
        except discord.NotFound:
            await interaction.followup.send("That user isn't banned.", ephemeral=True)
            return
        except (discord.Forbidden, discord.HTTPException) as e:
            await interaction.followup.send(f"Could not unban: {e}", ephemeral=True)
            return
        # Drop any pending tempban for them too.
        g = self.bot.store.guild(interaction.guild.id)
        if str(uid) in g.get("tempbans", {}):
            g["tempbans"].pop(str(uid))
            self.bot.store.save()
        await mod_log(self.bot, interaction.guild,
                      f"UNBAN — user id {uid} — reason: {reason} "
                      f"— by {interaction.user}")
        await interaction.followup.send(f"Unbanned user {uid}.", ephemeral=True)

    @app_commands.command(name="slowmode", description="Set slowmode on this channel (0 = off).")
    @app_commands.checks.has_permissions(manage_channels=True)
    async def slowmode(self, interaction: discord.Interaction,
                       seconds: app_commands.Range[int, 0, 21600]):
        ch = interaction.channel
        if not isinstance(ch, discord.TextChannel):
            await interaction.response.send_message(
                "Slowmode only works in text channels.", ephemeral=True)
            return
        try:
            await ch.edit(slowmode_delay=seconds,
                          reason=f"Slowmode by {interaction.user}")
        except (discord.Forbidden, discord.HTTPException) as e:
            await interaction.response.send_message(
                f"Could not set slowmode: {e}", ephemeral=True)
            return
        # Don't let the auto-slowmode reset loop fight a mod's manual setting.
        self._slowed.pop(ch.id, None)
        await mod_log(self.bot, interaction.guild,
                      f"SLOWMODE — #{ch.name} set to {seconds}s — by {interaction.user}")
        await interaction.response.send_message(
            f"Slowmode in #{ch.name} is now {seconds} seconds.", ephemeral=True)

    @tasks.loop(hours=1)
    async def tempban_sweep(self):
        """Unban expired tempbans (also catches up after a restart)."""
        now = datetime.now(timezone.utc)
        try:
            guild_ids = self.bot.store.setup_guilds()
        except Exception as e:  # noqa: BLE001
            print(f"more_security tempban sweep error: {e!r}",
                  file=sys.stderr, flush=True)
            return
        for gid in guild_ids:
            guild = self.bot.get_guild(gid)
            try:
                g = self.bot.store.guild(gid)
            except Exception:  # noqa: BLE001
                continue
            tb = g.get("tempbans", {})
            expired = []
            for uid, rec in list(tb.items()):
                try:
                    until = datetime.fromisoformat(rec.get("until", ""))
                    if until.tzinfo is None:
                        until = until.replace(tzinfo=timezone.utc)
                except (ValueError, TypeError, AttributeError):
                    expired.append(uid)  # broken record: drop it
                    continue
                if until <= now:
                    expired.append(uid)
            for uid in expired:
                rec = tb.pop(uid, {})
                if guild is not None:
                    try:
                        await guild.unban(discord.Object(id=int(uid)),
                                          reason="Tempban expired")
                        await mod_log(
                            self.bot, guild,
                            f"UNBAN — user id {uid} — tempban expired "
                            f"(was: {rec.get('reason', 'no reason')}).")
                    except (discord.NotFound, discord.Forbidden,
                            discord.HTTPException):
                        pass
                    except (ValueError, TypeError):
                        pass
            if expired:
                try:
                    self.bot.store.save()
                except Exception:  # noqa: BLE001
                    pass

    @tempban_sweep.before_loop
    async def _tempban_before(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    await bot.add_cog(MoreSecurity(bot))
