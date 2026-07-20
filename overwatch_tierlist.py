"""Counterwatch Overwatch tier list — biweekly styled announcements."""

from __future__ import annotations

import asyncio
import io
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
from PIL import Image

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

    def all_heroes(self) -> list[TierHero]:
        out: list[TierHero] = []
        for tier in TIER_ORDER:
            out.extend(self.tiers.get(tier) or [])
        return out


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
    # Compact labels: bold % for scan, short words so win vs pick stays obvious
    return f"**{hero.win_rate}** win · **{hero.pick_rate}** pick"


def _hero_emoji_key(hero: TierHero) -> str:
    raw = (hero.slug or hero.name).lower()
    return re.sub(r"[^a-z0-9]+", "_", raw).strip("_") or "hero"


def emoji_name_for_hero(hero: TierHero) -> str:
    """Discord emoji names: 2–32 chars, [a-z0-9_]. Square thumbs (no circle mask)."""
    return f"ows_{_hero_emoji_key(hero)}"[:32]


def _icon_url(hero: TierHero) -> str | None:
    """Prefer Counterwatch *thumb* assets (not full 3D busts)."""
    icon = hero.icon_url
    if not icon:
        return None
    if "?" in icon:
        icon = icon.split("?", 1)[0]
    if "/full/" in icon:
        icon = icon.replace("/full/", "/thumb/")
    return icon


def _to_emoji_png(data: bytes) -> bytes:
    """Square PNG for app emojis — no circular mask."""
    im = Image.open(io.BytesIO(data)).convert("RGBA")
    w, h = im.size
    side = min(w, h)
    left = (w - side) // 2
    top = (h - side) // 2
    im = im.crop((left, top, left + side, top + side))
    im = im.resize((128, 128), Image.Resampling.LANCZOS)
    out = io.BytesIO()
    im.save(out, format="PNG", optimize=True)
    payload = out.getvalue()
    if len(payload) > 256_000:
        im = im.resize((96, 96), Image.Resampling.LANCZOS)
        out = io.BytesIO()
        im.save(out, format="PNG", optimize=True)
        payload = out.getvalue()
    return payload


def _hero_line(
    hero: TierHero, emoji_map: dict[str, discord.Emoji] | None = None
) -> str:
    """Emoji + rates only — icon identifies the hero."""
    stats = _hero_stats(hero)
    if emoji_map:
        em = emoji_map.get(emoji_name_for_hero(hero))
        if em is not None:
            return f"{em}  {stats}"
    return f"**{hero.name}** — {stats}"


def _tier_body(
    heroes: list[TierHero], emoji_map: dict[str, discord.Emoji] | None = None
) -> str:
    return "\n\n".join(_hero_line(h, emoji_map) for h in heroes)


# Discord: sum of all Text Display characters in one message ≤ 4000.
_TEXT_BUDGET = 3700


def _pack_hero_chunks(
    heroes: list[TierHero],
    emoji_map: dict[str, discord.Emoji] | None,
    *,
    title: str,
    budget: int,
) -> list[str]:
    """Split a tier into TextDisplay bodies that fit under `budget` chars."""
    chunks: list[str] = []
    lines: list[str] = []
    prefix = f"**{title}**\n\n"
    used = len(prefix)
    for hero in heroes:
        line = _hero_line(hero, emoji_map)
        # blank line between heroes (+2)
        cost = len(line) + (2 if lines else 0)
        if lines and used + cost > budget:
            chunks.append(prefix + "\n\n".join(lines))
            prefix = f"**{title}** · cont.\n\n"
            lines = [line]
            used = len(prefix) + len(line)
        else:
            lines.append(line)
            used += cost
    if lines:
        chunks.append(prefix + "\n\n".join(lines))
    return chunks


def _tier_header(summary: TierListSummary, *, preview: bool = False) -> str:
    date_bit = summary.updated or "latest"
    season_bit = f"S{summary.season}" if summary.season else "OW"
    title = "**Tier list by win rate**"
    if preview:
        title = f"🧪 {title}"
    meta = f"[{season_bit} tier list]({summary.url}) · {date_bit}"
    legend = "_win % · pick %_"
    return f"{title}\n{meta}\n{legend}"


def build_tier_layouts(
    summary: TierListSummary,
    *,
    preview: bool = False,
    emoji_map: dict[str, discord.Emoji] | None = None,
) -> list[discord.ui.LayoutView]:
    """
    Square app-emoji icons + rates.
    Splits across messages so each stays under Discord's 4000 displayable-char cap.
    """
    season_bit = f"S{summary.season}" if summary.season else "OW"
    header = _tier_header(summary, preview=preview)

    views: list[discord.ui.LayoutView] = []
    view = discord.ui.LayoutView(timeout=None)
    chars = 0
    comps = 0

    def flush() -> None:
        nonlocal view, chars, comps
        views.append(view)
        view = discord.ui.LayoutView(timeout=None)
        cont = f"**[{season_bit}]({summary.url})** · cont."
        view.add_item(discord.ui.TextDisplay(cont))
        chars = len(cont)
        comps = 1

    def ensure_room(extra_chars: int, extra_comps: int = 2) -> None:
        if comps + extra_comps > 35 or chars + extra_chars > _TEXT_BUDGET:
            flush()

    view.add_item(discord.ui.TextDisplay(header))
    chars = len(header)
    comps = 1

    for tier in TIER_ORDER:
        heroes = summary.tiers.get(tier) or []
        if not heroes:
            continue

        bodies = _pack_hero_chunks(
            heroes,
            emoji_map,
            title=tier,
            budget=min(3500, _TEXT_BUDGET - 80),
        )
        for body in bodies:
            ensure_room(len(body), 2)
            container = discord.ui.Container(
                accent_colour=TIER_COLOR.get(tier, discord.Color.orange())
            )
            view.add_item(container)
            container.add_item(discord.ui.TextDisplay(body))
            chars += len(body)
            comps += 2

    views.append(view)
    return views


