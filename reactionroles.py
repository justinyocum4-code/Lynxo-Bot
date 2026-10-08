"""Reaction roles: members tap a reaction on a bot-posted message to
give themselves (or remove) a role. Configured from the dashboard."""
import sys

import discord
from discord.ext import commands


class ReactionRoles(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    def _config(self, guild):
        g = self.bot.store.guild(guild.id)
        cfg = g.get("reaction_roles") or {}
        if not isinstance(cfg.get("mappings"), list):
            cfg["mappings"] = []
        return cfg

    def _find_mapping(self, cfg, emoji_str):
        for m in cfg.get("mappings", []):
            if str(m.get("emoji", "")) == emoji_str:
                return m
        return None

    async def _member_for(self, guild, user_id):
        member = guild.get_member(user_id)
        if member is None:
            try:
                member = await guild.fetch_member(user_id)
            except (discord.NotFound, discord.HTTPException):
                return None
        return member

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload):
        await self._handle(payload, add=True)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload):
        await self._handle(payload, add=False)

    async def _handle(self, payload, add):
        if self.bot.user and payload.user_id == self.bot.user.id:
            return
        if payload.guild_id is None:
            return  # DMs have no roles
        guild = self.bot.get_guild(payload.guild_id)
        if guild is None:
            return
        cfg = self._config(guild)
        if not cfg.get("message_id") or payload.message_id != cfg["message_id"]:
            return
        mapping = self._find_mapping(cfg, str(payload.emoji))
        if mapping is None:
            return
        role = guild.get_role(int(mapping.get("role_id", 0)))
        if role is None:
            print(f"ReactionRoles: role {mapping.get('role_id')} no longer "
                  f"exists in {guild.name}; skipping.", file=sys.stderr,
                  flush=True)
            return
        member = await self._member_for(guild, payload.user_id)
        if member is None or member.bot:
            return
        action = "add" if add else "remove"
        try:
            if add:
                await member.add_roles(role, reason="Lynxo Bot reaction role")
            else:
                await member.remove_roles(role,
                                          reason="Lynxo Bot reaction role")
        except (discord.Forbidden, discord.HTTPException) as e:
            print(f"ReactionRoles: could not {action} role {role.name} for "
                  f"{member} in {guild.name}: {e}", file=sys.stderr,
                  flush=True)

    async def post_reaction_message(self, guild):
        """Post (or update) the reaction-role embed. Returns the message."""
        cfg = self._config(guild)
        channel_id = cfg.get("channel_id")
        channel = guild.get_channel(channel_id) if channel_id else None
        if channel is None:
            raise ValueError("No reaction-role channel is set.")
        lines = []
        for m in cfg.get("mappings", []):
            label = str(m.get("label") or "").strip()
            role = guild.get_role(int(m.get("role_id", 0)))
            if not label and role is not None:
                label = role.name
            lines.append(f"{m.get('emoji')} — {label or 'role'}")
        description = str(cfg.get("description") or "")
        if lines:
            description = (description + "\n\n" + "\n".join(lines)).strip()
        embed = discord.Embed(
            title=str(cfg.get("title") or "Pick your roles"),
            description=description or "Tap a reaction to pick a role.",
            color=int(cfg.get("color") or 16766720),
        )
        message = None
        if cfg.get("message_id"):
            try:
                message = await channel.fetch_message(cfg["message_id"])
            except (discord.NotFound, discord.HTTPException):
                message = None
        if message is None:
            message = await channel.send(embed=embed)
        else:
            await message.edit(embed=embed)
        try:
            await message.clear_reactions()
        except (discord.Forbidden, discord.HTTPException):
            pass
        for m in cfg.get("mappings", []):
            emoji = str(m.get("emoji", "")).strip()
            if not emoji:
                continue
            try:
                await message.add_reaction(emoji)
            except (discord.Forbidden, discord.HTTPException,
                    discord.InvalidArgument) as e:
                print(f"ReactionRoles: could not add reaction {emoji!r} in "
                      f"{guild.name}: {e}", file=sys.stderr, flush=True)
        cfg["message_id"] = message.id
        self.bot.store.save()
        return message

    async def delete_reaction_message(self, guild):
        """Delete the posted message (if any) and clear the stored id."""
        cfg = self._config(guild)
        message_id = cfg.get("message_id")
        if not message_id:
            return False
        channel = (guild.get_channel(cfg["channel_id"])
                   if cfg.get("channel_id") else None)
        deleted = False
        if channel is not None:
            try:
                message = await channel.fetch_message(message_id)
                await message.delete()
                deleted = True
            except (discord.NotFound, discord.Forbidden,
                    discord.HTTPException):
                pass
        cfg["message_id"] = None
        self.bot.store.save()
        return deleted
