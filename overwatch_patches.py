"""Overwatch patch notes monitor — daily fetch, concise hero cards, styled embeds."""

from __future__ import annotations

import json
import logging
import re
import ssl
import time
from dataclasses import dataclass, field
from html import unescape

import aiohttp
import certifi
import discord
from discord.ext import commands, tasks

import config
from ow_forum import (
    OW_PATCH_TAG_NAMES,
    is_ow_destination,
    patch_thread_title,
    post_ow_announcement,
)

log = logging.getLogger("dream_team.overwatch")

PATCH_URL = config.OW_PATCH_URL
# Blizzard loads older months via XHR:
#   /{locale}/news/patch-body/live/{year}/{month}
OW_ORANGE = discord.Color.from_rgb(249, 158, 26)
OW_BLUE = discord.Color.from_rgb(33, 143, 254)
USER_AGENT = "DreamTeamBot/1.0 (+discord; patch-notes monitor)"
_SSL_CTX = ssl.create_default_context(cafile=certifi.where())
OW_HISTORY_BUTTON_ID = "ow_patch:history"

ROLE_ORDER = ("Tank", "Damage", "Support")
ROLE_COLOR = {
    "Tank": discord.Color.from_rgb(242, 166, 50),
    "Damage": discord.Color.from_rgb(232, 84, 84),
    "Support": discord.Color.from_rgb(45, 190, 140),
}
ROLE_LABEL = {
    "Tank": "TANK",
    "Damage": "DAMAGE",
    "Support": "SUPPORT",
}
ROLE_HEADER = {
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
    icon_url: str | None = None  # ability / utility icon from Blizzard


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


# In-memory cache so Dream doesn't re-download months on every question
_PATCH_CACHE: list[PatchSummary] | None = None
_PATCH_CACHE_AT = 0.0
_PATCH_CACHE_TTL = 600.0  # 10 minutes
_PATCH_CACHE_MONTHS = 0


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


def _short_label(label: str, limit: int = 34) -> str:
    """Trim long Blizzard labels at a word boundary."""
    label = label.strip()
    if len(label) <= limit:
        return label
    cut = label[:limit].rsplit(" ", 1)[0].rstrip(".,;:-")
    return (cut or label[: limit - 1]) + "…"


def _make_change(
    ability: str, raw_li: str, *, icon_url: str | None = None
) -> ChangeLine | None:
    clean = _strip_tags(raw_li)
    if not clean:
        return None
    label, value, mode = _compact_value(clean)
    tone = _tone_from(clean)
    # Drop ability name if Blizzard repeats it in the label
    if label and ability and label.lower().startswith(ability.lower()):
        trimmed = label[len(ability) :].lstrip(" :-–—")
        if trimmed:
            label = trimmed
    if label:
        label = _short_label(label)
    if label and "→" in value:
        text = f"{label} `{value}`"
    elif "→" in value:
        text = f"`{value}`"
    else:
        text = value
    return ChangeLine(
        ability=ability, text=text, mode=mode, tone=tone, icon_url=icon_url
    )



def _format_ability_block(ability: str, lines: list[ChangeLine]) -> str:
    """Ability name, then each buff/nerf on its own indented line."""
    shared = [c for c in lines if not c.mode]
    v5 = [c for c in lines if c.mode == "5v5"]
    v6 = [c for c in lines if c.mode == "6v6"]

    rows: list[str] = [ability]
    for c in shared:
        rows.append(f"{c.tone}  {c.text}")

    if v5 or v6:
        if v5 and v6 and len(v5) == len(v6):
            for a, b in zip(v5, v6):
                rows.append(f"{a.tone}  5v5  {a.text}")
                rows.append(f"{b.tone}  6v6  {b.text}")
        else:
            for c in v5:
                rows.append(f"{c.tone}  5v5  {c.text}")
            for c in v6:
                rows.append(f"{c.tone}  6v6  {c.text}")

    return "\n".join(rows)


def _hero_changes_text(hero: HeroChange) -> str:
    """Ability blocks with a blank line between them for breathing room."""
    by_ability: dict[str, list[ChangeLine]] = {}
    for ch in hero.changes:
        by_ability.setdefault(ch.ability, []).append(ch)
    return "\n\n".join(
        _format_ability_block(ability, lines)
        for ability, lines in by_ability.items()
    )


def _hero_card_text(hero: HeroChange) -> str:
    """Single card block: bold name + plain compact changes."""
    changes = _hero_changes_text(hero)
    return f"**{hero.name}**\n{changes}" if changes else f"**{hero.name}**"


def _hero_section_text(hero: HeroChange) -> str:
    """Fallback embeds use the same card text."""
    return _hero_card_text(hero)



def _parse_patch_block(block: str) -> PatchSummary | None:
    """Parse one PatchNotes-patch HTML block into a summary."""
    patch_id = _first(r'id="(patch-[^"]+)"', block) or _first(
        r'PatchNotes-date">([^<]+)', block
    )
    date = _first(r'PatchNotes-date">([^<]+)', block)
    title = _strip_tags(_first(r'PatchNotes-patchTitle"[^>]*>(.*?)</h3>', block, re.S))
    if not title:
        title = f"Overwatch Patch Notes – {date}" if date else "Overwatch Patch Notes"
    if not date and not patch_id:
        return None

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

            for ab_html in re.split(r'<div class="PatchNotesAbilityUpdate">', body)[1:]:
                ability_icon = _first(
                    r'PatchNotesAbilityUpdate-icon" src="([^"]+)"', ab_html
                ) or None
                ability_name = _first(
                    r'PatchNotesAbilityUpdate-name">([^<]+)', ab_html
                )
                if not ability_name:
                    continue
                ability_name = re.sub(
                    r"\s*[–—-]\s*(Major|Minor)\s+Perk\s*$",
                    "",
                    ability_name,
                    flags=re.I,
                )
                detail = _first(
                    r'PatchNotesAbilityUpdate-detailList">(.*?)</div>', ab_html, re.S
                )
                for li in re.findall(r"<li>(.*?)</li>", detail or "", re.S):
                    ch = _make_change(ability_name, li, icon_url=ability_icon)
                    if ch:
                        changes.append(ch)
                    if len(changes) >= 8:
                        break
                if len(changes) >= 8:
                    break

            if not changes:
                for li in re.findall(r"<li>(.*?)</li>", body, re.S)[:6]:
                    ch = _make_change("General", li, icon_url=None)
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
    return _parse_patch_block(m.group(1))


def parse_all_patches(html: str, *, limit: int = 15) -> list[PatchSummary]:
    """
    Parse every patch block on the Blizzard patch-notes page (newest first).
    Used by Dream AI to find the last time a hero was changed.
    """
    # Split on patch containers; keep class so we know which is live
    parts = re.split(r'(?=<div class="PatchNotes-patch)', html)
    out: list[PatchSummary] = []
    for part in parts:
        if 'class="PatchNotes-patch' not in part[:80]:
            continue
        # Trim trailing next-patch start if present — block is the part itself
        block = part
        summary = _parse_patch_block(block)
        if summary is None:
            continue
        if not summary.date and not summary.heroes:
            continue
        out.append(summary)
        if len(out) >= limit:
            break
    return out


def parse_available_months(html: str, *, patch_type: str = "live") -> list[tuple[int, int]]:
    """
    Read Blizzard's patchNotesDates calendar from the patch-notes page.
    Returns (year, month) newest-first, e.g. [(2026, 7), (2026, 6), ...].
    """
    m = re.search(r"patchNotesDates\s*=\s*(\{.*?\});", html, re.S)
    if not m:
        return []
    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError:
        return []
    raw = data.get(patch_type) or data.get("live") or []
    out: list[tuple[int, int]] = []
    for item in raw:
        # "2026-07"
        mm = re.match(r"^(\d{4})-(\d{1,2})$", str(item).strip())
        if not mm:
            continue
        out.append((int(mm.group(1)), int(mm.group(2))))
    return out


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


async def fetch_patch_body_html(
    year: int,
    month: int,
    *,
    session: aiohttp.ClientSession | None = None,
    patch_type: str = "live",
) -> str:
    """Fetch one calendar month of retail patch notes (XHR body Blizzard uses)."""
    close = False
    if session is None:
        session = aiohttp.ClientSession(
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "text/html",
                "X-Requested-With": "XMLHttpRequest",
            }
        )
        close = True
    url = (
        f"https://overwatch.blizzard.com/en-us/news/patch-body/"
        f"{patch_type}/{year}/{month:02d}"
    )
    try:
        async with session.get(
            url,
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


def _hero_name_in_patches(patches: list[PatchSummary], hero_query: str) -> bool:
    q = (hero_query or "").lower().strip()
    if not q:
        return False
    compact_q = q.replace(" ", "")
    for summary in patches:
        for h in summary.heroes:
            name = (h.name or "").lower()
            compact = name.replace(" ", "")
            if name == q or name.startswith(q) or compact_q in compact or q in compact:
                return True
    return False


async def fetch_all_patch_summaries(
    *,
    limit: int = 15,
    max_months: int = 8,
    stop_hero: str | None = None,
) -> list[PatchSummary]:
    """
    Walk Blizzard's patch-notes calendar (newest month → older) via the same
    patch-body endpoint the website uses when you change month/year.
    Returns individual patches newest-first, capped by `limit`.

    `stop_hero`: stop downloading older months once this hero appears (faster
    Dream answers). Results are cached ~10 minutes.
    """
    global _PATCH_CACHE, _PATCH_CACHE_AT, _PATCH_CACHE_MONTHS

    now = time.monotonic()
    if (
        _PATCH_CACHE
        and now - _PATCH_CACHE_AT < _PATCH_CACHE_TTL
        and _PATCH_CACHE_MONTHS >= max_months
    ):
        cached = _PATCH_CACHE[:limit]
        if not stop_hero or _hero_name_in_patches(cached, stop_hero):
            return cached
        # Hero not in cache — fall through and fetch more months below

    html = await fetch_patch_html()
    months = parse_available_months(html)
    if not months:
        patches = parse_all_patches(html, limit=limit)
        if patches:
            _PATCH_CACHE, _PATCH_CACHE_AT, _PATCH_CACHE_MONTHS = patches, now, 1
            return patches[:limit]
        latest = parse_latest_patch(html)
        out = [latest] if latest else []
        _PATCH_CACHE, _PATCH_CACHE_AT, _PATCH_CACHE_MONTHS = out, now, 1
        return out

    # Reuse any still-fresh partial cache as a starting point
    out: list[PatchSummary] = []
    seen: set[str] = set()
    if _PATCH_CACHE and now - _PATCH_CACHE_AT < _PATCH_CACHE_TTL:
        for summary in _PATCH_CACHE:
            key = summary.fingerprint or f"{summary.date}:{summary.title}"
            if key in seen:
                continue
            seen.add(key)
            out.append(summary)

    start_month_idx = 0
    if out and _PATCH_CACHE_MONTHS > 0:
        start_month_idx = min(_PATCH_CACHE_MONTHS, len(months))

    session = aiohttp.ClientSession(
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html",
            "X-Requested-With": "XMLHttpRequest",
        }
    )
    months_fetched = start_month_idx
    try:
        for year, month in months[start_month_idx:max_months]:
            try:
                body = await fetch_patch_body_html(
                    year, month, session=session
                )
            except Exception as exc:
                log.warning(
                    "Failed to fetch patch body %s-%02d: %s", year, month, exc
                )
                months_fetched += 1
                continue
            months_fetched += 1
            for summary in parse_all_patches(body, limit=limit):
                key = summary.fingerprint or f"{summary.date}:{summary.title}"
                if key in seen:
                    continue
                seen.add(key)
                out.append(summary)
            if stop_hero and _hero_name_in_patches(out, stop_hero):
                break
            if len(out) >= limit and not stop_hero:
                break
    finally:
        await session.close()

    if out:
        _PATCH_CACHE = out
        _PATCH_CACHE_AT = time.monotonic()
        _PATCH_CACHE_MONTHS = max(months_fetched, _PATCH_CACHE_MONTHS)
        return out[:limit] if not stop_hero else out

    patches = parse_all_patches(html, limit=limit)
    if patches:
        _PATCH_CACHE, _PATCH_CACHE_AT, _PATCH_CACHE_MONTHS = patches, now, 1
        return patches
    latest = parse_latest_patch(html)
    out = [latest] if latest else []
    _PATCH_CACHE, _PATCH_CACHE_AT, _PATCH_CACHE_MONTHS = out, now, 1
    return out


