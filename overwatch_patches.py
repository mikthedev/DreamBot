"""Overwatch patch notes monitor — daily fetch, concise hero summary, styled embeds."""

from __future__ import annotations

import logging
import re
import ssl
from dataclasses import dataclass, field
from html import unescape

import aiohttp
import certifi
import discord
from discord.ext import commands, tasks

import config

log = logging.getLogger("dream_team.overwatch")

PATCH_URL = config.OW_PATCH_URL
OW_ORANGE = discord.Color.from_rgb(249, 158, 26)
OW_BLUE = discord.Color.from_rgb(33, 143, 254)
USER_AGENT = "DreamTeamBot/1.0 (+discord; patch-notes monitor)"
_SSL_CTX = ssl.create_default_context(cafile=certifi.where())

ROLE_ORDER = ("Tank", "Damage", "Support")
ROLE_EMOJI = {"Tank": "🛡️", "Damage": "⚔️", "Support": "💚"}
SKIP_GENERIC = {
    "hero updates",
    "stadium updates",
    "bug fixes",
    "custom game updates",
    "hotfixes",
    "hotfix update",
}


@dataclass
class HeroChange:
    name: str
    role: str
    bullets: list[str] = field(default_factory=list)


@dataclass
class PatchSummary:
    patch_id: str
    date: str
    title: str
    events: list[str] = field(default_factory=list)
    heroes: list[HeroChange] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)  # short extras
    url: str = PATCH_URL

    @property
    def fingerprint(self) -> str:
        return self.patch_id or self.title


