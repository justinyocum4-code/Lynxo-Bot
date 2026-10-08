"""Community engagement: welcome/goodbye messages, leveling, polls,
giveaways, starboard, and suggestion voting. Configured from the dashboard."""
import copy
import random
import sys
import time

import discord
from discord import app_commands
from discord.ext import commands, tasks

GOLD = 0xFFD700
_NUMBER_EMOJI = ["1\ufe0f\u20e3", "2\ufe0f\u20e3", "3\ufe0f\u20e3", "4\ufe0f\u20e3",
                 "5\ufe0f\u20e3", "6\ufe0f\u20e3", "7\ufe0f\u20e3", "8\ufe0f\u20e3",
                 "9\ufe0f\u20e3", "\U0001f51f"]

# Settings keys live in the guild blob's "settings" dict. config.py is the
# canonical home for defaults; these are setdefault-merged here so the file
# is self-contained and never needs config.py edits.
_COMMUNITY_DEFAULTS = {
    # Welcome / goodbye
    "welcome_enabled": False,
    "welcome_channel_id": None,
    "welcome_message": "Welcome to the server, {user}!",
    "goodbye_enabled": False,
    "goodbye_channel_id": None,
    "goodbye_message": "{name} has left the server.",
    # Leveling
    "leveling_enabled": False,
    "levelup_channel_id": None,   # None = post in the channel where they leveled
    "level_roles": {},            # {"3": role_id} — grant on reaching that level
    # Starboard
    "starboard_enabled": False,
    "starboard_channel_id": None,
    "starboard_threshold": 3,     # distinct non-bot star reactors needed
    # Suggestions
    "suggest_channel_id": None,   # messages here get 👍/👎 auto-reactions
}


def _xp_for_level(n):
    """Total XP needed to reach level n (triangular: 100, 300, 600, ...)."""
    return 100 * n * (n + 1) // 2


def _level_for_xp(xp):
    n = 0
    while _xp_for_level(n + 1) <= xp:
        n += 1
    return n


