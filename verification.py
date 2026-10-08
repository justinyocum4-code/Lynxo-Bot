"""Verification: /setup, persistent buttons, regular + 18+ flows."""
import io
import random

import discord
from discord import app_commands
from discord.ext import commands

from utils import dm_user, mod_log, now_str, user_label
from vision import check_selfie

VERIFY_BUTTON_ID = "lynxo_verify_regular"
ADULT_BUTTON_ID = "lynxo_verify_adult"
PHOTO_TIMEOUT = 300  # seconds to send the selfie in DMs


class VerifyView(discord.ui.View):
    """Persistent 'Get Verified' button for #verify-here."""
    def __init__(self, bot):
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(label="Get Verified", style=discord.ButtonStyle.success,
                       custom_id=VERIFY_BUTTON_ID)
    async def verify(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(
                "Use that button inside the server.", ephemeral=True)
            return
        g = self.bot.store.guild(guild.id)
        if not g["setup"]:
            await interaction.response.send_message(
                "This server isn't set up yet — an admin needs to run /setup.",
                ephemeral=True)
            return
        raid = self.bot.get_cog("Raid")
        if raid and raid.is_lockdown(guild.id):
            await interaction.response.send_message(
                "Verification is paused during raid lockdown. Try again soon.",
                ephemeral=True)
            return
        member = interaction.user
        role = guild.get_role(g["roles"].get("member", 0))
        if role is None:
            await interaction.response.send_message(
                "Member role is missing — an admin needs to run /setup again.",
                ephemeral=True)
            return
        if role in member.roles:
            await interaction.response.send_message(
                "You're already verified.", ephemeral=True)
            return
        try:
            await member.add_roles(role, reason="Verified via #verify-here button")
        except (discord.Forbidden, discord.HTTPException) as e:
            await interaction.response.send_message(
                f"Couldn't give you the role: {e}", ephemeral=True)
            return
        await mod_log(self.bot, guild,
                      f"VERIFIED — {user_label(member)} pressed the verify button and got the Member role.")
        await interaction.response.send_message(
            "You're verified! Welcome to the server.", ephemeral=True)


class AdultView(discord.ui.View):
    """Persistent 'Verify 18+' button for #get-adult-access."""
    def __init__(self, bot):
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(label="Verify 18+", style=discord.ButtonStyle.primary,
                       custom_id=ADULT_BUTTON_ID)
    async def adult(self, interaction: discord.Interaction, button: discord.ui.Button):
        cog = self.bot.get_cog("Verification")
        await cog.start_adult_flow(interaction)


class Verification(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.active_sessions = set()  # user ids with an open 18+ photo wait

    # ---------------- /setup ----------------

    async def _get_role(self, guild, name):
        role = discord.utils.get(guild.roles, name=name)
        if role is None:
            role = await guild.create_role(name=name, reason="Lynxo Bot /setup")
        return role

    async def _get_channel(self, guild, name, overwrites, topic):
        ch = discord.utils.get(guild.text_channels, name=name)
        if ch is None:
            ch = await guild.create_text_channel(
                name, overwrites=overwrites, topic=topic,
                reason="Lynxo Bot /setup")
        return ch

    @app_commands.command(name="setup", description="Set up verification channels, roles, and buttons.")
    @app_commands.checks.has_permissions(administrator=True)
    async def setup(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        me = guild.me

        member_role = await self._get_role(guild, "Member")
        adult_role = await self._get_role(guild, "Adult 18+")
        quar_role = await self._get_role(guild, "Quarantined")

        # Quarantined members can't talk anywhere.
        for ch in guild.text_channels:
            try:
                await ch.set_permissions(quar_role, send_messages=False,
                                         reason="Lynxo Bot /setup")
            except (discord.Forbidden, discord.HTTPException):
                pass

        public_ow = {
            guild.default_role: discord.PermissionOverwrite(
                view_channel=True, send_messages=False),
            me: discord.PermissionOverwrite(
                view_channel=True, send_messages=True, manage_messages=True),
        }
        mod_ow = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            me: discord.PermissionOverwrite(
                view_channel=True, send_messages=True, manage_messages=True),
        }
        for role in guild.roles:
            if role.permissions.manage_messages and not role.is_default():
                mod_ow[role] = discord.PermissionOverwrite(
                    view_channel=True, send_messages=True)

        verify_ch = await self._get_channel(
            guild, "verify-here", public_ow,
            "Press the button to verify and unlock the server.")
        adult_ch = await self._get_channel(
            guild, "get-adult-access", public_ow,
            "Optional 18+ verification for adult channels.")
        logs_ch = await self._get_channel(
            guild, "mod-logs", mod_ow, "Lynxo Bot moderation log.")
        review_ch = await self._get_channel(
            guild, "verify-review", mod_ow,
            "18+ photo review queue for moderators.")

        await verify_ch.send(
            "Welcome! Press the button below to get verified and unlock the server.",
            view=VerifyView(self.bot))
        await adult_ch.send(
            "Want access to the 18+ channels? Press the button below. "
            "You'll get a DM with a quick photo check.",
            view=AdultView(self.bot))

        g = self.bot.store.guild(guild.id)
        g["setup"] = True
        g["channels"] = {
            "verify_here": verify_ch.id,
            "get_adult_access": adult_ch.id,
            "mod_logs": logs_ch.id,
            "verify_review": review_ch.id,
        }
        g["roles"] = {
            "member": member_role.id,
            "adult": adult_role.id,
            "quarantined": quar_role.id,
        }
        self.bot.store.save()

        # Sync this guild's slash commands right away (global sync can lag).
        try:
            self.bot.tree.copy_global_to(guild=guild)
            await self.bot.tree.sync(guild=guild)
        except (discord.Forbidden, discord.HTTPException):
            pass

        await mod_log(self.bot, guild,
                      f"SETUP — verification channels and roles created by {interaction.user}.")
        await interaction.followup.send(
            "Setup done. #verify-here, #get-adult-access, #mod-logs, and "
            "#verify-review are ready, with Member, Adult 18+, and Quarantined roles.",
            ephemeral=True)

    # ---------------- 18+ flow ----------------

    async def start_adult_flow(self, interaction: discord.Interaction):
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(
                "Use that button inside the server.", ephemeral=True)
            return
        g = self.bot.store.guild(guild.id)
        if not g["setup"]:
            await interaction.response.send_message(
                "This server isn't set up yet — an admin needs to run /setup.",
                ephemeral=True)
            return
        member = interaction.user
        member_role = guild.get_role(g["roles"].get("member", 0))
        adult_role = guild.get_role(g["roles"].get("adult", 0))
        if adult_role in member.roles:
            await interaction.response.send_message(
                "You already have 18+ access.", ephemeral=True)
            return
        if member_role not in member.roles:
            await interaction.response.send_message(
                "Get verified in #verify-here first, then come back here.",
                ephemeral=True)
            return
        if member.id in self.active_sessions:
            await interaction.response.send_message(
                "You already have a check open — send your photo in our DMs.",
                ephemeral=True)
            return

        number = random.randint(1, 5)
        dm_text = (
            f"This is the 18+ check for **{guild.name}**.\n\n"
            f"Reply here with ONE photo: a selfie of you holding up "
            f"**{number} finger(s)**. Make sure your fingers are clearly visible.\n"
            f"You have 5 minutes. The photo goes to the mods for review."
        )
        if not await dm_user(member, dm_text):
            await interaction.response.send_message(
                "I couldn't DM you — open your DMs (Privacy Settings > Allow "
                "direct messages) and try again.", ephemeral=True)
            return
        await interaction.response.send_message(
            "Check your DMs — I sent you the photo challenge.", ephemeral=True)
        await mod_log(self.bot, guild,
                      f"18+ START — {user_label(member)} was given the number {number}.")

        self.active_sessions.add(member.id)
        try:
            def check(m):
                return (m.author.id == member.id
                        and isinstance(m.channel, discord.DMChannel)
                        and m.attachments)
            msg = await self.bot.wait_for("message", check=check, timeout=PHOTO_TIMEOUT)
        except TimeoutError:
            await dm_user(member, "Time ran out — press the Verify 18+ button again when you're ready.")
            await mod_log(self.bot, guild, f"18+ TIMEOUT — {user_label(member)} didn't send a photo in time.")
            return
        finally:
            self.active_sessions.discard(member.id)

        attachment = next(
            (a for a in msg.attachments
             if (a.content_type or "").startswith("image/")), None)
        if attachment is None:
            await dm_user(member, "That wasn't a photo — press the Verify 18+ button to try again.")
            return

        try:
            image_bytes = await attachment.read()
        except discord.HTTPException:
            await dm_user(member, "Couldn't download that photo — try again.")
            return

        # AI vision stub (Phase 2 will auto-check here).
        result = await check_selfie(image_bytes, number)
        if result.verdict == "pass":
            await self._grant_adult(guild, member, "AI vision check passed")
            await dm_user(member, f"You're verified 18+ in {guild.name}.")
            return
        if result.verdict == "fail":
            await self._record_fail(guild, member, "AI vision check failed")
            await dm_user(member, "That photo didn't pass the check — the mods will take a look.")
            # fall through to human review as well

        await self._send_to_review(guild, member, number, attachment, image_bytes)
        await dm_user(member, "Photo received — the mods will review it shortly.")

    async def _send_to_review(self, guild, member, number, attachment, image_bytes):
        g = self.bot.store.guild(guild.id)
        ch = guild.get_channel(g["channels"].get("verify_review", 0))
        fails = g["verify"].get(str(member.id), {}).get("fails", 0)
        text = (
            f"18+ REVIEW — {user_label(member)}\n"
            f"Expected fingers: **{number}**\n"
            f"Prior failed attempts: {fails}\n"
            f"A mod can approve with /approve18 or deny with /deny18."
        )
        file = discord.File(io.BytesIO(image_bytes), filename=attachment.filename)
        if ch:
            try:
                await ch.send(text, file=file)
            except (discord.Forbidden, discord.HTTPException):
                pass
        await mod_log(self.bot, guild,
                      f"18+ PHOTO — {user_label(member)} sent a selfie (expected {number} fingers) for review.")

    async def _record_fail(self, guild, member, why):
        g = self.bot.store.guild(guild.id)
        rec = g["verify"].setdefault(str(member.id), {"fails": 0})
        rec["fails"] += 1
        self.bot.store.save()
        await mod_log(self.bot, guild,
                      f"18+ FAIL — {user_label(member)} — {why} (attempt {rec['fails']})")
        if rec["fails"] >= 3:
            ch = guild.get_channel(g["channels"].get("verify_review", 0))
            if ch:
                try:
                    await ch.send(
                        f"MANUAL REVIEW — {user_label(member)} has {rec['fails']} "
                        f"failed 18+ attempts. Please review by hand.")
                except (discord.Forbidden, discord.HTTPException):
                    pass
            await mod_log(self.bot, guild,
                          f"18+ MANUAL REVIEW — {user_label(member)} flagged after {rec['fails']} failed attempts.")

    async def _grant_adult(self, guild, member, why):
        g = self.bot.store.guild(guild.id)
        role = guild.get_role(g["roles"].get("adult", 0))
        if role:
            try:
                await member.add_roles(role, reason=why)
            except (discord.Forbidden, discord.HTTPException):
                pass
        g["verify"].pop(str(member.id), None)
        self.bot.store.save()
        await mod_log(self.bot, guild,
                      f"18+ APPROVED — {user_label(member)} — {why}")

    @app_commands.command(name="approve18", description="Approve a member's 18+ verification.")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def approve18(self, interaction: discord.Interaction, member: discord.Member):
        await self._grant_adult(interaction.guild, member,
                                f"approved by {interaction.user}")
        await dm_user(member, f"Your 18+ verification in {interaction.guild.name} was approved.")
        await interaction.response.send_message(
            f"{member.mention} approved for 18+.", ephemeral=True)

    @app_commands.command(name="deny18", description="Deny a member's 18+ verification.")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def deny18(self, interaction: discord.Interaction,
                     member: discord.Member, reason: str = "Photo did not match"):
        await self._record_fail(interaction.guild, member,
                                f"denied by {interaction.user}: {reason}")
        await dm_user(member,
                      f"Your 18+ photo in {interaction.guild.name} was denied: {reason}. "
                      f"You can try again with the Verify 18+ button.")
        await interaction.response.send_message(
            f"{member.mention} denied.", ephemeral=True)


async def setup(bot):
    await bot.add_cog(Verification(bot))