def _strip_tags(html: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", html, flags=re.I)
    text = re.sub(r"</p>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    return unescape(re.sub(r"\s+", " ", text)).strip()


def _first(pattern: str, text: str, flags: int = 0) -> str:
    m = re.search(pattern, text, flags)
    return m.group(1).strip() if m else ""


def _shorten_bullet(text: str, limit: int = 110) -> str:
    text = text.strip().rstrip(".")
    # Prefer arrow form for common phrasing
    text = re.sub(
        r"\breduced from ([^ ]+) to ([^ .]+)",
        r"\1 → \2",
        text,
        flags=re.I,
    )
    text = re.sub(
        r"\bincreased from ([^ ]+) to ([^ .]+)",
        r"\1 → \2",
        text,
        flags=re.I,
    )
    text = re.sub(
        r"\breduced (\d+%?) to (\d+%?)",
        r"\1 → \2",
        text,
        flags=re.I,
    )
    text = re.sub(r"\breduced to ([^ .]+) \(Down from ([^)]+)\)", r"\2 → \1", text, flags=re.I)
    text = re.sub(r"\bincreased to ([^ .]+) \(Up from ([^)]+)\)", r"\2 → \1", text, flags=re.I)
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text


def _tone(bullet: str) -> str:
    low = bullet.lower()
    buff = any(
        w in low
        for w in ("increased", "up from", "buff", "now grants", "heal increased", "damage increased")
    )
    nerf = any(
        w in low
        for w in ("reduced", "down from", "nerf", "decreased", "removed")
    )
    if buff and not nerf:
        return "▲"
    if nerf and not buff:
        return "▼"
    return "•"


def parse_latest_patch(html: str) -> PatchSummary | None:
    m = re.search(
        r'<div class="PatchNotes-patch PatchNotes-live">(.*?)<div class="PatchNotes-patch ',
        html,
        re.S,
    )
    if not m:
        # last/only patch on page
        m = re.search(
            r'<div class="PatchNotes-patch PatchNotes-live">(.*)$',
            html,
            re.S,
        )
    if not m:
        return None

    block = m.group(1)
    patch_id = _first(r'id="(patch-[^"]+)"', block) or _first(
        r'PatchNotes-date">([^<]+)', block
    )
    date = _first(r'PatchNotes-date">([^<]+)', block)
    title = _strip_tags(_first(r'PatchNotes-patchTitle"[^>]*>(.*?)</h3>', block, re.S))
    if not title:
        title = f"Overwatch Patch Notes – {date}" if date else "Overwatch Patch Notes"

    summary = PatchSummary(patch_id=patch_id or title, date=date, title=title)

    # Split into sections
    sections = re.split(r'<div class="PatchNotes-section ', block)[1:]
    mode = "retail"  # until Stadium / Bug Fixes
    current_role = ""

    for sec in sections:
        sec_title = _strip_tags(_first(r'PatchNotes-sectionTitle"[^>]*>(.*?)</h4>', sec, re.S))
        low = sec_title.lower()

        if low in ("stadium updates", "bug fixes", "custom game updates"):
            if low == "stadium updates":
                summary.notes.append("Stadium powers/items also adjusted")
            mode = "skip"
            continue

        if mode == "skip":
            continue

        is_hero_section = "PatchNotes-section-hero_update" in sec[:80] or sec.startswith(
            "PatchNotes-section-hero_update"
        )
        # After split, class remains at start: `PatchNotes-section-hero_update">...`
        if sec.startswith("PatchNotes-section-hero_update") or "hero_update" in sec[:60]:
            is_hero_section = True

        if sec_title in ROLE_ORDER:
            current_role = sec_title

        if is_hero_section or sec_title in ROLE_ORDER:
            heroes = re.finditer(
                r'<div class="PatchNotesHeroUpdate">'
                r'.*?<h5 class="PatchNotesHeroUpdate-name">([^<]+)</h5>'
                r'(.*?)</div>\s*</div>\s*(?=<div class="PatchNotesHeroUpdate">|<div class="PatchNotes-section|$)',
                sec,
                re.S,
            )
            for hm in heroes:
                name = hm.group(1).strip()
                body = hm.group(2)
                bullets: list[str] = []
                for ability in re.finditer(
                    r'PatchNotesAbilityUpdate-name">([^<]+)</div>'
                    r'.*?PatchNotesAbilityUpdate-detailList">(.*?)</div>',
                    body,
                    re.S,
                ):
                    ability_name = ability.group(1).strip()
                    # Drop long perk suffixes for space
                    ability_name = re.sub(
                        r"\s*[–—-]\s*(Major|Minor)\s+Perk\s*$",
                        "",
                        ability_name,
                        flags=re.I,
                    )
                    lis = re.findall(r"<li>(.*?)</li>", ability.group(2), re.S)
                    for li in lis:
                        tip = _shorten_bullet(_strip_tags(li))
                        if tip:
                            bullets.append(f"{ability_name}: {tip}")
                        if len(bullets) >= 3:
                            break
                    if len(bullets) >= 3:
                        break
                if not bullets:
                    for li in re.findall(r"<li>(.*?)</li>", body, re.S):
                        tip = _shorten_bullet(_strip_tags(li))
                        if tip:
                            bullets.append(tip)
                        if len(bullets) >= 3:
                            break
                if bullets:
                    summary.heroes.append(
                        HeroChange(
                            name=name,
                            role=current_role or "Hero",
                            bullets=bullets[:3],
                        )
                    )
            continue

        # Generic events — titles only
        if low and low not in SKIP_GENERIC and sec_title not in ROLE_ORDER:
            if "generic_update" in sec[:120]:
                summary.events.append(sec_title)

    # Deduplicate heroes keeping first (retail) occurrence
    seen: set[str] = set()
    unique: list[HeroChange] = []
    for h in summary.heroes:
        key = f"{h.role}:{h.name}"
        if key in seen:
            continue
        seen.add(key)
        unique.append(h)
    summary.heroes = unique
    return summary


async def fetch_patch_html(session: aiohttp.ClientSession | None = None) -> str:
    close = False
    if session is None:
        session = aiohttp.ClientSession(
            headers={"User-Agent": USER_AGENT, "Accept": "text/html"}
        )
        close = True
    try:
        async with session.get(
            PATCH_URL,
            timeout=aiohttp.ClientTimeout(total=45),
            ssl=_SSL_CTX,
        ) as resp:
            resp.raise_for_status()
            return await resp.text()
    finally:
        if close:
            await session.close()


async def fetch_latest_summary() -> PatchSummary | None:
    html = await fetch_patch_html()
    return parse_latest_patch(html)


def build_patch_embeds(summary: PatchSummary, *, preview: bool = False) -> list[discord.Embed]:
    head = discord.Embed(
        title=summary.date or "New Overwatch patch",
        description=(
            f"**{summary.title}**\n"
            + ("🧪 **Preview** — not a live announce\n" if preview else "")
            + f"[Official patch notes]({summary.url})"
        ),
        color=OW_ORANGE if not preview else OW_BLUE,
        url=summary.url,
    )
    head.set_author(name="Overwatch · Patch brief")
    if summary.events:
        head.add_field(
            name="Also live",
            value=" · ".join(f"**{e}**" for e in summary.events[:4]),
            inline=False,
        )
    if summary.notes:
        head.add_field(name="Note", value=" · ".join(summary.notes), inline=False)
    if not summary.heroes:
        head.add_field(
            name="Hero balance",
            value="_No retail hero tweaks in this drop (hotfix / events only)._",
            inline=False,
        )
    head.set_footer(text="Dream Team · checked daily · heroes only")

    embeds: list[discord.Embed] = [head]
    if not summary.heroes:
        return embeds

    by_role: dict[str, list[HeroChange]] = {r: [] for r in ROLE_ORDER}
    other: list[HeroChange] = []
    for h in summary.heroes:
        if h.role in by_role:
            by_role[h.role].append(h)
        else:
            other.append(h)

    for role in ROLE_ORDER:
        heroes = by_role[role]
        if not heroes:
            continue
        lines: list[str] = []
        for h in heroes:
            tips = "\n".join(f"  {_tone(b)} {b}" for b in h.bullets)
            lines.append(f"**{h.name}**\n{tips}")
        chunk = "\n\n".join(lines)
        # Split if too long for one field
        while chunk:
            part, chunk = chunk[:1000], chunk[1000:]
            if chunk:
                # break on hero boundary if possible
                cut = part.rfind("\n\n**")
                if cut > 400:
                    chunk = part[cut + 2 :] + chunk
                    part = part[:cut]
            emb = discord.Embed(color=OW_ORANGE if not preview else OW_BLUE)
            emb.add_field(
                name=f"{ROLE_EMOJI.get(role, '•')} {role}",
                value=part,
                inline=False,
            )
            embeds.append(emb)

    # Discord allows 10 embeds per message — keep first 8 role chunks + head
    if len(embeds) > 8:
        embeds = embeds[:8]
        embeds[-1].set_footer(text="…truncated — see full notes on Battle.net")
    return embeds


class OverwatchPatchCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._session: aiohttp.ClientSession | None = None
        self.check_patches.start()

    def cog_unload(self) -> None:
        self.check_patches.cancel()
        if self._session and not self._session.closed:
            self.bot.loop.create_task(self._session.close())

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={"User-Agent": USER_AGENT, "Accept": "text/html"}
            )
        return self._session

    async def get_summary(self) -> PatchSummary | None:
        session = await self._get_session()
        html = await fetch_patch_html(session)
        return parse_latest_patch(html)

    async def post_to_channel(
        self,
        channel: discord.TextChannel,
        summary: PatchSummary,
        *,
        preview: bool = False,
    ) -> discord.Message:
        embeds = build_patch_embeds(summary, preview=preview)
        return await channel.send(embeds=embeds)

    async def announce_if_new(self, guild: discord.Guild) -> tuple[bool, str]:
        channel_id = self.bot.db.get_ow_patch_channel(guild.id)
        if not channel_id:
            return False, "No Overwatch patch channel set."
        channel = guild.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            return False, "Patch channel missing."

        try:
            summary = await self.get_summary()
        except Exception as exc:
            log.warning("OW patch fetch failed: %s", exc)
            return False, f"Fetch failed: {exc}"

        if summary is None:
            return False, "Could not parse patch notes page."

        if self.bot.db.was_ow_patch_announced(guild.id, summary.fingerprint):
            return False, f"Already posted `{summary.fingerprint}`."

        await self.post_to_channel(channel, summary, preview=False)
        self.bot.db.mark_ow_patch_announced(guild.id, summary.fingerprint)
        return True, summary.title

    @tasks.loop(hours=config.OW_PATCH_CHECK_HOURS)
    async def check_patches(self) -> None:
        for guild in self.bot.guilds:
            try:
                posted, detail = await self.announce_if_new(guild)
                if posted:
                    log.info("OW patch posted in %s: %s", guild.name, detail)
            except Exception as exc:
                log.warning("OW patch check failed for %s: %s", guild.name, exc)

    @check_patches.before_loop
    async def before_check(self) -> None:
        await self.bot.wait_until_ready()
