"""Overwatch patch notes monitor — daily fetch, concise hero cards, styled embeds."""

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
ROLE_COLOR = {
    "Tank": discord.Color.from_rgb(242, 166, 50),
    "Damage": discord.Color.from_rgb(232, 84, 84),
    "Support": discord.Color.from_rgb(45, 190, 140),
}
ROLE_LABEL = {
    "Tank": "🛡️  TANK",
    "Damage": "⚔️  DAMAGE",
    "Support": "💚  SUPPORT",
}


@dataclass
class ChangeLine:
    """One balance tweak, optionally mode-specific."""

    ability: str
    text: str
    mode: str | None = None  # "5v5" | "6v6" | None
    tone: str = "•"  # ▲ ▼ •


@dataclass
class HeroChange:
    name: str
    role: str
    icon_url: str | None = None
    changes: list[ChangeLine] = field(default_factory=list)


@dataclass
class PatchSummary:
    patch_id: str
    date: str
    title: str
    heroes: list[HeroChange] = field(default_factory=list)
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


def _tone_from(text: str) -> str:
    low = text.lower()
    buff = any(
        w in low
        for w in (
            "increased",
            "up from",
            "buff",
            "now grants",
            "added",
            "restores",
        )
    )
    nerf = any(
        w in low
        for w in ("reduced", "down from", "nerf", "decreased", "removed", "lower")
    )
    if buff and not nerf:
        return "▲"
    if nerf and not buff:
        return "▼"
    return "•"


def _compact_value(raw: str) -> tuple[str, str, str | None]:
    """Turn verbose Blizzard phrasing into a short readable line."""
    text = raw.strip().rstrip(".")
    mode = None
    m = re.search(r"\((5v5|6v6)\)\s*$", text, re.I)
    if m:
        mode = m.group(1).lower()
        text = text[: m.start()].strip().rstrip(".")

    def _sec(v: str) -> str:
        return re.sub(r"\s*seconds?$", "s", v.strip(), flags=re.I)

    # Cooldown reduced from 12 to 10 seconds
    m = re.search(
        r"^(?P<label>.+?)\s+(?:reduced|increased)\s+from\s+(?P<a>.+?)\s+to\s+(?P<b>.+)$",
        text,
        re.I,
    )
    if m:
        label = m.group("label").strip()
        a, b = m.group("a").strip(), m.group("b").strip()
        if re.search(r"seconds?$", b, re.I) or re.search(r"seconds?$", a, re.I):
            a = re.sub(r"\s*seconds?$", "", a, flags=re.I) + "s"
            b = re.sub(r"\s*seconds?$", "", b, flags=re.I) + "s"
        else:
            a, b = _sec(a), _sec(b)
        return label, f"{a} → {b}", mode

    m = re.search(
        r"^(?P<label>.+?)\s+reduced\s+(?P<a>\d+%?)\s+to\s+(?P<b>\d+%?)",
        text,
        re.I,
    )
    if m:
        return m.group("label").strip(), f"{m.group('a')} → {m.group('b')}", mode

    m = re.search(
        r"reduced to (?P<b>.+?) \(Down from (?P<a>.+?)\)",
        text,
        re.I,
    )
    if m:
        label = re.split(r"\s+reduced\s+to\s+", text, maxsplit=1, flags=re.I)[0].strip()
        return label or "Value", f"{_sec(m.group('a'))} → {_sec(m.group('b'))}", mode

    m = re.search(
        r"increased to (?P<b>.+?) \(Up from (?P<a>.+?)\)",
        text,
        re.I,
    )
    if m:
        label = re.split(r"\s+increased\s+to\s+", text, maxsplit=1, flags=re.I)[0].strip()
        return label or "Value", f"{_sec(m.group('a'))} → {_sec(m.group('b'))}", mode

    if len(text) > 90:
        text = text[:89].rstrip() + "…"
    return "", text, mode


