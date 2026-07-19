"""Counterwatch Overwatch tier list — biweekly styled announcements."""

from __future__ import annotations

import io
import logging
import math
import re
import ssl
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from html import unescape

import aiohttp
import certifi
import discord
from discord.ext import commands, tasks
from PIL import Image, ImageDraw, ImageFont

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
TIER_RGB = {
    "S": (255, 176, 32),
    "A": (80, 200, 120),
    "B": (64, 156, 255),
    "C": (180, 180, 80),
    "D": (232, 140, 60),
    "F": (200, 80, 80),
}
TIER_LABEL = {
    "S": "S Tier",
    "A": "A Tier",
    "B": "B Tier",
    "C": "C Tier",
    "D": "D Tier",
    "F": "F Tier",
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
    return f"{hero.win_rate} win rate · {hero.pick_rate} pick rate"


def _hero_line(hero: TierHero) -> str:
    return f"{hero.name} — {_hero_stats(hero)}"


def _tier_body(heroes: list[TierHero]) -> str:
    return "\n".join(_hero_line(h) for h in heroes)


def _icon_url(hero: TierHero) -> str | None:
    icon = hero.icon_url
    if not icon:
        return None
    if "?" in icon:
        icon = icon.split("?", 1)[0]
    if "/full/" in icon:
        icon = icon.replace("/full/", "/thumb/")
    return icon


def _load_font(size: int) -> ImageFont.ImageFont:
    for path in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/TTF/DejaVuSans.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/Library/Fonts/Arial.ttf",
    ):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


async def _fetch_icon_bytes(
    session: aiohttp.ClientSession, url: str
) -> bytes | None:
    try:
        async with session.get(
            url, ssl=_SSL_CTX, timeout=aiohttp.ClientTimeout(total=20)
        ) as resp:
            if resp.status != 200:
                return None
            return await resp.read()
    except Exception:
        return None


async def fetch_hero_icons(
    session: aiohttp.ClientSession, summary: TierListSummary
) -> dict[str, bytes]:
    """Download hero thumbnails keyed by slug/name."""
    out: dict[str, bytes] = {}
    seen: set[str] = set()
    for heroes in summary.tiers.values():
        for h in heroes:
            key = h.slug or h.name
            url = _icon_url(h)
            if not url or key in seen:
                continue
            seen.add(key)
            data = await _fetch_icon_bytes(session, url)
            if data:
                out[key] = data
    return out


def render_tier_strip(
    tier: str, heroes: list[TierHero], icons: dict[str, bytes]
) -> io.BytesIO:
    """
    Compact grid of small portraits with win rate / pick rate under each.
    Discord can't shrink Section thumbnails — we control size here.
    """
    icon_px = 40
    cell_w = 92
    text_h = 28
    cell_h = icon_px + text_h + 8
    pad = 8
    cols = min(5, max(1, len(heroes)))
    rows = max(1, math.ceil(len(heroes) / cols))
    header_h = 26

    width = pad * 2 + cols * cell_w
    height = pad * 2 + header_h + rows * cell_h
    bg = (22, 24, 32)
    accent = TIER_RGB.get(tier, (249, 158, 26))

    img = Image.new("RGBA", (width, height), bg + (255,))
    draw = ImageDraw.Draw(img)
    title_font = _load_font(16)
    small_font = _load_font(10)

    # Accent bar + tier title
    draw.rectangle((0, 0, 4, height), fill=accent + (255,))
    draw.text((pad + 6, 6), tier, font=title_font, fill=accent + (255,))

    for i, hero in enumerate(heroes):
        col = i % cols
        row = i // cols
        x0 = pad + col * cell_w
        y0 = pad + header_h + row * cell_h

        key = hero.slug or hero.name
        portrait = None
        raw = icons.get(key)
        if raw:
            try:
                portrait = Image.open(io.BytesIO(raw)).convert("RGBA")
                portrait = portrait.resize((icon_px, icon_px), Image.Resampling.LANCZOS)
            except Exception:
                portrait = None

        ix = x0 + (cell_w - icon_px) // 2
        iy = y0
        if portrait is not None:
            # Circular-ish rounded mask for a tighter look
            mask = Image.new("L", (icon_px, icon_px), 0)
            ImageDraw.Draw(mask).rounded_rectangle(
                (0, 0, icon_px - 1, icon_px - 1), radius=8, fill=255
            )
            img.paste(portrait, (ix, iy), mask)
        else:
            draw.rounded_rectangle(
                (ix, iy, ix + icon_px, iy + icon_px),
                radius=8,
                fill=(50, 54, 68, 255),
            )

        # Tiny labels under the icon
        line1 = f"{hero.win_rate} win rate"
        line2 = f"{hero.pick_rate} pick rate"
        tw1 = draw.textlength(line1, font=small_font)
        tw2 = draw.textlength(line2, font=small_font)
        draw.text(
            (x0 + (cell_w - tw1) / 2, iy + icon_px + 2),
            line1,
            font=small_font,
            fill=(230, 230, 235, 255),
        )
        draw.text(
            (x0 + (cell_w - tw2) / 2, iy + icon_px + 13),
            line2,
            font=small_font,
            fill=(160, 168, 180, 255),
        )

    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="PNG", optimize=True)
    buf.seek(0)
    return buf


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
            "Win rate · pick rate"
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


async def build_tier_message(
    summary: TierListSummary,
    session: aiohttp.ClientSession,
    *,
    preview: bool = False,
) -> tuple[discord.ui.LayoutView, list[discord.File]]:
    """One compact message: small rendered icon grids attached as images."""
    icons = await fetch_hero_icons(session, summary)

    date_bit = summary.updated or "latest"
    season_bit = f"S{summary.season}" if summary.season else "OW"
    if preview:
        header = f"🧪 **[{season_bit} tier list]({summary.url})** · {date_bit}"
    else:
        header = f"**[{season_bit} tier list]({summary.url})** · {date_bit}"

    view = discord.ui.LayoutView(timeout=None)
    view.add_item(discord.ui.TextDisplay(header))

    files: list[discord.File] = []
    gallery_items: list[discord.MediaGalleryItem] = []

    for tier in TIER_ORDER:
        heroes = summary.tiers.get(tier) or []
        if not heroes:
            continue
        filename = f"tier_{tier.lower()}.png"
        buf = render_tier_strip(tier, heroes, icons)
        files.append(discord.File(buf, filename=filename))
        gallery_items.append(
            discord.MediaGalleryItem(
                f"attachment://{filename}",
                description=f"{TIER_LABEL.get(tier, tier)} · win rate · pick rate",
            )
        )

    if gallery_items:
        # One gallery keeps the post short; Discord shows a compact image stack/grid
        view.add_item(discord.ui.MediaGallery(*gallery_items))
    else:
        view.add_item(
            discord.ui.TextDisplay("_Could not render tier icons._")
        )

    return view, files


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
        session = await self._get_session()
        try:
            view, files = await build_tier_message(
                summary, session, preview=preview
            )
            msg = await channel.send(view=view, files=files)
            return [msg]
        except Exception as exc:
            log.warning("OW tier layout failed, using embeds: %s", exc)
            return [
                await channel.send(
                    embeds=build_tier_embeds(summary, preview=preview)
                )
            ]

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
        session = await self._get_session()
        try:
            view, files = await build_tier_message(
                summary, session, preview=True
            )
            await interaction.followup.send(
                view=view, files=files, ephemeral=True
            )
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