def summary_to_payload(summary: PatchSummary) -> str:
    return json.dumps(
        {
            "patch_id": summary.patch_id,
            "date": summary.date,
            "title": summary.title,
            "url": summary.url,
            "heroes": [
                {
                    "name": h.name,
                    "role": h.role,
                    "icon_url": h.icon_url,
                    "changes": [
                        {
                            "ability": c.ability,
                            "text": c.text,
                            "mode": c.mode,
                            "tone": c.tone,
                        }
                        for c in h.changes
                    ],
                }
                for h in summary.heroes
            ],
        }
    )


def summary_from_payload(raw: str) -> PatchSummary | None:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    heroes: list[HeroChange] = []
    for h in data.get("heroes") or []:
        changes = [
            ChangeLine(
                ability=c.get("ability") or "General",
                text=c.get("text") or "",
                mode=c.get("mode"),
                tone=c.get("tone") or "•",
            )
            for c in (h.get("changes") or [])
        ]
        heroes.append(
            HeroChange(
                name=h.get("name") or "Hero",
                role=h.get("role") or "Damage",
                icon_url=h.get("icon_url"),
                changes=changes,
            )
        )
    return PatchSummary(
        patch_id=data.get("patch_id") or data.get("title") or "patch",
        date=data.get("date") or "",
        title=data.get("title") or "Overwatch Patch Notes",
        heroes=heroes,
        url=data.get("url") or PATCH_URL,
    )