def _make_change(ability: str, raw_li: str) -> ChangeLine | None:
    clean = _strip_tags(raw_li)
    if not clean:
        return None
    label, value, mode = _compact_value(clean)
    tone = _tone_from(clean)
    if label and "→" in value:
        text = f"**{label}** `{value}`"
    elif "→" in value:
        text = f"`{value}`"
    else:
        text = value
    # Avoid repeating ability name inside the line
    return ChangeLine(ability=ability, text=text, mode=mode, tone=tone)


def _format_hero_body(hero: HeroChange) -> str:
    """Group by ability; put 5v5 / 6v6 on their own lines."""
    by_ability: dict[str, list[ChangeLine]] = {}
    for ch in hero.changes:
        by_ability.setdefault(ch.ability, []).append(ch)

    blocks: list[str] = []
    for ability, lines in by_ability.items():
        shared = [c for c in lines if not c.mode]
        v5 = [c for c in lines if c.mode == "5v5"]
        v6 = [c for c in lines if c.mode == "6v6"]

        parts = [f"**{ability}**"]
        for c in shared:
            parts.append(f"{c.tone} {c.text}")

        if v5 or v6:
            # Pair modes for easy scanning
            if v5 and v6 and len(v5) == len(v6):
                for a, b in zip(v5, v6):
                    # Strip mode from already-compact text; show badges
                    parts.append(f"┣ **5v5**  {a.tone} {a.text}")
                    parts.append(f"┗ **6v6**  {b.tone} {b.text}")
            else:
                for c in v5:
                    parts.append(f"┣ **5v5**  {c.tone} {c.text}")
                for c in v6:
                    parts.append(f"┗ **6v6**  {c.tone} {c.text}")

        blocks.append("\n".join(parts))

    body = "\n\n".join(blocks)
    if len(body) > 3900:
        body = body[:3899].rstrip() + "…"
    return body


