"""Verification: /setup, persistent buttons, regular + 18+ flows."""
import hashlib
import io
import random

import discord
from discord import app_commands
from discord.ext import commands

from ai import ai_vision_scan
from utils import dm_user, mod_log, now_str, user_label
from vision import check_selfie

VERIFY_BUTTON_ID = "lynxo_verify_regular"
ADULT_BUTTON_ID = "lynxo_verify_adult"
REVIEW_APPROVE_ID = "lynxo_review_approve"
REVIEW_DENY_ID = "lynxo_review_deny"
REVIEW_AISCAN_ID = "lynxo_review_aiscan"
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


def _is_mod(interaction):
    """Same bar as /approve18 and /deny18 (manage_messages)."""
    user = interaction.user
    return (isinstance(user, discord.Member)
            and user.guild_permissions.manage_messages)


async def ai_scan_photo(bot, guild, message, user_id=None,
                        expected_fingers=None):
    """Shared AI photo scan: duplicate check + Groq vision advisory.

    Downloads the first image attachment on the message, SHA256 duplicate
    check against verify_photo_hashes, Groq vision advisory scan via
    ai.ai_vision_scan. Returns plain-words results text. Advisory only —
    never approves or denies on its own.

    user_id: the person the photo is attributed to (for the duplicate
        check and the "who" label). Defaults to the message author.
    expected_fingers: the finger count the person was asked to hold up,
        if known (18+ review posts). None for generic messages.
    """
    attachment = next(
        (a for a in message.attachments
         if (a.content_type or "").startswith("image/")), None)
    if attachment is None:
        return "AI scan: no photo attached to that message."
    try:
        image_bytes = await attachment.read()
    except discord.HTTPException:
        return "AI scan: couldn't download the photo."

    # 1. Duplicate check: has this exact photo been submitted before?
    sha = hashlib.sha256(image_bytes).hexdigest()
    g = bot.store.guild(guild.id)
    hashes = g["verify_photo_hashes"]
    uid = str(user_id) if user_id is not None else str(message.author.id)
    prev = hashes.get(sha)
    if prev is None:
        dup_line = ("Duplicate check: this photo has not been submitted "
                    "before.")
    elif prev == uid:
        dup_line = ("Duplicate check: this is the same photo this user "
                    "submitted before — not a red flag on its own.")
    else:
        dup_line = (f"Duplicate check: RED FLAG — this exact photo was "
                    f"already submitted by a different user (id {prev}). "
                    f"Possible photo reuse.")
    hashes[sha] = uid
    while len(hashes) > 2000:
        hashes.pop(next(iter(hashes)))
    bot.store.save()

    # 2. Vision scan (advisory).
    fingers = expected_fingers if expected_fingers is not None else "?"
    prompt = (
        "You are an advisory assistant helping a human moderator review "
        "a verification photo for a Discord server. You do NOT make "
        "the final decision — a human moderator does.\n"
        f"The person was asked to take a selfie holding up "
        f"{fingers} finger(s).\n"
        "Reply in short plain-words bullet points, no more than 8 lines:\n"
        "- Signs this looks AI-generated vs a genuine photo (be specific "
        "about artifacts: hands, fingers, skin, background, lighting).\n"
        "- Does it look like a real selfie of a person? (yes / no / "
        "uncertain)\n"
        "- Apparent age: ADULT, MINOR, or UNCERTAIN. Say UNCERTAIN unless "
        "the person is clearly an adult or clearly a minor."
    )
    note = await ai_vision_scan(
        image_bytes, attachment.content_type or "image/jpeg", prompt)
    if note:
        ai_part = "AI notes:\n" + note
    else:
        ai_part = ("AI notes: unavailable right now (no vision access) — "
                   "the duplicate check above still stands.")

    member = guild.get_member(int(uid)) if uid.isdigit() else None
    who = user_label(member) if member else f"user id {uid}"
    return (
        f"🔍 AI SCAN — {who}\n"
        f"{dup_line}\n"
        f"{ai_part}\n"
        f"This is advisory — you make the final call."
    )


