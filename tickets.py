"""Ticket panel: customizable button opens a private thread.
Configured from the dashboard Tickets tab."""
import sys

import discord
from discord.ext import commands

TICKET_OPEN_ID = "lynxo_ticket_open"
TICKET_CLOSE_ID = "lynxo_ticket_close"

BUTTON_STYLES = {
    "primary": discord.ButtonStyle.primary,
    "secondary": discord.ButtonStyle.secondary,
    "success": discord.ButtonStyle.success,
    "danger": discord.ButtonStyle.danger,
}

DEFAULTS = {
    "enabled": False,
    "channel_id": None,
    "message_id": None,
    "title": "Need help?",
    "description": "Press the button below to open a private support ticket. Only you and the mods can see it.",
    "color": 16766720,
    "image_url": "",
    "button_text": "Open a Ticket",
    "button_emoji": "🎫",
    "button_style": "primary",
    "welcome": "Thanks for opening a ticket! Describe your issue and a mod will be with you soon.",
    "support_role_id": None,
}


class TicketPanelView(discord.ui.View):
    """Persistent 'Open a Ticket' button."""
    def __init__(self, bot):
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(label="Open a Ticket", emoji="🎫",
                       style=discord.ButtonStyle.primary,
                       custom_id=TICKET_OPEN_ID)
    async def open_ticket(self, interaction: discord.Interaction,
                          button: discord.ui.Button):
        cog = self.bot.get_cog("Tickets")
        if cog is None:
            await interaction.response.send_message(
                "Tickets aren't available right now.", ephemeral=True)
            return
        await cog.handle_open(interaction, button)


class TicketCloseView(discord.ui.View):
    """Persistent 'Close Ticket' button inside ticket threads."""
    def __init__(self, bot):
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(label="Close Ticket", emoji="🔒",
                       style=discord.ButtonStyle.danger,
                       custom_id=TICKET_CLOSE_ID)
    async def close_ticket(self, interaction: discord.Interaction,
                           button: discord.ui.Button):
        cog = self.bot.get_cog("Tickets")
        if cog is None:
            return
        await cog.handle_close(interaction)