def parse_latest_patch(html: str) -> PatchSummary | None:
    m = re.search(
        r'<div class="PatchNotes-patch PatchNotes-live">(.*?)<div class="PatchNotes-patch ',
        html,
        re.S,
    )
    if not m:
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

    sections = re.split(r'<div class="PatchNotes-section ', block)[1:]
    mode = "retail"
    current_role = ""

    for sec in sections:
        sec_title = _strip_tags(_first(r'PatchNotes-sectionTitle"[^>]*>(.*?)</h4>', sec, re.S))
        low = sec_title.lower()

        if low in ("stadium updates", "bug fixes", "custom game updates"):
            mode = "skip"
            continue
        if mode == "skip":
            continue

        is_hero_section = sec.startswith("PatchNotes-section-hero_update") or (
            "hero_update" in sec[:60]
        )
        if sec_title in ROLE_ORDER:
            current_role = sec_title

        if not (is_hero_section or sec_title in ROLE_ORDER):
            continue

        heroes = re.finditer(
            r'<div class="PatchNotesHeroUpdate">'
            r'.*?<img class="PatchNotesHeroUpdate-icon" src="([^"]+)"[^>]*>'
            r'.*?<h5 class="PatchNotesHeroUpdate-name">([^<]+)</h5>'
            r"(.*?)</div>\s*</div>\s*(?=<div class=\"PatchNotesHeroUpdate\">|<div class=\"PatchNotes-section|$)",
            sec,
            re.S,
        )
        for hm in heroes:
            icon_url = hm.group(1).strip()
            name = hm.group(2).strip()
            body = hm.group(3)
            changes: list[ChangeLine] = []

            for ability in re.finditer(
                r'PatchNotesAbilityUpdate-name">([^<]+)</div>'
                r'.*?PatchNotesAbilityUpdate-detailList">(.*?)</div>',
                body,
                re.S,
            ):
                ability_name = ability.group(1).strip()
                ability_name = re.sub(
                    r"\s*[–—-]\s*(Major|Minor)\s+Perk\s*$",
                    "",
                    ability_name,
                    flags=re.I,
                )
                for li in re.findall(r"<li>(.*?)</li>", ability.group(2), re.S):
                    ch = _make_change(ability_name, li)
                    if ch:
                        changes.append(ch)
                    if len(changes) >= 6:
                        break
                if len(changes) >= 6:
                    break

            if not changes:
                for li in re.findall(r"<li>(.*?)</li>", body, re.S)[:4]:
                    ch = _make_change("General", li)
                    if ch:
                        changes.append(ch)

            if changes:
                summary.heroes.append(
                    HeroChange(
                        name=name,
                        role=current_role or "Hero",
                        icon_url=icon_url or None,
                        changes=changes,
                    )
                )

    # Retail only (first occurrence per hero name in role)
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
    """Header + one embed per role (Tank / Damage / Support) — single Discord message."""
    n = len(summary.heroes)
    color = OW_BLUE if preview else OW_ORANGE
    head = discord.Embed(
        title=summary.date or "Overwatch patch",
        description=(
            ("🧪 **Preview** — not a live announce\n\n" if preview else "")
            + (f"**{n} heroes** balanced\n" if n != 1 else "**1 hero** balanced\n")
            + f"[Full official notes]({summary.url})"
        ),
        color=color,
        url=summary.url,
    )
    head.set_author(name="Overwatch  ·  Balance brief")
    if not summary.heroes:
        head.description = (
            ("🧪 **Preview**\n\n" if preview else "")
            + "_No retail hero balance in this drop._\n"
            + f"[Official notes]({summary.url})"
        )
        return [head]

    by_role: dict[str, list[HeroChange]] = {r: [] for r in ROLE_ORDER}
    for h in summary.heroes:
        by_role.setdefault(h.role, []).append(h)

    embeds: list[discord.Embed] = [head]
    for role in ROLE_ORDER:
        heroes = by_role.get(role, [])
        if not heroes:
            continue

        emb = discord.Embed(color=ROLE_COLOR.get(role, color))
        emb.set_author(name=f"{ROLE_LABEL.get(role, role)}  ·  {len(heroes)}")
        if heroes[0].icon_url:
            emb.set_thumbnail(url=heroes[0].icon_url)

        chunks: list[str] = []
        for hero in heroes:
            body = _format_hero_body(hero)
            # Portrait link on the hero title (opens official icon)
            if hero.icon_url:
                title = f"**[{hero.name}]({hero.icon_url})**"
            else:
                title = f"**{hero.name}**"
            chunks.append(f"{title}\n{body}")

        # Fit into description (4096) or spill into fields
        joined = "\n\n﹒﹒﹒\n\n".join(chunks)
        if len(joined) <= 4000:
            emb.description = joined
        else:
            for hero in heroes:
                body = _format_hero_body(hero)
                if len(body) > 1024:
                    body = body[:1021].rstrip() + "…"
                emb.add_field(name=hero.name, value=body, inline=False)

        embeds.append(emb)

    return embeds


def _chunk_embeds(embeds: list[discord.Embed], size: int = 10) -> list[list[discord.Embed]]:
    return [embeds[i : i + size] for i in range(0, len(embeds), size)]


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
        first: discord.Message | None = None
        for batch in _chunk_embeds(embeds, 10):
            msg = await channel.send(embeds=batch)
            if first is None:
                first = msg
        assert first is not None
        return first

    async def send_preview_ephemeral(self, interaction: discord.Interaction) -> None:
        """Ephemeral preview (may use multiple followups for many heroes)."""
        summary = await self.get_summary()
        if summary is None:
            await interaction.followup.send(
                "Could not parse the patch notes page.", ephemeral=True
            )
            return
        embeds = build_patch_embeds(summary, preview=True)
        batches = _chunk_embeds(embeds, 10)
        await interaction.followup.send(embeds=batches[0], ephemeral=True)
        for batch in batches[1:]:
            await interaction.followup.send(embeds=batch, ephemeral=True)

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
