"""Counterwatch Overwatch tier list — biweekly styled announcements."""

from __future__ import annotations

import asyncio
import gc
import hashlib
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
from ow_forum import (
    OW_TIER_TAG_NAMES,
    is_ow_destination,
    post_ow_announcement,
    tier_thread_title,
)

log = logging.getLogger("dream_team.ow_tier")

TIER_URL = config.OW_TIER_URL
BLIZZARD_HEROES_URL = "https://overwatch.blizzard.com/en-us/heroes/"
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
    # Prefer slug; strip punctuation so "D.Mon" / "dmon" / "D.Va" stay stable
    raw = (hero.slug or hero.name).lower().replace(".", "").replace("'", "")
    return re.sub(r"[^a-z0-9]+", "_", raw).strip("_") or "hero"


def emoji_name_for_hero(hero: TierHero) -> str:
    """Discord emoji names: 2–32 chars, [a-z0-9_]. Square thumbs (no circle mask)."""
    return f"ows_{_hero_emoji_key(hero)}"[:32]


def emoji_name_for_ability(hero: str, ability: str) -> str:
    """Stable app-emoji name for a hero ability (≤32 chars)."""
    key = f"{_normalize_hero_token(hero)}|{_normalize_hero_token(ability)}"
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:10]
    return f"owa_{digest}"


def _normalize_hero_token(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (value or "").lower())


@dataclass
class BlizzardHeroIcon:
    hero_id: str
    name: str
    icon_url: str


@dataclass
class BlizzardAbilityIcon:
    hero_id: str
    hero_name: str
    ability: str
    icon_url: str


def parse_blizzard_ability_icons(
    html: str, *, hero_id: str, hero_name: str
) -> list[BlizzardAbilityIcon]:
    """Ability name + icon from an official Blizzard hero page."""
    pairs = re.findall(
        r'<span class="ability-name[^"]*">([^<]+)</span>\s*'
        r'<blz-image[^>]*src="'
        r'(https://d15f34w2p8l1cc\.cloudfront\.net/overwatch/[^"]+)"',
        html,
        re.I,
    )
    out: list[BlizzardAbilityIcon] = []
    seen: set[str] = set()
    for raw_name, icon_url in pairs:
        name = unescape(raw_name).strip()
        if not name:
            continue
        token = _normalize_hero_token(name)
        if not token or token in seen:
            continue
        if token in ("general", "hero", "passive"):
            continue
        seen.add(token)
        out.append(
            BlizzardAbilityIcon(
                hero_id=hero_id,
                hero_name=hero_name,
                ability=name,
                icon_url=icon_url.strip(),
            )
        )
    return out


async def fetch_blizzard_ability_icons(
    session: aiohttp.ClientSession,
    heroes: list[BlizzardHeroIcon],
    *,
    concurrency: int = 3,
) -> list[BlizzardAbilityIcon]:
    """Pull ability icons for every hero on the official roster."""
    sem = asyncio.Semaphore(concurrency)
    out: list[BlizzardAbilityIcon] = []

    async def one(hero: BlizzardHeroIcon) -> list[BlizzardAbilityIcon]:
        url = f"{BLIZZARD_HEROES_URL}{hero.hero_id}/"
        async with sem:
            try:
                async with session.get(
                    url,
                    ssl=_SSL_CTX,
                    timeout=aiohttp.ClientTimeout(total=30),
                    headers={"User-Agent": USER_AGENT, "Accept": "text/html"},
                ) as resp:
                    if resp.status != 200:
                        log.warning(
                            "Ability page %s → %s", hero.hero_id, resp.status
                        )
                        return []
                    html = await resp.text()
            except Exception as exc:
                log.warning("Ability page %s failed: %s", hero.hero_id, exc)
                return []
        return parse_blizzard_ability_icons(
            html, hero_id=hero.hero_id, hero_name=hero.name
        )

    results = await asyncio.gather(*(one(h) for h in heroes))
    for batch in results:
        out.extend(batch)
    log.info(
        "Blizzard ability icons: %s abilities across %s heroes",
        len(out),
        len(heroes),
    )
    return out


