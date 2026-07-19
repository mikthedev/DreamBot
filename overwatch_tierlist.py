"""Counterwatch Overwatch tier list — biweekly styled announcements."""

from __future__ import annotations

import logging
import re
import ssl
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from html import unescape

import aiohttp
import certifi
import discord
from discord.ext import commands, tasks

import config

log = logging.getLogger("dream_team.ow_tier")

TIER_URL = config.OW_TIER_URL
USER_AGENT = "DreamTeamBot/1.0 (+discord; tier-list monitor)"
_SSL_CTX = ssl.create_default_context(cafile=certifi.where())

TIER_ORDER = ("S", "A", "B", "C", "D", "F")
TIER_COLOR = {
    "S": discord.Color.from_rgb(255, 176, 32),
    "A": discord.Color.from_rgb(80, 200, 120),
    "B": discord.Color.from_rgb(64, 156, 255),
    "C": discord.Color.from_rgb(180, 180, 80),
    "D": discord.Color.from_rgb(232, 140, 60),
    "F": discord.Color.from_rgb(200, 80, 80),
}
TIER_LABEL = {
    "S": "S Tier",
    "A": "A Tier",
    "B": "B Tier",
    "C": "C Tier",
    "D": "D Tier",
    "F": "F Tier",
}
ROLE_SHORT = {
    "Tank": "Tank",
    "Damage": "DPS",
    "Support": "Support",
}


@dataclass
class TierHero:
    name: str
    role: str
    win_rate: str
    pick_rate: str
    slug: str = ""
    icon_url: str | None = None


@dataclass
class TierListSummary:
    season: str
    updated: str
    url: str = TIER_URL
    tiers: dict[str, list[TierHero]] = field(default_factory=dict)

    @property
    def fingerprint(self) -> str:
        return f"s{self.season}-{self.updated}".lower().replace(" ", "-").replace(",", "")

    @property
    def title(self) -> str:
        season = f"Season {self.season}" if self.season else "Overwatch"
        if self.updated:
            return f"{season} · {self.updated}"
        return season


def _strip_tags(html: str) -> str:
    text = re.sub(r"<[^>]+>", " ", html)
    return unescape(re.sub(r"\s+", " ", text)).strip()


def parse_tier_list(html: str) -> TierListSummary | None:
    season_m = re.search(r"Season\s+(\d+)", html)
    updated_m = re.search(
        r"updated\s+([A-Za-z]+\s+\d{1,2},\s+\d{4})", html, re.I
    )
    season = season_m.group(1) if season_m else ""
    updated = updated_m.group(1) if updated_m else ""

    sections = re.findall(
        r"([SABCDEF]) Tier</span>.*?<table[^>]*>(.*?)</table>",
        html,
        re.S,
    )
    if not sections:
        return None

    summary = TierListSummary(season=season, updated=updated)
    row_re = re.compile(
        r"^(?P<name>.+?)\s+(?P<role>Tank|Damage|Support)\s+"
        r"(?P<wr>\d+(?:\.\d+)?)\s*%\s+(?P<pr>\d+(?:\.\d+)?)\s*%",
        re.I,
    )

    for tier, table_html in sections:
        heroes: list[TierHero] = []
        for tr in re.finditer(r"<tr[^>]*>(.*?)</tr>", table_html, re.S):
            row = tr.group(1)
            if "<th" in row:
                continue
            plain = _strip_tags(row)
            plain = re.sub(r"Build a team around.*$", "", plain, flags=re.I).strip()
            m = row_re.match(plain)
            if not m:
                continue
            slug_m = re.search(r'/stats/overwatch/heroes/([^"/]+)', row)
            img_m = re.search(r'<img[^>]+src="([^"]+)"', row)
            icon = img_m.group(1) if img_m else None
            if icon and icon.startswith("/"):
                icon = "https://www.counterwatch.gg" + icon
            heroes.append(
                TierHero(
                    name=m.group("name").strip(),
                    role=m.group("role"),
                    win_rate=f"{m.group('wr')}%",
                    pick_rate=f"{m.group('pr')}%",
                    slug=slug_m.group(1) if slug_m else "",
                    icon_url=icon,
                )
            )
        if heroes:
            summary.tiers[tier] = heroes

    if not summary.tiers:
        return None
    return summary