class Tickets(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    def _config(self, guild):
        g = self.bot.store.guild(guild.id)
        cfg = g.setdefault("ticket_panel", {})
        for k, v in DEFAULTS.items():
            cfg.setdefault(k, v)
        # tickets map: user_id str -> thread_id (shared with /newticket)
        g.setdefault("tickets", {})
        return cfg

    def _save(self):
        self.bot.store.save()

    def _panel_view(self, cfg):
        view = discord.ui.View(timeout=None)
        style = BUTTON_STYLES.get(cfg.get("button_style"), discord.ButtonStyle.primary)
        btn = discord.ui.Button(
            label=str(cfg.get("button_text") or "Open a Ticket")[:80],
            style=style,
            custom_id=TICKET_OPEN_ID,
        )
        emoji = str(cfg.get("button_emoji") or "").strip()
        if emoji:
            try:
                btn.emoji = emoji
            except Exception:  # noqa: BLE001
                pass
        view.add_item(btn)
        return view

    def _build_embed(self, cfg):
        embed = discord.Embed(
            title=str(cfg.get("title") or "Need help?"),
            description=str(cfg.get("description") or ""),
            color=int(cfg.get("color") or 16766720),
        )
        image_url = str(cfg.get("image_url") or "").strip()
        if image_url:
            embed.set_image(url=image_url)
        return embed

    async def post_panel(self, guild):
        """Post (or update) the ticket panel. Returns the message."""
        cfg = self._config(guild)
        channel = guild.get_channel(cfg["channel_id"]) if cfg.get("channel_id") else None
        if channel is None:
            raise ValueError("No ticket panel channel is set.")
        embed = self._build_embed(cfg)
        view = self._panel_view(cfg)
        message = None
        if cfg.get("message_id"):
            try:
                message = await channel.fetch_message(cfg["message_id"])
            except (discord.NotFound, discord.HTTPException):
                message = None
        if message is None:
            message = await channel.send(embed=embed, view=view)
        else:
            await message.edit(embed=embed, view=view)
        cfg["message_id"] = message.id
        self._save()
        return message

    async def _support_members(self, guild, cfg):
        """Members who should see tickets: support role + admins."""
        members = set()
        rid = cfg.get("support_role_id")
        if rid:
            role = guild.get_role(int(rid))
            if role:
                members.update(role.members)
        for m in guild.members:
            if m.guild_permissions.administrator and not m.bot:
                members.add(m)
        return members

    async def handle_open(self, interaction, button):
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(
                "Use that button inside the server.", ephemeral=True)
            return
        cfg = self._config(guild)
        if not cfg.get("enabled"):
            await interaction.response.send_message(
                "Tickets aren't turned on right now.", ephemeral=True)
            return
        user = interaction.user
        # Already have an open ticket?
        tickets = self.bot.store.guild(guild.id).setdefault("tickets", {})
        old_id = tickets.get(str(user.id))
        if old_id:
            thread = guild.get_thread(old_id)
            if thread is not None and not thread.archived:
                await interaction.response.send_message(
                    f"You already have an open ticket: {thread.mention}",
                    ephemeral=True)
                return
        # Private threads live under a text channel — use the panel channel.
        channel = guild.get_channel(cfg["channel_id"]) if cfg.get("channel_id") else None
        if channel is None:
            await interaction.response.send_message(
                "Ticket panel channel is missing — an admin needs to fix it.",
                ephemeral=True)
            return
        name = f"ticket-{user.name[:40]}".lower().replace(" ", "-")
        try:
            thread = await channel.create_thread(
                name=name,
                type=discord.ChannelType.private_thread,
                reason=f"Ticket opened by {user}",
            )
        except (discord.Forbidden, discord.HTTPException) as e:
            await interaction.response.send_message(
                f"Couldn't open your ticket: {e}", ephemeral=True)
            return
        # Add support staff so they can see the private thread.
        for m in await self._support_members(guild, cfg):
            if m.id == user.id:
                continue
            try:
                await thread.add_user(m)
            except (discord.Forbidden, discord.HTTPException):
                pass
        tickets[str(user.id)] = thread.id
        self._save()
        # Welcome message + close button.
        welcome = str(cfg.get("welcome") or "").strip()
        close_view = discord.ui.View(timeout=None)
        close_btn = discord.ui.Button(
            label="Close Ticket", emoji="🔒",
            style=discord.ButtonStyle.danger, custom_id=TICKET_CLOSE_ID)
        close_view.add_item(close_btn)
        try:
            await thread.send(
                f"{user.mention} {welcome}" if welcome else user.mention,
                view=close_view)
        except (discord.Forbidden, discord.HTTPException):
            pass
        await interaction.response.send_message(
            f"Your private ticket is open: {thread.mention}", ephemeral=True)

    async def handle_close(self, interaction):
        guild = interaction.guild
        channel = interaction.channel
        if guild is None or not isinstance(channel, discord.Thread):
            await interaction.response.send_message(
                "This isn't a ticket thread.", ephemeral=True)
            return
        cfg = self._config(guild)
        tickets = self.bot.store.guild(guild.id).setdefault("tickets", {})
        owner_id = None
        for uid, tid in tickets.items():
            if tid == channel.id:
                owner_id = uid
                break
        is_mod = interaction.user.guild_permissions.manage_channels
        support_role = None
        if cfg.get("support_role_id"):
            support_role = guild.get_role(int(cfg["support_role_id"]))
        is_support = (support_role is not None
                      and support_role in interaction.user.roles)
        if not is_mod and not is_support and str(interaction.user.id) != owner_id:
            await interaction.response.send_message(
                "Only the ticket owner, support staff, or a mod can close this.",
                ephemeral=True)
            return
        if owner_id:
            tickets.pop(owner_id, None)
            self._save()
        try:
            await channel.send("Ticket closed. Archiving…")
            await channel.edit(archived=True, reason="Ticket closed")
        except (discord.Forbidden, discord.HTTPException):
            pass
        await interaction.response.send_message(
            "Ticket closed.", ephemeral=True)

    async def close_thread(self, guild, thread_id):
        """Archive a ticket thread by ID (dashboard). Returns True if found."""
        tickets = self.bot.store.guild(guild.id).setdefault("tickets", {})
        owner_id = None
        for uid, tid in tickets.items():
            if tid == thread_id:
                owner_id = uid
                break
        thread = guild.get_thread(thread_id)
        if thread is None:
            if owner_id:
                tickets.pop(owner_id, None)
                self._save()
            return False
        try:
            await thread.send("Ticket closed from the dashboard. Archiving…")
            await thread.edit(archived=True, reason="Ticket closed (dashboard)")
        except (discord.Forbidden, discord.HTTPException):
            pass
        if owner_id:
            tickets.pop(owner_id, None)
            self._save()
        return True

    def open_tickets(self, guild):
        """List of open ticket threads: [{thread_id, thread_name, owner}]."""
        tickets = self.bot.store.guild(guild.id).get("tickets", {})
        out = []
        for uid, tid in tickets.items():
            thread = guild.get_thread(tid)
            if thread is None or thread.archived:
                continue
            member = guild.get_member(int(uid))
            out.append({
                "thread_id": str(tid),
                "thread_name": thread.name,
                "owner": member.display_name if member else f"user {uid}",
            })
        return out
