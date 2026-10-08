"""Server backups: snapshot, list, restore with confirmation (Phase 3).

A snapshot is a JSON file describing the server's roles and channels
(names, positions, permissions, overwrites). Because Render's free disk
wipes on restart, /backup ALSO sends the file to you as a Discord
attachment — that file is the real backup. Keep it somewhere safe.

/restore takes an uploaded snapshot file, shows you what it will do, and
only rebuilds after you press "Yes, restore". Things that already exist
(matched by name) are skipped, never duplicated.
"""
import io
import json
import os
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from utils import dm_user, mod_log, now_str, user_label

BACKUP_DIR = os.environ.get("BACKUP_PATH", "backups")
SNAPSHOT_VERSION = 1

_TYPE_NAMES = {
    discord.ChannelType.text: "text",
    discord.ChannelType.voice: "voice",
    discord.ChannelType.category: "category",
    discord.ChannelType.forum: "forum",
    discord.ChannelType.stage_voice: "stage",
}


def _chan_type(channel):
    return _TYPE_NAMES.get(channel.type, "text")


class Backups(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        os.makedirs(BACKUP_DIR, exist_ok=True)

    # ---------------- snapshot building ----------------

    def build_snapshot(self, guild, by=""):
        roles = []
        for r in sorted(guild.roles, key=lambda r: r.position):
            if r.is_default() or r.managed:
                continue  # @everyone is implicit; bot roles can't be recreated
            roles.append({
                "name": r.name,
                "color": r.color.value,
                "permissions": r.permissions.value,
                "position": r.position,
                "hoist": r.hoist,
                "mentionable": r.mentionable,
            })
        channels = []
        for ch in sorted(guild.channels, key=lambda c: c.position):
            overwrites = []
            for target, ow in ch.overwrites.items():
                allow, deny = ow.pair()
                overwrites.append({
                    "target_type": "role" if isinstance(target, discord.Role) else "member",
                    "target_name": target.name,
                    "target_id": target.id,
                    "allow": allow.value,
                    "deny": deny.value,
                })
            cat = getattr(ch, "category", None)
            channels.append({
                "name": ch.name,
                "type": _chan_type(ch),
                "position": ch.position,
                "category": cat.name if cat else None,
                "topic": getattr(ch, "topic", None),
                "nsfw": bool(getattr(ch, "nsfw", False)),
                "slowmode": int(getattr(ch, "slowmode_delay", 0) or 0),
                "bitrate": getattr(ch, "bitrate", None),
                "user_limit": getattr(ch, "user_limit", None),
                "overwrites": overwrites,
            })
        return {
            "version": SNAPSHOT_VERSION,
            "app": "lynxo-bot",
            "guild_id": guild.id,
            "guild_name": guild.name,
            "taken_at": now_str(),
            "taken_by": str(by),
            "roles": roles,
            "channels": channels,
        }

    def save_snapshot(self, guild, snapshot):
        ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        fname = f"lynxo_{guild.id}_{ts}.json"
        path = os.path.join(BACKUP_DIR, fname)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, indent=2)
        return fname, path

    def latest_backup_path(self, guild_id):
        prefix = f"lynxo_{guild_id}_"
        try:
            names = sorted(n for n in os.listdir(BACKUP_DIR)
                           if n.startswith(prefix) and n.endswith(".json"))
        except OSError:
            return None
        if not names:
            return None
        return os.path.join(BACKUP_DIR, names[-1])

    def list_backups(self, guild_id):
        prefix = f"lynxo_{guild_id}_"
        out = []
        try:
            for n in sorted(os.listdir(BACKUP_DIR)):
                if n.startswith(prefix) and n.endswith(".json"):
                    p = os.path.join(BACKUP_DIR, n)
                    out.append({"name": n,
                                "size_kb": round(os.path.getsize(p) / 1024, 1)})
        except OSError:
            pass
        return out

    @staticmethod
    def load_snapshot(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    # ---------------- restore ----------------

    def _resolve_overwrites(self, guild, ow_list, name_to_role):
        overwrites = {}
        skipped = 0
        for ow in ow_list:
            if ow["target_type"] == "role":
                if ow["target_id"] == guild.id:
                    target = guild.default_role
                else:
                    target = name_to_role.get(ow["target_name"])
                if target is None:
                    skipped += 1
                    continue
                overwrites[target] = discord.PermissionOverwrite.from_pair(
                    discord.Permissions(ow["allow"]),
                    discord.Permissions(ow["deny"]))
            else:
                skipped += 1  # member overwrites can't be resolved by name
        return overwrites, skipped

    async def restore_snapshot(self, guild, snap, by_label):
        """Rebuild roles/channels from a snapshot. Returns a plain-English report."""
        if snap.get("app") != "lynxo-bot" or snap.get("version") != SNAPSHOT_VERSION:
            raise ValueError("That file is not a Lynxo Bot backup.")
        created_roles, skipped_roles = [], []
        name_to_role = {r.name: r for r in guild.roles}

        for r in snap.get("roles", []):
            if r["name"] in name_to_role:
                skipped_roles.append(r["name"])
                continue
            try:
                nr = await guild.create_role(
                    name=r["name"],
                    permissions=discord.Permissions(r["permissions"]),
                    color=discord.Color(r["color"]),
                    hoist=r["hoist"], mentionable=r["mentionable"],
                    reason=f"Lynxo Bot restore by {by_label}")
                name_to_role[r["name"]] = nr
                created_roles.append(r["name"])
            except (discord.Forbidden, discord.HTTPException):
                skipped_roles.append(r["name"] + " (failed)")

        created_ch, skipped_ch, ow_skipped = [], [], 0
        # Categories first so channels can be placed inside them.
        ordered = sorted(snap.get("channels", []),
                         key=lambda c: 0 if c["type"] == "category" else 1)
        for c in ordered:
            existing = None
            for ch in guild.channels:
                if ch.name == c["name"] and _chan_type(ch) == c["type"]:
                    existing = ch
                    break
            if existing is not None:
                skipped_ch.append("#" + c["name"])
                continue
            overwrites, nskip = self._resolve_overwrites(guild, c.get("overwrites", []),
                                                         name_to_role)
            ow_skipped += nskip
            category = None
            if c.get("category"):
                category = discord.utils.get(guild.categories, name=c["category"])
            try:
                if c["type"] == "category":
                    await guild.create_category(c["name"], overwrites=overwrites,
                                                reason=f"Lynxo Bot restore by {by_label}")
                elif c["type"] == "voice":
                    await guild.create_voice_channel(
                        c["name"], category=category, overwrites=overwrites,
                        bitrate=c.get("bitrate") or 64000,
                        user_limit=c.get("user_limit") or 0,
                        reason=f"Lynxo Bot restore by {by_label}")
                elif c["type"] == "stage":
                    await guild.create_stage_channel(
                        c["name"], category=category, overwrites=overwrites,
                        reason=f"Lynxo Bot restore by {by_label}")
                else:  # text and forum fall back to text
                    await guild.create_text_channel(
                        c["name"], category=category, overwrites=overwrites,
                        topic=c.get("topic"), nsfw=c.get("nsfw", False),
                        slowmode_delay=c.get("slowmode", 0),
                        reason=f"Lynxo Bot restore by {by_label}")
                created_ch.append("#" + c["name"])
            except (discord.Forbidden, discord.HTTPException):
                skipped_ch.append("#" + c["name"] + " (failed)")

        parts = [
            f"Restore finished from the backup taken {snap.get('taken_at', 'at an unknown time')}.",
            f"Roles: created {len(created_roles)}"
            + (f" ({', '.join(created_roles)})" if created_roles else "")
            + f", skipped {len(skipped_roles)} that already exist.",
            f"Channels: created {len(created_ch)}"
            + (f" ({', '.join(created_ch)})" if created_ch else "")
            + f", skipped {len(skipped_ch)} that already exist.",
        ]
        if ow_skipped:
            parts.append(f"{ow_skipped} permission overwrite(s) could not be "
                         f"matched to a role and were skipped.")
        parts.append("Messages inside channels are NOT part of backups and "
                     "cannot be restored.")
        report = " ".join(parts)
        await mod_log(self.bot, guild, f"RESTORE — {report} — by {by_label}")
        return report

    # ---------------- slash commands ----------------

    @app_commands.command(name="backup", description="Save a snapshot of the server (roles + channels).")
    @app_commands.checks.has_permissions(administrator=True)
    async def backup(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        snap = self.build_snapshot(guild, by=interaction.user)
        fname, path = self.save_snapshot(guild, snap)
        file = discord.File(path, filename=fname)
        msg = (f"Backup saved: {len(snap['roles'])} roles, "
               f"{len(snap['channels'])} channels. "
               f"I'm sending you the file — keep it somewhere safe, because "
               f"backups stored on the bot disappear when it restarts.")
        try:
            await interaction.followup.send(msg, file=file, ephemeral=True)
        except (discord.Forbidden, discord.HTTPException):
            # Fall back to DM if the ephemeral upload fails.
            await dm_user(interaction.user, msg)
            try:
                await interaction.user.send(file=discord.File(path, filename=fname))
            except discord.HTTPException:
                pass
        await mod_log(self.bot, guild,
                      f"BACKUP — snapshot {fname} taken by {user_label(interaction.user)}.")

    @app_commands.command(name="backups", description="List saved server snapshots.")
    @app_commands.checks.has_permissions(administrator=True)
    async def backups(self, interaction: discord.Interaction):
        items = self.list_backups(interaction.guild.id)
        if not items:
            await interaction.response.send_message(
                "No backups saved on the bot right now. Run /backup to make one. "
                "(Saved backups disappear when the bot restarts — keep the file "
                "I send you.)",
                ephemeral=True)
            return
        lines = ["Saved backups (newest last):"]
        lines += [f"- {b['name']} ({b['size_kb']} KB)" for b in items]
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    @app_commands.command(name="restore", description="Restore the server from an uploaded backup file.")
    @app_commands.checks.has_permissions(administrator=True)
    async def restore(self, interaction: discord.Interaction, file: discord.Attachment):
        await interaction.response.defer(ephemeral=True)
        if not file.filename.endswith(".json"):
            await interaction.followup.send(
                "That doesn't look like a backup file — it should be a .json "
                "file from /backup.", ephemeral=True)
            return
        try:
            raw = await file.read()
            snap = json.loads(raw.decode("utf-8"))
        except Exception:  # noqa: BLE001
            await interaction.followup.send(
                "I couldn't read that file. Make sure it's a backup from /backup.",
                ephemeral=True)
            return
        if snap.get("app") != "lynxo-bot" or snap.get("version") != SNAPSHOT_VERSION:
            await interaction.followup.send(
                "That file isn't a Lynxo Bot backup.", ephemeral=True)
            return
        n_roles = len(snap.get("roles", []))
        n_ch = len(snap.get("channels", []))
        view = RestoreConfirmView(
            self, interaction.guild, snap, str(interaction.user))
        await interaction.followup.send(
            f"This backup was taken {snap.get('taken_at', 'at an unknown time')} "
            f"for \"{snap.get('guild_name', 'a server')}\". It holds {n_roles} "
            f"role(s) and {n_ch} channel(s). Things that already exist will be "
            f"skipped, never duplicated. Messages inside channels can't be "
            f"restored.\n\nAre you sure you want to restore it?",
            view=view, ephemeral=True)


class RestoreConfirmView(discord.ui.View):
    """One-shot Yes/Cancel for /restore."""

    def __init__(self, cog, guild, snap, by_label):
        super().__init__(timeout=120)
        self.cog = cog
        self.guild = guild
        self.snap = snap
        self.by_label = by_label

    @discord.ui.button(label="Yes, restore", style=discord.ButtonStyle.danger)
    async def yes(self, interaction: discord.Interaction, button: discord.ui.Button):
        for child in self.children:
            child.disabled = True
        try:
            report = await self.cog.restore_snapshot(self.guild, self.snap,
                                                     self.by_label)
        except Exception as e:  # noqa: BLE001
            report = f"Restore failed: {e}"
        await interaction.response.edit_message(content=report, view=self)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(
            content="Restore cancelled — nothing was changed.", view=self)


async def setup(bot):
    await bot.add_cog(Backups(bot))
