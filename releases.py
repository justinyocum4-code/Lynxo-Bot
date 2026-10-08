"""Automatic metal new-release alerts: once a day the bot scans
MusicBrainz for metal albums/EPs released in the last 7 days and posts
any new ones in the configured channel, with Spotify / YouTube Music /
Amazon Music search buttons. Configured from the dashboard."""
import sys
from datetime import datetime, timedelta, timezone
from urllib.parse import quote_plus

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks

MB_URL = "https://musicbrainz.org/ws/2/release-group/"
MB_UA = "LynxoBot/1.0 (Discord music bot)"
MAX_POSTS_PER_RUN = 15
ANNOUNCED_CAP = 500


async def fetch_metal_releases():
    """Return [{mbid, title, artist, date, type}] for metal Albums/EPs
    first released in the last 7 days, oldest first. One HTTP request."""
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=7)
    query = f"firstreleasedate:[{start} TO {end}] AND tag:metal"
    params = {"fmt": "json", "limit": "100", "query": query}
    try:
        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(
                headers={"User-Agent": MB_UA}) as session:
            async with session.get(MB_URL, params=params,
                                   timeout=timeout) as resp:
                if resp.status != 200:
                    print(f"ReleaseAlerts: MusicBrainz returned "
                          f"HTTP {resp.status}.", file=sys.stderr, flush=True)
                    return []
                data = await resp.json()
    except Exception as e:  # noqa: BLE001
        print(f"ReleaseAlerts: could not reach MusicBrainz: {e}",
              file=sys.stderr, flush=True)
        return []
    out = []
    for rg in data.get("release-groups", []) or []:
        ptype = rg.get("primary-type") or ""
        if ptype not in ("Album", "EP"):
            continue
        artists = ", ".join(
            a.get("name", "") for a in (rg.get("artist-credit") or []))
        out.append({
            "mbid": rg.get("id") or "",
            "title": rg.get("title") or "Unknown title",
            "artist": artists or "Unknown artist",
            "date": rg.get("first-release-date") or "",
            "type": ptype,
        })
    out.sort(key=lambda r: r["date"] or "")
    return out


class ReleaseAlerts(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    def _config(self, guild):
        g = self.bot.store.guild(guild.id)
        cfg = g.get("releases") or {}
        if not isinstance(cfg.get("announced"), list):
            cfg["announced"] = []
        return cfg

    async def cog_load(self):
        self.check_loop.start()

    async def cog_unload(self):
        self.check_loop.cancel()

    @tasks.loop(hours=24)
    async def check_loop(self):
        for gid in self.bot.store.setup_guilds():
            guild = self.bot.get_guild(gid)
            if guild is None:
                continue
            try:
                posted = await self.run_check(guild)
                if posted:
                    print(f"ReleaseAlerts: posted {posted} new release(s) "
                          f"in {guild.name}.", file=sys.stderr, flush=True)
            except Exception as e:  # noqa: BLE001 - one guild must not
                print(f"ReleaseAlerts: check failed for {guild.name}: {e}",  # kill the loop
                      file=sys.stderr, flush=True)

    @check_loop.before_loop
    async def _before_check(self):
        await self.bot.wait_until_ready()

    async def run_check(self, guild):
        """Post new metal releases for one guild. Returns count posted."""
        cfg = self._config(guild)
        if not cfg.get("enabled"):
            return 0
        channel = (guild.get_channel(cfg.get("channel_id"))
                   if cfg.get("channel_id") else None)
        if channel is None:
            print(f"ReleaseAlerts: no announcement channel set in "
                  f"{guild.name}; skipping.", file=sys.stderr, flush=True)
            return 0
        rels = await fetch_metal_releases()
        seen = {a.get("id") for a in cfg.get("announced", [])}
        posted = 0
        for rel in rels:
            if rel["mbid"] in seen or posted >= MAX_POSTS_PER_RUN:
                continue
            try:
                await self.post_release(channel, rel)
            except (discord.Forbidden, discord.HTTPException) as e:
                print(f"ReleaseAlerts: could not post in {guild.name}: {e}",
                      file=sys.stderr, flush=True)
                continue
            cfg["announced"].append({
                "id": rel["mbid"],
                "title": rel["title"],
                "artist": rel["artist"],
            })
            seen.add(rel["mbid"])
            posted += 1
        while len(cfg["announced"]) > ANNOUNCED_CAP:
            cfg["announced"].pop(0)
        cfg["last_check"] = datetime.now(timezone.utc).isoformat()
        self.bot.store.save()
        return posted

    async def post_release(self, channel, rel):
        """Post one release embed with music-service search buttons."""
        query = quote_plus(f"{rel['artist']} {rel['title']}")
        embed = discord.Embed(
            title=rel["title"],
            description=rel["artist"],
            color=0xFFD700,
        )
        embed.add_field(name="Release date",
                        value=rel["date"] or "Unknown", inline=True)
        embed.add_field(name="Type", value=rel["type"], inline=True)
        embed.set_footer(text="New metal release")
        view = discord.ui.View()
        view.add_item(discord.ui.Button(
            style=discord.ButtonStyle.link, label="Spotify", emoji="🟢",
            url=f"https://open.spotify.com/search/{query}"))
        view.add_item(discord.ui.Button(
            style=discord.ButtonStyle.link, label="YouTube Music", emoji="🔴",
            url=f"https://music.youtube.com/search?q={query}"))
        view.add_item(discord.ui.Button(
            style=discord.ButtonStyle.link, label="Amazon Music", emoji="🔵",
            url=f"https://music.amazon.com/search/{query}"))
        await channel.send(embed=embed, view=view)

    @app_commands.command(name="releases_check",
                          description="Check for new metal releases now.")
    @app_commands.checks.has_permissions(administrator=True)
    async def releases_check(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            posted = await self.run_check(interaction.guild)
        except Exception as e:  # noqa: BLE001
            await interaction.followup.send(
                f"Could not check releases: {e}", ephemeral=True)
            return
        await interaction.followup.send(
            f"Posted {posted} new release(s).", ephemeral=True)