def parse_blizzard_hero_icons(html: str) -> list[BlizzardHeroIcon]:
    """Official roster portraits from overwatch.blizzard.com/heroes/."""
    cards = re.findall(
        r'<a class="hero-card"[^>]*\bid="([^"]+)"[^>]*>'
        r'.*?alt="([^"]*)"[^>]*src="(https://d15f34w2p8l1cc\.cloudfront\.net/overwatch/[^"]+)"'
        r"[^>]*>.*?<h2[^>]*>([^<]+)</h2>",
        html,
        re.S | re.I,
    )
    out: list[BlizzardHeroIcon] = []
    seen: set[str] = set()
    for hero_id, _alt, icon_url, name in cards:
        hero_id = hero_id.strip().lower()
        if not hero_id or hero_id in seen:
            continue
        seen.add(hero_id)
        out.append(
            BlizzardHeroIcon(
                hero_id=hero_id,
                name=unescape(name).strip(),
                icon_url=icon_url.strip(),
            )
        )
    return out


async def fetch_blizzard_hero_icons(
    session: aiohttp.ClientSession,
) -> list[BlizzardHeroIcon]:
    async with session.get(
        BLIZZARD_HEROES_URL,
        ssl=_SSL_CTX,
        timeout=aiohttp.ClientTimeout(total=45),
        headers={"User-Agent": USER_AGENT, "Accept": "text/html"},
    ) as resp:
        resp.raise_for_status()
        html = await resp.text()
    icons = parse_blizzard_hero_icons(html)
    log.info("Blizzard hero roster: %s icons", len(icons))
    return icons


def _blizzard_icon_indexes(
    icons: list[BlizzardHeroIcon],
) -> tuple[dict[str, str], dict[str, str]]:
    by_id: dict[str, str] = {}
    by_name: dict[str, str] = {}
    for icon in icons:
        by_id[icon.hero_id] = icon.icon_url
        by_id[_normalize_hero_token(icon.hero_id)] = icon.icon_url
        by_name[_normalize_hero_token(icon.name)] = icon.icon_url
    return by_id, by_name