async def send_summary_ephemeral(
    interaction: discord.Interaction,
    summary: PatchSummary,
    *,
    archive: bool = False,
) -> None:
    try:
        layouts = build_patch_layouts(summary, preview=False, archive=archive)
        await interaction.followup.send(view=layouts[0], ephemeral=True)
        for layout in layouts[1:]:
            await interaction.followup.send(view=layout, ephemeral=True)
    except Exception as exc:
        log.warning("OW ephemeral layout failed: %s", exc)
        embeds = build_patch_embeds(summary, preview=archive)
        if archive:
            embeds[0].title = f"Archive · {summary.date or summary.title}"
        await interaction.followup.send(embeds=embeds, ephemeral=True)


class OwArchiveSelect(discord.ui.Select):
    def __init__(self, options: list[discord.SelectOption]) -> None:
        super().__init__(
            placeholder="Choose a previous patch…",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(
                "This only works in a server.", ephemeral=True
            )
            return
        patch_id = self.values[0]
        raw = interaction.client.db.get_ow_patch_payload(guild.id, patch_id)
        summary = summary_from_payload(raw) if raw else None
        if summary is None:
            await interaction.response.send_message(
                "That archived patch could not be loaded.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        await send_summary_ephemeral(interaction, summary, archive=True)


class OwArchiveSelectView(discord.ui.View):
    def __init__(self, options: list[discord.SelectOption]) -> None:
        super().__init__(timeout=120)
        self.add_item(OwArchiveSelect(options))


class OwPatchHistoryView(discord.ui.View):
    """Persistent button under the live patch post — opens earlier notes privately."""

    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Previous patches",
        style=discord.ButtonStyle.secondary,
        custom_id=OW_HISTORY_BUTTON_ID,
    )
    async def previous_patches(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(
                "This only works in a server.", ephemeral=True
            )
            return

        rows = interaction.client.db.list_ow_patch_history(guild.id)
        if not rows:
            await interaction.response.send_message(
                "No previous patches saved yet.", ephemeral=True
            )
            return

        if len(rows) == 1:
            summary = summary_from_payload(rows[0]["payload"])
            if summary is None:
                await interaction.response.send_message(
                    "Could not load the archived patch.", ephemeral=True
                )
                return
            await interaction.response.defer(ephemeral=True)
            await send_summary_ephemeral(interaction, summary, archive=True)
            return

        options: list[discord.SelectOption] = []
        for row in rows[:25]:
            summary = summary_from_payload(row["payload"])
            label = (summary.date if summary and summary.date else row["patch_id"])[
                :100
            ]
            desc = (summary.title if summary else row["patch_id"])[:100]
            options.append(
                discord.SelectOption(
                    label=label,
                    value=row["patch_id"][:100],
                    description=desc,
                )
            )
        await interaction.response.send_message(
            "Pick a previous patch to view (only you will see it):",
            view=OwArchiveSelectView(options),
            ephemeral=True,
        )



def build_patch_layouts(
    summary: PatchSummary,
    *,
    preview: bool = False,
    archive: bool = False,
) -> list[discord.ui.LayoutView]:
    """
    One colour-accented container per role; each hero is a compact portrait card.
    Discord's 40-component cap may split a large patch into multiple messages.
    """
    by_role: dict[str, list[HeroChange]] = {r: [] for r in ROLE_ORDER}
    for h in summary.heroes:
        by_role.setdefault(h.role, []).append(h)

    date_label = summary.date or "Patch"
    if preview:
        header = f"🧪 **Preview** · **[{date_label}]({summary.url})**"
    elif archive:
        header = f"📜 **Archive** · **[{date_label}]({summary.url})**"
    else:
        header = f"**Overwatch** · **[{date_label}]({summary.url})**"

    if not summary.heroes:
        view = discord.ui.LayoutView(timeout=None)
        view.add_item(
            discord.ui.TextDisplay(header + "\n_No retail hero balance in this drop._")
        )
        return [view]

    BUDGET = 38
    # Section with 1 text child + thumbnail accessory counts as 3
    HERO_COST = 3
    views: list[discord.ui.LayoutView] = []
    view = discord.ui.LayoutView(timeout=None)
    view.add_item(discord.ui.TextDisplay(header))

    def flush() -> None:
        nonlocal view
        views.append(view)
        view = discord.ui.LayoutView(timeout=None)
        prefix = "📜 **Archive**" if archive else "**Overwatch**"
        view.add_item(
            discord.ui.TextDisplay(
                f"{prefix} · **[{date_label}]({summary.url})** · cont."
            )
        )

    for role in ROLE_ORDER:
        heroes = by_role.get(role, [])
        if not heroes:
            continue

        colour = ROLE_COLOR.get(role, OW_ORANGE)
        label = ROLE_HEADER.get(role, role)

        def open_role_container(*, continued: bool = False) -> discord.ui.Container:
            # Container(1) + title TextDisplay(1) + at least one hero(3)
            if view._total_children + 1 + 1 + HERO_COST > BUDGET:
                flush()
            title = f"**{label}**" + (" · cont." if continued else "")
            container = discord.ui.Container(accent_colour=colour)
            view.add_item(container)
            container.add_item(discord.ui.TextDisplay(title))
            return container

        container = open_role_container()

        for i, hero in enumerate(heroes):
            if view._total_children + HERO_COST > BUDGET:
                container = open_role_container(continued=True)

            card = _hero_card_text(hero)
            if hero.icon_url:
                container.add_item(
                    discord.ui.Section(
                        card,
                        accessory=discord.ui.Thumbnail(hero.icon_url),
                    )
                )
            else:
                # TextDisplay alone counts as 1; still fine under budget checks
                if view._total_children + 1 > BUDGET:
                    container = open_role_container(continued=True)
                container.add_item(discord.ui.TextDisplay(card))

            # Tight hairline between heroes inside the role card
            if i < len(heroes) - 1 and view._total_children + 1 + HERO_COST <= BUDGET:
                container.add_item(
                    discord.ui.Separator(
                        visible=True,
                        spacing=discord.SeparatorSpacing.small,
                    )
                )

    views.append(view)
    return views


def build_patch_embeds(summary: PatchSummary, *, preview: bool = False) -> list[discord.Embed]:
    """Fallback embeds if Components V2 layout is unavailable."""
    color = OW_BLUE if preview else OW_ORANGE
    head = discord.Embed(
        title=summary.date or "Overwatch patch",
        description="🧪 **Preview**" if preview else None,
        color=color,
        url=summary.url,
    )
    head.set_author(name="Overwatch")
    if not summary.heroes:
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
        emb.set_author(name=ROLE_HEADER.get(role, role))
        emb.description = "\n\n".join(_hero_section_text(h) for h in heroes)[:4096]
        embeds.append(emb)
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
        channel: discord.TextChannel | discord.ForumChannel,
        summary: PatchSummary,
        *,
        preview: bool = False,
        with_history: bool = False,
        existing_thread_id: int | None = None,
    ) -> tuple[list[discord.Message], int | None]:
        layouts = build_patch_layouts(summary, preview=preview)
        return await post_ow_announcement(
            channel,
            thread_name=patch_thread_title(date=summary.date, title=summary.title),
            layouts=layouts,
            embeds_fallback=lambda: build_patch_embeds(summary, preview=preview),
            tag_names=OW_PATCH_TAG_NAMES,
            trailing_content="Earlier balance notes:" if with_history else None,
            trailing_view=OwPatchHistoryView() if with_history else None,
            existing_thread_id=existing_thread_id,
        )

    async def delete_live_messages(
        self, channel: discord.TextChannel, guild_id: int
    ) -> None:
        """Only used for classic text channels — forum posts are edited in place."""
        for mid in self.bot.db.get_ow_live_message_ids(guild_id):
            try:
                msg = await channel.fetch_message(mid)
                await msg.delete()
            except discord.NotFound:
                pass
            except discord.HTTPException as exc:
                log.warning("Could not delete old OW patch message %s: %s", mid, exc)

    async def publish_live(
        self,
        channel: discord.TextChannel | discord.ForumChannel,
        summary: PatchSummary,
    ) -> list[discord.Message]:
        """Overwrite the live patch post (forum thread or text messages)."""
        guild_id = channel.guild.id
        existing_thread_id = None
        if isinstance(channel, discord.TextChannel):
            await self.delete_live_messages(channel, guild_id)
        else:
            existing_thread_id = self.bot.db.get_ow_patch_thread_id(guild_id)

        messages, thread_id = await self.post_to_channel(
            channel,
            summary,
            preview=False,
            with_history=True,
            existing_thread_id=existing_thread_id,
        )
        if thread_id is not None:
            self.bot.db.set_ow_patch_thread_id(guild_id, thread_id)

        self.bot.db.save_ow_patch_live(
            guild_id,
            summary.fingerprint,
            [m.id for m in messages],
            summary_to_payload(summary),
        )
        return messages

    async def send_preview_ephemeral(self, interaction: discord.Interaction) -> None:
        summary = await self.get_summary()
        if summary is None:
            await interaction.followup.send(
                "Could not parse the patch notes page.", ephemeral=True
            )
            return
        try:
            layouts = build_patch_layouts(summary, preview=True)
            await interaction.followup.send(view=layouts[0], ephemeral=True)
            for layout in layouts[1:]:
                await interaction.followup.send(view=layout, ephemeral=True)
        except Exception as exc:
            log.warning("OW layout preview failed: %s", exc)
            await interaction.followup.send(
                embeds=build_patch_embeds(summary, preview=True),
                ephemeral=True,
            )

    async def announce_if_new(self, guild: discord.Guild) -> tuple[bool, str]:
        channel_id = self.bot.db.get_ow_patch_channel(guild.id)
        if not channel_id:
            return False, "No Overwatch patch channel set."
        channel = guild.get_channel(channel_id)
        if not is_ow_destination(channel):
            return False, "Patch channel missing (set a forum or text channel)."

        try:
            summary = await self.get_summary()
        except Exception as exc:
            log.warning("OW patch fetch failed: %s", exc)
            return False, f"Fetch failed: {exc}"

        if summary is None:
            return False, "Could not parse patch notes page."

        if self.bot.db.was_ow_patch_announced(guild.id, summary.fingerprint):
            return False, f"Already posted `{summary.fingerprint}`."

        await self.publish_live(channel, summary)
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