class ReviewView(discord.ui.View):
    """Persistent Approve / Deny / AI Scan buttons on 18+ review posts."""
    def __init__(self, bot):
        super().__init__(timeout=None)
        self.bot = bot

    def _lookup(self, interaction):
        g = self.bot.store.guild(interaction.guild.id)
        rec = g["verify_reviews"].get(str(interaction.message.id))
        return rec

    async def _lock_decisions(self, interaction, note):
        """Disable Approve/Deny after a decision; AI Scan stays available."""
        view = ReviewView(self.bot)
        for child in view.children:
            if child.custom_id in (REVIEW_APPROVE_ID, REVIEW_DENY_ID):
                child.disabled = True
        try:
            base = interaction.message.content or ""
            await interaction.message.edit(
                content=base + f"\n{note}", view=view)
        except (discord.Forbidden, discord.HTTPException):
            pass

    @discord.ui.button(label="Approve", emoji="✅",
                       style=discord.ButtonStyle.success,
                       custom_id=REVIEW_APPROVE_ID)
    async def approve(self, interaction: discord.Interaction,
                      button: discord.ui.Button):
        if not _is_mod(interaction):
            await interaction.response.send_message(
                "Mods only.", ephemeral=True)
            return
        rec = self._lookup(interaction)
        if rec is None:
            await interaction.response.send_message(
                "This review post isn't tracked anymore — use /approve18.",
                ephemeral=True)
            return
        member = interaction.guild.get_member(int(rec["user_id"]))
        if member is None:
            await interaction.response.send_message(
                "That user isn't in the server anymore.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        cog = self.bot.get_cog("Verification")
        await cog._grant_adult(
            interaction.guild, member,
            f"approved by {interaction.user} via review button")
        await dm_user(
            member,
            f"Your 18+ verification in {interaction.guild.name} was approved.")
        await self._lock_decisions(
            interaction, f"Decided: approved by {interaction.user}.")
        await interaction.followup.send(
            f"{member.mention} approved for 18+.", ephemeral=True)

    @discord.ui.button(label="Deny", emoji="❌",
                       style=discord.ButtonStyle.danger,
                       custom_id=REVIEW_DENY_ID)
    async def deny(self, interaction: discord.Interaction,
                   button: discord.ui.Button):
        if not _is_mod(interaction):
            await interaction.response.send_message(
                "Mods only.", ephemeral=True)
            return
        rec = self._lookup(interaction)
        if rec is None:
            await interaction.response.send_message(
                "This review post isn't tracked anymore — use /deny18.",
                ephemeral=True)
            return
        member = interaction.guild.get_member(int(rec["user_id"]))
        if member is None:
            await interaction.response.send_message(
                "That user isn't in the server anymore.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        cog = self.bot.get_cog("Verification")
        await cog._record_fail(
            interaction.guild, member,
            f"denied by {interaction.user} via review button")
        await dm_user(
            member,
            f"Your 18+ photo in {interaction.guild.name} was denied. "
            f"You can try again with the Verify 18+ button.")
        await self._lock_decisions(
            interaction, f"Decided: denied by {interaction.user}.")
        await interaction.followup.send(
            f"{member.mention} denied.", ephemeral=True)

    @discord.ui.button(label="AI Scan", emoji="🔍",
                       style=discord.ButtonStyle.secondary,
                       custom_id=REVIEW_AISCAN_ID)
    async def aiscan(self, interaction: discord.Interaction,
                     button: discord.ui.Button):
        if not _is_mod(interaction):
            await interaction.response.send_message(
                "Mods only.", ephemeral=True)
            return
        rec = self._lookup(interaction)
        if rec is None:
            await interaction.response.send_message(
                "This review post isn't tracked anymore.", ephemeral=True)
            return
        cog = self.bot.get_cog("Verification")
        await cog.run_ai_scan(interaction, rec)


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
            f"Prior failed attempts: {fails}"
        )
        file = discord.File(io.BytesIO(image_bytes), filename=attachment.filename)
        if ch:
            try:
                sent = await ch.send(text, file=file,
                                     view=ReviewView(self.bot))
                reviews = g["verify_reviews"]
                reviews[str(sent.id)] = {
                    "user_id": str(member.id),
                    "expected": number,
                }
                # Prune oldest entries so the registry can't grow forever.
                while len(reviews) > 200:
                    reviews.pop(next(iter(reviews)))
                self.bot.store.save()
            except (discord.Forbidden, discord.HTTPException):
                pass
        await mod_log(self.bot, guild,
                      f"18+ PHOTO — {user_label(member)} sent a selfie (expected {number} fingers) for review.")

    async def run_ai_scan(self, interaction, rec):
        """Duplicate-photo check + Groq vision scan, posted as a reply.

        Advisory only — never approves or denies on its own.
        """
        await interaction.response.defer()  # public "thinking" in the mod channel
        text = await ai_scan_photo(
            self.bot, interaction.guild, interaction.message,
            user_id=rec["user_id"], expected_fingers=rec.get("expected"))
        await interaction.message.reply(text, mention_author=False)

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

    @app_commands.context_menu(name="Scan photo with AI")
    async def scan_photo_ctx(self, interaction: discord.Interaction,
                             message: discord.Message):
        """Message context menu: AI-scan any photo. Mods only.

        Long-press (mobile) or right-click (desktop) a message with a photo
        and choose "Scan photo with AI". No IDs to copy.
        """
        if not _is_mod(interaction):
            await interaction.response.send_message(
                "Mods only.", ephemeral=True)
            return
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(
                "Use that inside the server.", ephemeral=True)
            return
        has_photo = any(
            (a.content_type or "").startswith("image/")
            for a in message.attachments)
        if not has_photo:
            await interaction.response.send_message(
                "That message has no photo.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        text = await ai_scan_photo(self.bot, guild, message)
        try:
            await message.reply(text, mention_author=False)
        except (discord.Forbidden, discord.HTTPException):
            await interaction.followup.send(
                "Couldn't post the scan results in that channel.",
                ephemeral=True)
            return
        await interaction.followup.send("Scan posted.", ephemeral=True)


async def setup(bot):
    await bot.add_cog(Verification(bot))