def build_tier_embeds(
    summary: TierListSummary,
    *,
    preview: bool = False,
    emoji_map: dict[str, discord.Emoji] | None = None,
) -> list[discord.Embed]:
    color = discord.Color.from_rgb(33, 143, 254) if preview else discord.Color.from_rgb(
        255, 176, 32
    )
    season_bit = f"S{summary.season}" if summary.season else "OW"
    date_bit = summary.updated or "latest"
    head = discord.Embed(
        title="Tier list by win rate",
        description=(
            ("🧪 **Preview**\n" if preview else "")
            + f"**[{season_bit} tier list]({summary.url})** · {date_bit}\n"
            "_win % · pick %_"
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
            description=_tier_body(heroes, emoji_map)[:4096],
            color=TIER_COLOR.get(tier, color),
        )
        embeds.append(emb)
    return embeds


class OverwatchTierCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._session: aiohttp.ClientSession | None = None
        self._emoji_cache: dict[str, discord.Emoji] | None = None
        self.check_tier_list.start()

    def cog_unload(self) -> None:
        self.check_tier_list.cancel()
        if self._session and not self._session.closed:
            self.bot.loop.create_task(self._session.close())

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={"User-Agent": USER_AGENT, "Accept": "text/html,image/*"}
            )
        return self._session

    async def get_summary(self) -> TierListSummary | None:
        session = await self._get_session()
        return await fetch_tier_summary(session)

    async def ensure_hero_emojis(
        self, summary: TierListSummary
    ) -> dict[str, discord.Emoji]:
        """
        Upload missing hero icons as *application* emojis (not server slots).
        First run may take a minute; later posts reuse them.
        """
        if self._emoji_cache is not None:
            needed = {emoji_name_for_hero(h) for h in summary.all_heroes()}
            if needed <= set(self._emoji_cache):
                return self._emoji_cache

        existing = {
            e.name: e for e in await self.bot.fetch_application_emojis()
        }
        session = await self._get_session()
        created = 0

        for hero in summary.all_heroes():
            name = emoji_name_for_hero(hero)
            if name in existing:
                continue
            url = _icon_url(hero)
            if not url:
                continue
            try:
                async with session.get(
                    url, ssl=_SSL_CTX, timeout=aiohttp.ClientTimeout(total=20)
                ) as resp:
                    if resp.status != 200:
                        log.warning("Emoji icon fetch %s → %s", name, resp.status)
                        continue
                    raw = await resp.read()
                    png = _to_emoji_png(raw)
                    emoji = await self.bot.create_application_emoji(name=name, image=png)
                existing[name] = emoji
                created += 1
                log.info("Created app emoji %s", name)
                # Gentle pacing for Discord rate limits
                await asyncio.sleep(0.7)
            except discord.HTTPException as exc:
                log.warning("Could not create emoji %s: %s", name, exc)
            except Exception as exc:
                log.warning("Emoji sync failed for %s: %s", name, exc)

        if created:
            log.info("Synced %s new Overwatch hero emojis", created)

        self._emoji_cache = existing
        return existing

    async def post_to_channel(
        self,
        channel: discord.TextChannel,
        summary: TierListSummary,
        *,
        preview: bool = False,
    ) -> list[discord.Message]:
        emoji_map = await self.ensure_hero_emojis(summary)
        messages: list[discord.Message] = []

        try:
            layouts = build_tier_layouts(
                summary,
                preview=preview,
                emoji_map=emoji_map,
            )
            for layout in layouts:
                messages.append(await channel.send(view=layout))
        except Exception as exc:
            log.warning("OW tier layout failed, using embeds: %s", exc)
            messages.append(
                await channel.send(
                    embeds=build_tier_embeds(
                        summary, preview=preview, emoji_map=emoji_map
                    )
                )
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
            emoji_map = await self.ensure_hero_emojis(summary)
            layouts = build_tier_layouts(
                summary,
                preview=True,
                emoji_map=emoji_map,
            )
            await interaction.followup.send(view=layouts[0], ephemeral=True)
            for layout in layouts[1:]:
                await interaction.followup.send(view=layout, ephemeral=True)
        except Exception as exc:
            log.warning("OW tier preview failed: %s", exc)
            try:
                emoji_map = await self.ensure_hero_emojis(summary)
            except Exception:
                emoji_map = None
            await interaction.followup.send(
                embeds=build_tier_embeds(
                    summary, preview=True, emoji_map=emoji_map
                ),
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
