"""Reaction roles: members tap a reaction on a bot-posted message to
give themselves (or remove) a role. Configured from the dashboard.
Supports multiple embeds per server."""
import sys
import time
import uuid

import discord
from discord.ext import commands


class ReactionRoles(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    def _config(self, guild):
        g = self.bot.store.guild(guild.id)
        cfg = g.get("reaction_roles") or {}
        # Migrate old single-embed format to the embeds list.
        if "embeds" not in cfg:
            embeds = []
            if cfg.get("channel_id") or cfg.get("mappings"):
                embeds.append({
                    "id": str(uuid.uuid4()),
                    "name": "Reaction Roles",
                    "channel_id": cfg.get("channel_id"),
                    "message_id": cfg.get("message_id"),
                    "title": cfg.get("title"),
                    "description": cfg.get("description"),
                    "color": cfg.get("color"),
                    "image_url": cfg.get("image_url"),
                    "mappings": cfg.get("mappings") or [],
                })
            cfg = {"embeds": embeds}
            g["reaction_roles"] = cfg
            self.bot.store.save()
        if not isinstance(cfg.get("embeds"), list):
            cfg["embeds"] = []
        return cfg

    def _get_embed(self, cfg, embed_id):
        for e in cfg.get("embeds", []):
            if str(e.get("id")) == str(embed_id):
                return e
        return None

    def _find_mapping(self, embed, emoji_str):
        for m in embed.get("mappings", []):
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
        embed = None
        for e in cfg.get("embeds", []):
            if e.get("message_id") and payload.message_id == e["message_id"]:
                embed = e
                break
        if embed is None:
            return
        mapping = self._find_mapping(embed, str(payload.emoji))
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

    def _build_embed(self, guild, embed):
        lines = []
        for m in embed.get("mappings", []):
            label = str(m.get("label") or "").strip()
            role = guild.get_role(int(m.get("role_id", 0)))
            if not label and role is not None:
                label = role.name
            lines.append(f"{m.get('emoji')} — {label or 'role'}")
        description = str(embed.get("description") or "")
        if lines:
            description = (description + "\n\n" + "\n".join(lines)).strip()
        emb = discord.Embed(
            title=str(embed.get("title") or "Pick your roles"),
            description=description or "Tap a reaction to pick a role.",
            color=int(embed.get("color") or 16766720),
        )
        image_url = str(embed.get("image_url") or "").strip()
        if image_url:
            emb.set_image(url=image_url)
        return emb

    async def post_reaction_message(self, guild, embed_id):
        """Post (or update) one reaction-role embed. Returns the message."""
        cfg = self._config(guild)
        embed = self._get_embed(cfg, embed_id)
        if embed is None:
            raise ValueError("Embed not found.")
        channel_id = embed.get("channel_id")
        channel = guild.get_channel(channel_id) if channel_id else None
        if channel is None:
            raise ValueError("No reaction-role channel is set.")
        emb = self._build_embed(guild, embed)
        message = None
        if embed.get("message_id"):
            try:
                message = await channel.fetch_message(embed["message_id"])
            except (discord.NotFound, discord.HTTPException):
                message = None
        if message is None:
            message = await channel.send(embed=emb)
        else:
            await message.edit(embed=emb)
        try:
            await message.clear_reactions()
        except (discord.Forbidden, discord.HTTPException):
            pass
        for m in embed.get("mappings", []):
            emoji = str(m.get("emoji", "")).strip()
            if not emoji:
                continue
            try:
                await message.add_reaction(emoji)
            except (discord.Forbidden, discord.HTTPException,
                    discord.InvalidArgument) as e:
                print(f"ReactionRoles: could not add reaction {emoji!r} in "
                      f"{guild.name}: {e}", file=sys.stderr, flush=True)
        embed["message_id"] = message.id
        self.bot.store.save()
        return message

    async def delete_reaction_message(self, guild, embed_id):
        """Delete one embed's posted message (if any) and clear its id."""
        cfg = self._config(guild)
        embed = self._get_embed(cfg, embed_id)
        if embed is None:
            return False
        message_id = embed.get("message_id")
        if not message_id:
            return False
        channel = (guild.get_channel(embed["channel_id"])
                   if embed.get("channel_id") else None)
        deleted = False
        if channel is not None:
            try:
                message = await channel.fetch_message(message_id)
                await message.delete()
                deleted = True
            except (discord.NotFound, discord.Forbidden,
                    discord.HTTPException):
                pass
        embed["message_id"] = None
        self.bot.store.save()
        return deleted

    def remove_embed(self, guild, embed_id):
        """Remove an embed config entirely. Returns True if removed."""
        cfg = self._config(guild)
        before = len(cfg.get("embeds", []))
        cfg["embeds"] = [e for e in cfg.get("embeds", [])
                         if str(e.get("id")) != str(embed_id)]
        if len(cfg["embeds"]) != before:
            self.bot.store.save()
            return True
        return False

    def new_embed_id(self):
        return str(uuid.uuid4())

    def touch(self, guild):
        """Ensure migration ran; returns the config."""
        return self._config(guild)