def resolve_hero_icon_url(
    hero: TierHero,
    *,
    blizzard_by_id: dict[str, str] | None = None,
    blizzard_by_name: dict[str, str] | None = None,
) -> str | None:
    """Prefer official Blizzard CDN portraits; fall back to Counterwatch."""
    blizzard_by_id = blizzard_by_id or {}
    blizzard_by_name = blizzard_by_name or {}

    for token in (
        (hero.slug or "").lower(),
        _normalize_hero_token(hero.slug or ""),
        _normalize_hero_token(hero.name),
    ):
        if token and token in blizzard_by_id:
            return blizzard_by_id[token]
        if token and token in blizzard_by_name:
            return blizzard_by_name[token]

    return _icon_url(hero)


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
    with Image.open(io.BytesIO(data)) as src:
        im = src.convert("RGBA")
    try:
        w, h = im.size
        side = min(w, h)
        left = (w - side) // 2
        top = (h - side) // 2
        cropped = im.crop((left, top, left + side, top + side))
        im.close()
        im = cropped
        resized = im.resize((128, 128), Image.Resampling.LANCZOS)
        im.close()
        im = resized
        out = io.BytesIO()
        im.save(out, format="PNG", optimize=True)
        payload = out.getvalue()
        if len(payload) > 256_000:
            smaller = im.resize((96, 96), Image.Resampling.LANCZOS)
            im.close()
            im = smaller
            out = io.BytesIO()
            im.save(out, format="PNG", optimize=True)
            payload = out.getvalue()
        return payload
    finally:
        try:
            im.close()
        except Exception:
            pass


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
        self._ability_emoji_cache: dict[str, discord.Emoji] | None = None
        # (hero_token, ability_token) -> icon_url
        self._ability_icon_index: dict[tuple[str, str], str] = {}
        self.check_tier_list.start()
        self.sync_hero_icons.start()

    def cog_unload(self) -> None:
        self.check_tier_list.cancel()
        self.sync_hero_icons.cancel()
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
        Create/update *application* emojis from official Blizzard portraits
        (fallback: Counterwatch). New heroes and changed CDN icons refresh automatically.
        """
        session = await self._get_session()
        blizzard_by_id: dict[str, str] = {}
        blizzard_by_name: dict[str, str] = {}
        blizzard_icons: list[BlizzardHeroIcon] = []
        try:
            blizzard_icons = await fetch_blizzard_hero_icons(session)
            blizzard_by_id, blizzard_by_name = _blizzard_icon_indexes(blizzard_icons)
        except Exception as exc:
            log.warning("Blizzard hero icon fetch failed: %s", exc)

        # Sync every roster hero + anyone currently on the tier/META board
        work: dict[str, TierHero] = {}
        for icon in blizzard_icons:
            stub = TierHero(
                name=icon.name,
                role="",
                win_rate="",
                pick_rate="",
                slug=icon.hero_id,
                icon_url=icon.icon_url,
            )
            work[emoji_name_for_hero(stub)] = stub
        for hero in summary.all_heroes():
            work[emoji_name_for_hero(hero)] = hero

        existing = {
            e.name: e for e in await self.bot.fetch_application_emojis()
        }
        created = 0
        updated = 0

        for name, hero in work.items():
            url = resolve_hero_icon_url(
                hero,
                blizzard_by_id=blizzard_by_id,
                blizzard_by_name=blizzard_by_name,
            )
            if not url:
                continue

            stored = self.bot.db.get_hero_emoji_icon(name)
            if (
                name in existing
                and stored is not None
                and stored["icon_url"] == url
            ):
                # Same CDN URL as last sync — skip download
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
                sha = hashlib.sha256(png).hexdigest()

                if (
                    name in existing
                    and stored is not None
                    and stored["sha256"] == sha
                ):
                    # Content unchanged even if URL string differed
                    self.bot.db.set_hero_emoji_icon(name, url, sha)
                    continue

                if name in existing:
                    emoji = existing[name]
                    try:
                        owned = emoji.is_application_owned()
                    except Exception:
                        owned = True
                    if not owned:
                        continue
                    # Discord application emojis can only change name via edit —
                    # image updates require delete + recreate.
                    await emoji.delete()
                    emoji = await self.bot.create_application_emoji(
                        name=name, image=png
                    )
                    existing[name] = emoji
                    updated += 1
                    log.info("Updated app emoji %s (recreated icon)", name)
                else:
                    emoji = await self.bot.create_application_emoji(
                        name=name, image=png
                    )
                    existing[name] = emoji
                    created += 1
                    log.info("Created app emoji %s", name)

                self.bot.db.set_hero_emoji_icon(name, url, sha)
                await asyncio.sleep(0.7)
            except discord.HTTPException as exc:
                log.warning("Could not sync emoji %s: %s", name, exc)
            except Exception as exc:
                log.warning("Emoji sync failed for %s: %s", name, exc)
            if (created + updated) and (created + updated) % 8 == 0:
                gc.collect()

        if created or updated:
            log.info(
                "Hero emoji sync: created=%s updated=%s total=%s",
                created,
                updated,
                len(existing),
            )
            gc.collect()

        self._emoji_cache = existing
        return existing

    async def _sync_emoji_from_url(
        self,
        *,
        name: str,
        url: str,
        session: aiohttp.ClientSession,
        existing: dict[str, discord.Emoji],
    ) -> tuple[str, int, int]:
        """Create/update one app emoji. Returns (status, created_delta, updated_delta)."""
        stored = self.bot.db.get_hero_emoji_icon(name)
        if (
            name in existing
            and stored is not None
            and stored["icon_url"] == url
        ):
            return "skip", 0, 0
        try:
            async with session.get(
                url, ssl=_SSL_CTX, timeout=aiohttp.ClientTimeout(total=20)
            ) as resp:
                if resp.status != 200:
                    log.warning("Emoji icon fetch %s → %s", name, resp.status)
                    return "fail", 0, 0
                raw = await resp.read()
            png = _to_emoji_png(raw)
            sha = hashlib.sha256(png).hexdigest()
            if (
                name in existing
                and stored is not None
                and stored["sha256"] == sha
            ):
                self.bot.db.set_hero_emoji_icon(name, url, sha)
                return "skip", 0, 0
            if name in existing:
                emoji = existing[name]
                try:
                    owned = emoji.is_application_owned()
                except Exception:
                    owned = True
                if not owned:
                    return "skip", 0, 0
                await emoji.delete()
                emoji = await self.bot.create_application_emoji(
                    name=name, image=png
                )
                existing[name] = emoji
                self.bot.db.set_hero_emoji_icon(name, url, sha)
                await asyncio.sleep(0.7)
                return "updated", 0, 1
            emoji = await self.bot.create_application_emoji(name=name, image=png)
            existing[name] = emoji
            self.bot.db.set_hero_emoji_icon(name, url, sha)
            await asyncio.sleep(0.7)
            return "created", 1, 0
        except discord.HTTPException as exc:
            log.warning("Could not sync emoji %s: %s", name, exc)
            return "fail", 0, 0
        except Exception as exc:
            log.warning("Emoji sync failed for %s: %s", name, exc)
            return "fail", 0, 0

    def ability_icon_url(self, hero: str, ability: str) -> str | None:
        key = (_normalize_hero_token(hero), _normalize_hero_token(ability))
        return self._ability_icon_index.get(key)

    async def refresh_ability_icon_index(self) -> dict[tuple[str, str], str]:
        """Load ability icons from Blizzard hero pages into memory."""
        session = await self._get_session()
        heroes = await fetch_blizzard_hero_icons(session)
        abilities = await fetch_blizzard_ability_icons(session, heroes)
        index: dict[tuple[str, str], str] = {}
        for ab in abilities:
            h = _normalize_hero_token(ab.hero_name)
            a = _normalize_hero_token(ab.ability)
            if h and a and ab.icon_url:
                index[(h, a)] = ab.icon_url
                # also index by hero_id slug
                hid = _normalize_hero_token(ab.hero_id)
                if hid:
                    index[(hid, a)] = ab.icon_url
        self._ability_icon_index = index
        return index

    async def ensure_ability_emojis(
        self,
        pairs: list[tuple[str, str, str | None]],
        *,
        refresh_roster: bool = False,
    ) -> dict[str, discord.Emoji]:
        """
        Sync app emojis for (hero, ability, icon_url?) tuples.
        Missing URLs are filled from the Blizzard hero-page catalog.
        """
        if refresh_roster or not self._ability_icon_index:
            try:
                await self.refresh_ability_icon_index()
            except Exception as exc:
                log.warning("Ability roster refresh failed: %s", exc)

        work: dict[str, tuple[str, str, str]] = {}
        for hero, ability, icon_url in pairs:
            ability = (ability or "").strip()
            hero = (hero or "").strip()
            if not hero or not ability:
                continue
            if _normalize_hero_token(ability) in ("general", "hero", ""):
                continue
            url = (icon_url or "").strip() or (
                self.ability_icon_url(hero, ability) or ""
            )
            if not url:
                continue
            name = emoji_name_for_ability(hero, ability)
            work[name] = (hero, ability, url)

        existing = {
            e.name: e for e in await self.bot.fetch_application_emojis()
        }
        if not work:
            self._ability_emoji_cache = {
                k: v for k, v in existing.items() if k.startswith("owa_")
            }
            # Still return full map so callers can resolve names
            if self._emoji_cache is not None:
                existing.update(self._emoji_cache)
            return existing

        session = await self._get_session()
        created = updated = 0
        for name, (_hero, _ability, url) in work.items():
            status, c, u = await self._sync_emoji_from_url(
                name=name, url=url, session=session, existing=existing
            )
            created += c
            updated += u
            if status == "fail":
                continue
            if (created + updated) % 8 == 0:
                gc.collect()

        if created or updated:
            log.info(
                "Ability emoji sync: created=%s updated=%s needed=%s",
                created,
                updated,
                len(work),
            )
            gc.collect()
        self._ability_emoji_cache = {
            k: v for k, v in existing.items() if k.startswith("owa_")
        }
        if self._emoji_cache is not None:
            existing.update(self._emoji_cache)
        return existing

    async def ensure_ability_emojis_for_patch(
        self, summary
    ) -> dict[str, discord.Emoji]:
        """Sync ability icons referenced by a patch summary."""
        pairs: list[tuple[str, str, str | None]] = []
        for hero in getattr(summary, "heroes", None) or []:
            for ch in hero.changes:
                pairs.append((hero.name, ch.ability, ch.icon_url))
                # Fill missing icon_url on the change for payload persistence
                if not ch.icon_url:
                    filled = self.ability_icon_url(hero.name, ch.ability)
                    if filled:
                        ch.icon_url = filled
        return await self.ensure_ability_emojis(pairs)

    async def sync_blizzard_hero_emojis(self) -> dict[str, discord.Emoji]:
        """Force a full roster emoji sync for hero portraits only.

        Ability icons are synced on demand when a patch/history card needs them —
        uploading ~270 ability PNGs at once OOMs 256–512 MB bot-hosting plans.
        """
        self._emoji_cache = None
        empty = TierListSummary(season="", updated="")
        heroes = await self.ensure_hero_emojis(empty)
        # Refresh URL index for later on-demand ability sync (HTML only, no PNG uploads)
        try:
            await self.refresh_ability_icon_index()
        except Exception as exc:
            log.warning("Ability icon index refresh failed: %s", exc)
        gc.collect()
        return heroes

    async def post_to_channel(
        self,
        channel: discord.TextChannel | discord.ForumChannel,
        summary: TierListSummary,
        *,
        preview: bool = False,
        existing_thread_id: int | None = None,
    ) -> tuple[list[discord.Message], int | None]:
        emoji_map = await self.ensure_hero_emojis(summary)
        layouts = build_tier_layouts(
            summary,
            preview=preview,
            emoji_map=emoji_map,
        )
        return await post_ow_announcement(
            channel,
            thread_name=tier_thread_title(
                season=summary.season, updated=summary.updated
            ),
            layouts=layouts,
            embeds_fallback=lambda: build_tier_embeds(
                summary, preview=preview, emoji_map=emoji_map
            ),
            tag_names=OW_TIER_TAG_NAMES,
            existing_thread_id=existing_thread_id,
        )

    async def delete_live_messages(
        self, channel: discord.TextChannel, guild_id: int
    ) -> None:
        """Only used for classic text channels — forum posts are edited in place."""
        for mid in self.bot.db.get_ow_tier_message_ids(guild_id):
            try:
                msg = await channel.fetch_message(mid)
                await msg.delete()
            except discord.NotFound:
                pass
            except discord.HTTPException as exc:
                log.warning("Could not delete old OW tier message %s: %s", mid, exc)

    async def publish_live(
        self,
        channel: discord.TextChannel | discord.ForumChannel,
        summary: TierListSummary,
    ) -> list[discord.Message]:
        guild_id = channel.guild.id
        existing_thread_id = None
        if isinstance(channel, discord.TextChannel):
            await self.delete_live_messages(channel, guild_id)
        else:
            existing_thread_id = self.bot.db.get_ow_tier_thread_id(guild_id)

        messages, thread_id, _ = await self.post_to_channel(
            channel,
            summary,
            preview=False,
            existing_thread_id=existing_thread_id,
        )
        if thread_id is not None:
            self.bot.db.set_ow_tier_thread_id(guild_id, thread_id)
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
        if not is_ow_destination(channel):
            return False, "Tier-list channel missing (set a forum or text channel)."

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

    @tasks.loop(hours=24)
    async def sync_hero_icons(self) -> None:
        """Daily: pick up new heroes and refreshed Blizzard CDN portraits."""
        try:
            result = await self.sync_blizzard_hero_emojis()
            log.info(
                "Daily hero icon sync done (%s emojis)",
                len(result),
            )
        except Exception as exc:
            log.warning("Daily hero icon sync failed: %s", exc)

    @sync_hero_icons.before_loop
    async def before_sync_hero_icons(self) -> None:
        await self.bot.wait_until_ready()
        # Wait for login + first patch check to finish before touching images
        await asyncio.sleep(180)