class Community(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._xp_cooldown = {}  # guild_id -> {user_id: last_xp_timestamp}

    # ---------------- helpers ----------------

    def _settings(self, guild):
        """(settings dict, guild blob). New keys merge in automatically."""
        g = self.bot.store.guild(guild.id)
        s = g.setdefault("settings", {})
        for k, v in _COMMUNITY_DEFAULTS.items():
            s.setdefault(k, copy.deepcopy(v))
        g.setdefault("level_xp", {})
        g.setdefault("giveaways", {})
        if not isinstance(g.get("starboard_posts"), list):
            g["starboard_posts"] = []
        return s, g

    def _text_channel(self, guild, channel_id):
        if not channel_id:
            return None
        try:
            channel = guild.get_channel(int(channel_id))
        except (TypeError, ValueError):
            return None
        return channel if isinstance(channel, discord.TextChannel) else None

    # ---------------- welcome / goodbye ----------------

    @commands.Cog.listener()
    async def on_member_join(self, member):
        if member.bot or member.guild is None:
            return
        try:
            s, _ = self._settings(member.guild)
            if not s.get("welcome_enabled"):
                return
            channel = self._text_channel(member.guild, s.get("welcome_channel_id"))
            if channel is None:
                return
            text = str(s.get("welcome_message") or "")
            text = text.replace("{user}", member.mention)
            text = text.replace("{name}", member.display_name)
            await channel.send(text)
        except (discord.Forbidden, discord.HTTPException) as e:
            print(f"Community: welcome failed in {member.guild.name}: {e}",
                  file=sys.stderr, flush=True)

    @commands.Cog.listener()
    async def on_member_remove(self, member):
        if member.guild is None:
            return
        try:
            s, _ = self._settings(member.guild)
            if not s.get("goodbye_enabled"):
                return
            channel = self._text_channel(member.guild, s.get("goodbye_channel_id"))
            if channel is None:
                return
            text = str(s.get("goodbye_message") or "")
            text = text.replace("{user}", member.mention)
            text = text.replace("{name}", member.display_name)
            await channel.send(text)
        except (discord.Forbidden, discord.HTTPException) as e:
            print(f"Community: goodbye failed: {e}",
                  file=sys.stderr, flush=True)

    # ---------------- leveling + suggestions ----------------

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.guild is None or message.author.bot:
            return
        try:
            s, g = self._settings(message.guild)
            # Suggestion voting.
            suggest_id = s.get("suggest_channel_id")
            if suggest_id:
                try:
                    if message.channel.id == int(suggest_id):
                        for emoji in ("\U0001f44d", "\U0001f44e"):
                            await message.add_reaction(emoji)
                except (discord.Forbidden, discord.HTTPException):
                    pass
            # Leveling.
            if not s.get("leveling_enabled"):
                return
            now = time.time()
            cd = self._xp_cooldown.setdefault(message.guild.id, {})
            if now - cd.get(message.author.id, 0) < 60:
                return
            cd[message.author.id] = now
            xp_map = g["level_xp"]
            uid = str(message.author.id)
            old_xp = int(xp_map.get(uid, 0))
            old_level = _level_for_xp(old_xp)
            new_xp = old_xp + random.randint(15, 25)
            xp_map[uid] = new_xp
            new_level = _level_for_xp(new_xp)
            self.bot.store.save()
            if new_level > old_level:
                dest = (self._text_channel(message.guild, s.get("levelup_channel_id"))
                        or message.channel)
                try:
                    await dest.send(
                        f"GG {message.author.mention}, you hit level {new_level}!")
                except (discord.Forbidden, discord.HTTPException):
                    pass
                role_id = (s.get("level_roles") or {}).get(str(new_level))
                if role_id:
                    role = message.guild.get_role(int(role_id))
                    if role is not None:
                        try:
                            await message.author.add_roles(
                                role, reason=f"Lynxo Bot level {new_level} reward")
                        except (discord.Forbidden, discord.HTTPException) as e:
                            print(f"Community: level role failed: {e}",
                                  file=sys.stderr, flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"Community: on_message failed: {e}",
                  file=sys.stderr, flush=True)

    @app_commands.command(name="rank", description="Show your level and XP.")
    async def rank(self, interaction: discord.Interaction):
        try:
            s, g = self._settings(interaction.guild)
            if not s.get("leveling_enabled"):
                await interaction.response.send_message(
                    "Leveling is turned off on this server.", ephemeral=True)
                return
            xp = int(g.get("level_xp", {}).get(str(interaction.user.id), 0))
            level = _level_for_xp(xp)
            need = _xp_for_level(level + 1) - xp
            await interaction.response.send_message(
                f"{interaction.user.display_name}: level {level}, {xp} XP. "
                f"{need} XP to level {level + 1}.",
                ephemeral=True)
        except Exception as e:  # noqa: BLE001
            print(f"Community: /rank failed: {e}", file=sys.stderr, flush=True)
            await interaction.response.send_message(
                "Could not look up your rank.", ephemeral=True)

    # ---------------- polls ----------------

    @app_commands.command(name="poll", description="Start a poll (2-10 options).")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def poll(self, interaction: discord.Interaction,
                   question: str, option1: str, option2: str,
                   option3: str = None, option4: str = None,
                   option5: str = None, option6: str = None,
                   option7: str = None, option8: str = None,
                   option9: str = None, option10: str = None):
        options = [o for o in (option1, option2, option3, option4, option5,
                               option6, option7, option8, option9, option10)
                   if o]
        if len(options) < 2:
            await interaction.response.send_message(
                "Give at least 2 options.", ephemeral=True)
            return
        embed = discord.Embed(title="\U0001f4ca " + question[:250],
                              color=GOLD)
        for i, opt in enumerate(options):
            embed.add_field(name=_NUMBER_EMOJI[i],
                            value=opt[:200], inline=False)
        try:
            await interaction.response.send_message(embed=embed)
            msg = await interaction.original_response()
            for i in range(len(options)):
                await msg.add_reaction(_NUMBER_EMOJI[i])
        except (discord.Forbidden, discord.HTTPException) as e:
            print(f"Community: /poll failed: {e}", file=sys.stderr, flush=True)

    @app_commands.command(name="endpoll", description="End a poll and announce the winner.")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def endpoll(self, interaction: discord.Interaction, message_id: str):
        try:
            mid = int(message_id)
        except (TypeError, ValueError):
            await interaction.response.send_message(
                "That is not a valid message id.", ephemeral=True)
            return
        try:
            msg = await interaction.channel.fetch_message(mid)
        except (discord.NotFound, discord.HTTPException):
            await interaction.response.send_message(
                "Could not find that message in this channel.", ephemeral=True)
            return
        counts = []
        bot_id = self.bot.user.id if self.bot.user else 0
        for reaction in msg.reactions:
            if str(reaction.emoji) not in _NUMBER_EMOJI:
                continue
            users = [u async for u in reaction.users()]
            n = sum(1 for u in users if u.id != bot_id and not u.bot)
            counts.append((str(reaction.emoji), n))
        if not counts:
            await interaction.response.send_message(
                "No poll reactions found on that message.", ephemeral=True)
            return
        counts.sort(key=lambda c: c[1], reverse=True)
        best = counts[0][1]
        winners = [e for e, n in counts if n == best]
        total = sum(n for _, n in counts)
        if best == 0:
            result = "The poll ended with no votes."
        elif len(winners) == 1:
            result = f"The winner is {winners[0]} with {best} votes ({total} total)."
        else:
            result = (f"It's a tie between {' '.join(winners)} "
                      f"with {best} votes each ({total} total).")
        try:
            await interaction.channel.send("\U0001f3c1 Poll closed. " + result)
            await interaction.response.send_message("Poll closed.", ephemeral=True)
        except (discord.Forbidden, discord.HTTPException) as e:
            print(f"Community: /endpoll failed: {e}", file=sys.stderr, flush=True)

    # ---------------- giveaways ----------------

    async def cog_load(self):
        self.giveaway_loop.start()

    async def cog_unload(self):
        self.giveaway_loop.cancel()

    @tasks.loop(minutes=1)
    async def giveaway_loop(self):
        now = time.time()
        for gid in self.bot.store.setup_guilds():
            guild = self.bot.get_guild(gid)
            if guild is None:
                continue
            try:
                _, g = self._settings(guild)
                giveaways = g.get("giveaways") or {}
                for mid, gw in list(giveaways.items()):
                    if gw.get("ends_at", 0) <= now:
                        await self._finish_giveaway(guild, mid, gw)
                        giveaways.pop(mid, None)
                self.bot.store.save()
            except Exception as e:  # noqa: BLE001
                print(f"Community: giveaway loop failed for {guild.name}: {e}",
                      file=sys.stderr, flush=True)

    @giveaway_loop.before_loop
    async def _before_giveaway(self):
        await self.bot.wait_until_ready()

    async def _finish_giveaway(self, guild, mid, gw):
        channel = guild.get_channel(int(gw.get("channel_id", 0)))
        if not isinstance(channel, discord.TextChannel):
            return
        try:
            msg = await channel.fetch_message(int(mid))
        except (discord.NotFound, discord.HTTPException, ValueError):
            return
        winners_n = max(1, int(gw.get("winners", 1)))
        prize = gw.get("prize", "a prize")
        host_id = gw.get("host_id")
        entrants = []
        for reaction in msg.reactions:
            if str(reaction.emoji) != "\U0001f389":
                continue
            async for u in reaction.users():
                if u.bot or u.id == host_id:
                    continue
                if u.id not in entrants:
                    entrants.append(u.id)
        random.shuffle(entrants)
        picked = entrants[:winners_n]
        try:
            if picked:
                mentions = " ".join(f"<@{uid}>" for uid in picked)
                new_embed = discord.Embed(
                    title="\U0001f389 GIVEAWAY ENDED: " + str(prize)[:200],
                    description=f"Winners: {mentions}", color=GOLD)
                await msg.edit(embed=new_embed)
                await channel.send(
                    f"\U0001f389 Congratulations {mentions}! "
                    f"You won **{prize}**!")
            else:
                new_embed = discord.Embed(
                    title="\U0001f389 GIVEAWAY ENDED: " + str(prize)[:200],
                    description="No valid entries — no winner.", color=GOLD)
                await msg.edit(embed=new_embed)
                await channel.send(
                    f"\U0001f389 The giveaway for **{prize}** ended with no entries.")
        except (discord.Forbidden, discord.HTTPException) as e:
            print(f"Community: giveaway finish failed: {e}",
                  file=sys.stderr, flush=True)

    @app_commands.command(name="giveaway", description="Start a giveaway.")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def giveaway(self, interaction: discord.Interaction,
                       duration_minutes: app_commands.Range[int, 1, 10080],
                       winners: app_commands.Range[int, 1, 25],
                       prize: str):
        embed = discord.Embed(
            title="\U0001f389 GIVEAWAY: " + prize[:200],
            description=(f"React with \U0001f389 to enter!\n"
                         f"{winners} winner(s) — ends in {duration_minutes} minutes."),
            color=GOLD)
        try:
            await interaction.response.send_message(embed=embed)
            msg = await interaction.original_response()
            await msg.add_reaction("\U0001f389")
            _, g = self._settings(interaction.guild)
            g.setdefault("giveaways", {})[str(msg.id)] = {
                "channel_id": interaction.channel.id,
                "ends_at": time.time() + duration_minutes * 60,
                "winners": winners,
                "prize": prize[:200],
                "host_id": interaction.user.id,
            }
            self.bot.store.save()
        except (discord.Forbidden, discord.HTTPException) as e:
            print(f"Community: /giveaway failed: {e}", file=sys.stderr, flush=True)

    @app_commands.command(name="giveaway_end", description="End a giveaway now and pick winners.")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def giveaway_end(self, interaction: discord.Interaction, message_id: str):
        try:
            _, g = self._settings(interaction.guild)
            giveaways = g.get("giveaways") or {}
            gw = giveaways.pop(str(int(message_id)), None)
            self.bot.store.save()
            if gw is None:
                await interaction.response.send_message(
                    "No active giveaway with that message id.", ephemeral=True)
                return
            await self._finish_giveaway(interaction.guild, str(message_id), gw)
            await interaction.response.send_message(
                "Giveaway ended.", ephemeral=True)
        except (TypeError, ValueError):
            await interaction.response.send_message(
                "That is not a valid message id.", ephemeral=True)
        except Exception as e:  # noqa: BLE001
            print(f"Community: /giveaway_end failed: {e}",
                  file=sys.stderr, flush=True)
            await interaction.response.send_message(
                "Could not end the giveaway.", ephemeral=True)

    # ---------------- starboard ----------------

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload):
        if str(payload.emoji) != "\u2b50":
            return
        if payload.guild_id is None:
            return
        guild = self.bot.get_guild(payload.guild_id)
        if guild is None:
            return
        try:
            s, g = self._settings(guild)
            if not s.get("starboard_enabled"):
                return
            channel = self._text_channel(guild, s.get("starboard_channel_id"))
            if channel is None:
                return
            if payload.message_id in (g.get("starboard_posts") or []):
                return
            src = guild.get_channel(payload.channel_id)
            if not isinstance(src, discord.TextChannel):
                return
            try:
                msg = await src.fetch_message(payload.message_id)
            except (discord.NotFound, discord.HTTPException):
                return
            stars = 0
            for reaction in msg.reactions:
                if str(reaction.emoji) != "\u2b50":
                    continue
                async for u in reaction.users():
                    if not u.bot:
                        stars += 1
            if stars < int(s.get("starboard_threshold", 3)):
                return
            content = (msg.content or "")[:400] or "(no text — image or embed)"
            embed = discord.Embed(description=content, color=GOLD,
                                  timestamp=msg.created_at)
            embed.set_author(name=msg.author.display_name,
                             icon_url=(msg.author.display_avatar.url
                                       if msg.author.display_avatar else None))
            embed.add_field(name="Stars", value=f"\u2b50 {stars}", inline=True)
            embed.add_field(name="Channel", value=src.mention, inline=True)
            embed.add_field(name="Jump to message",
                            value=f"[Open]({msg.jump_url})", inline=False)
            await channel.send(embed=embed)
            posts = g["starboard_posts"]
            posts.append(payload.message_id)
            del posts[:-200]
            self.bot.store.save()
        except Exception as e:  # noqa: BLE001
            print(f"Community: starboard failed: {e}",
                  file=sys.stderr, flush=True)