async def fetch_tier_html(session: aiohttp.ClientSession | None = None) -> str:
    own = session is None
    if own:
        session = aiohttp.ClientSession(
            headers={"User-Agent": USER_AGENT, "Accept": "text/html"}
        )
    assert session is not None
    try:
        async with session.get(
            TIER_URL,
            ssl=_SSL_CTX,
            timeout=aiohttp.ClientTimeout(total=45),
        ) as resp:
            resp.raise_for_status()
            return await resp.text()
    finally:
        if own:
            await session.close()


async def fetch_tier_summary(
    session: aiohttp.ClientSession | None = None,
) -> TierListSummary | None:
    html = await fetch_tier_html(session)
    return parse_tier_list(html)


def _hero_stats(hero: TierHero) -> str:
    """Compact stats only — icon carries the identity."""
    return f"{hero.win_rate} · {hero.pick_rate}"


def _hero_line(hero: TierHero) -> str:
    """Embed fallback (no per-row icons)."""
    return f"{hero.name} — **{hero.win_rate}** · {hero.pick_rate}"


def _tier_body(heroes: list[TierHero]) -> str:
    return "\n".join(_hero_line(h) for h in heroes)


def _gallery_chunks(heroes: list[TierHero], size: int = 10) -> list[list[TierHero]]:
    return [heroes[i : i + size] for i in range(0, len(heroes), size)]


def build_tier_layouts(
    summary: TierListSummary, *, preview: bool = False
) -> list[discord.ui.LayoutView]:
    """
    Compact tier cards: hero icon grid with win rate · pick rate on each tile.
    """
    date_bit = summary.updated or "latest"
    season_bit = f"Season {summary.season}" if summary.season else "Overwatch"
    if preview:
        header = f"🧪 **Preview** · **[{season_bit}]({summary.url})** · {date_bit}"
    else:
        header = f"**Tier list** · **[{season_bit}]({summary.url})** · {date_bit}"

    BUDGET = 38
    views: list[discord.ui.LayoutView] = []
    view = discord.ui.LayoutView(timeout=None)
    view.add_item(discord.ui.TextDisplay(header))

    def flush() -> None:
        nonlocal view
        views.append(view)
        view = discord.ui.LayoutView(timeout=None)
        view.add_item(
            discord.ui.TextDisplay(
                f"**Tier list** · **[{season_bit}]({summary.url})** · cont."
            )
        )

    for tier in TIER_ORDER:
        heroes = summary.tiers.get(tier) or []
        if not heroes:
            continue

        chunks = _gallery_chunks(heroes)
        # Container(1) + title(1) + N galleries(1 each)
        cost = 2 + len(chunks)
        if view._total_children + cost > BUDGET:
            flush()

        container = discord.ui.Container(
            accent_colour=TIER_COLOR.get(tier, discord.Color.orange())
        )
        view.add_item(container)
        container.add_item(
            discord.ui.TextDisplay(f"**{TIER_LABEL.get(tier, tier)}**")
        )

        for chunk in chunks:
            items = [
                discord.MediaGalleryItem(
                    h.icon_url or "",
                    description=_hero_stats(h)[:256],
                )
                for h in chunk
                if h.icon_url
            ]
            if not items:
                # Fallback if icons missing
                container.add_item(
                    discord.ui.TextDisplay(_tier_body(chunk))
                )
                continue
            container.add_item(discord.ui.MediaGallery(*items))

    views.append(view)
    return views


def build_tier_embeds(
    summary: TierListSummary, *, preview: bool = False
) -> list[discord.Embed]:
    color = discord.Color.from_rgb(33, 143, 254) if preview else discord.Color.from_rgb(
        255, 176, 32
    )
    head = discord.Embed(
        title=summary.title,
        description=(
            ("🧪 **Preview**\n" if preview else "")
            + f"[Counterwatch tier list]({summary.url})\n"
            "Icon · win rate · pick rate"
        ),
        color=color,
        url=summary.url,
    )
    embeds = [head]
    for tier in TIER_ORDER:
        heroes = summary.tiers.get(tier) or []
        if not heroes:
            continue
        emb = discord.Embed(
            title=TIER_LABEL.get(tier, tier),
            description=_tier_body(heroes)[:4096],
            color=TIER_COLOR.get(tier, color),
        )
        embeds.append(emb)
    return embeds


class OverwatchTierCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._session: aiohttp.ClientSession | None = None
        self.check_tier_list.start()

    def cog_unload(self) -> None:
        self.check_tier_list.cancel()
        if self._session and not self._session.closed:
            self.bot.loop.create_task(self._session.close())

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={"User-Agent": USER_AGENT, "Accept": "text/html"}
            )
        return self._session

    async def get_summary(self) -> TierListSummary | None:
        session = await self._get_session()
        return await fetch_tier_summary(session)

    async def post_to_channel(
        self,
        channel: discord.TextChannel,
        summary: TierListSummary,
        *,
        preview: bool = False,
    ) -> list[discord.Message]:
        messages: list[discord.Message] = []
        try:
            layouts = build_tier_layouts(summary, preview=preview)
            for layout in layouts:
                messages.append(await channel.send(view=layout))
        except Exception as exc:
            log.warning("OW tier layout failed, using embeds: %s", exc)
            messages.append(
                await channel.send(embeds=build_tier_embeds(summary, preview=preview))
            )
        return messages

    async def delete_live_messages(
        self, channel: discord.TextChannel, guild_id: int
    ) -> None:
        for mid in self.bot.db.get_ow_tier_message_ids(guild_id):
            try:
                msg = await channel.fetch_message(mid)
                await msg.delete()
            except discord.NotFound:
                pass
            except discord.HTTPException as exc:
                log.warning("Could not delete old OW tier message %s: %s", mid, exc)

    async def publish_live(
        self, channel: discord.TextChannel, summary: TierListSummary
    ) -> list[discord.Message]:
        guild_id = channel.guild.id
        await self.delete_live_messages(channel, guild_id)
        messages = await self.post_to_channel(channel, summary, preview=False)
        self.bot.db.save_ow_tier_live(
            guild_id,
            summary.fingerprint,
            [m.id for m in messages],
        )
        return messages

    async def send_preview_ephemeral(self, interaction: discord.Interaction) -> None:
        summary = await self.get_summary()
        if summary is None:
            await interaction.followup.send(
                "Could not parse the Counterwatch tier list.", ephemeral=True
            )
            return
        try:
            layouts = build_tier_layouts(summary, preview=True)
            await interaction.followup.send(view=layouts[0], ephemeral=True)
            for layout in layouts[1:]:
                await interaction.followup.send(view=layout, ephemeral=True)
        except Exception as exc:
            log.warning("OW tier preview failed: %s", exc)
            await interaction.followup.send(
                embeds=build_tier_embeds(summary, preview=True),
                ephemeral=True,
            )

    def _due_for_post(self, guild_id: int) -> bool:
        last = self.bot.db.get_ow_tier_last_posted(guild_id)
        if last is None:
            return True
        interval = timedelta(days=config.OW_TIER_INTERVAL_DAYS)
        return datetime.now(timezone.utc) - last >= interval

    async def announce_if_due(self, guild: discord.Guild) -> tuple[bool, str]:
        channel_id = self.bot.db.get_ow_tier_channel(guild.id)
        if not channel_id:
            return False, "No Overwatch tier-list channel set."
        channel = guild.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            return False, "Tier-list channel missing."

        if not self._due_for_post(guild.id):
            last = self.bot.db.get_ow_tier_last_posted(guild.id)
            return False, f"Not due yet (last {last})."

        try:
            summary = await self.get_summary()
        except Exception as exc:
            log.warning("OW tier fetch failed: %s", exc)
            return False, f"Fetch failed: {exc}"

        if summary is None:
            return False, "Could not parse tier list page."

        await self.publish_live(channel, summary)
        return True, summary.title

    @tasks.loop(hours=config.OW_TIER_CHECK_HOURS)
    async def check_tier_list(self) -> None:
        for guild in self.bot.guilds:
            try:
                posted, detail = await self.announce_if_due(guild)
                if posted:
                    log.info("OW tier list posted in %s: %s", guild.name, detail)
            except Exception as exc:
                log.warning("OW tier check failed for %s: %s", guild.name, exc)

    @check_tier_list.before_loop
    async def before_check(self) -> None:
        await self.bot.wait_until_ready()
