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
# Blizzard sometimes uses a single "Hero Updates" block instead of role sections.
_EXTRA_ROLE_ORDER = ("Hero Updates", "Hero", "Hotfix Update")
ROLE_COLOR = {
    "Tank": discord.Color.from_rgb(242, 166, 50),
    "Damage": discord.Color.from_rgb(232, 84, 84),
    "Support": discord.Color.from_rgb(45, 190, 140),
    "Hero Updates": OW_ORANGE,
    "Hero": OW_ORANGE,
    "Hotfix Update": OW_ORANGE,
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
    "Hero Updates": "✨  HERO UPDATES",
    "Hero": "✨  HERO UPDATES",
    "Hotfix Update": "🔧  HOTFIX",
}


def _roles_for_display(heroes: list[HeroChange]) -> list[str]:
    """Classic Tank/Damage/Support first, then Blizzard's newer section titles."""
    present = {h.role for h in heroes if h.role}
    ordered = [r for r in ROLE_ORDER if r in present]
    for role in _EXTRA_ROLE_ORDER:
        if role in present and role not in ordered:
            ordered.append(role)
    for role in sorted(present):
        if role not in ordered:
            ordered.append(role)
    return ordered


FUN_MODE_COLOR = discord.Color.from_rgb(186, 85, 211)

# Arcade / limited events — not competitive, QP, or Competitive balance
_FUN_MODE_MARKERS = (
    "community crafted",
    "community-crafted",
)
_FUN_MODE_DATES = frozenset({"june 30, 2026"})


def is_fun_mode_patch(summary: PatchSummary | None) -> bool:
    """True for Arcade fun events (e.g. Community Crafted), not retail balance."""
    if summary is None:
        return False
    if summary.fun_mode:
        return True
    blob = f"{summary.title} {summary.patch_id} {summary.fun_label}".lower()
    if any(m in blob for m in _FUN_MODE_MARKERS):
        return True
    return (summary.date or "").strip().lower() in _FUN_MODE_DATES


def has_hero_balance(summary: PatchSummary | None) -> bool:
    """True when a drop should be announced (retail balance or a fun-mode event)."""
    if summary is None:
        return False
    if is_fun_mode_patch(summary):
        return True
    return any(h.changes for h in summary.heroes)


def _balance_fingerprint(summary: PatchSummary) -> str:
    """Compare hero balance content without icon URLs (those may be enriched)."""
    if is_fun_mode_patch(summary):
        return "\n".join(
            [
                summary.fingerprint,
                "fun_mode",
                summary.fun_label,
                summary.fun_note,
            ]
        )
    parts: list[str] = [summary.fingerprint]
    for hero in summary.heroes:
        parts.append(hero.role)
        parts.append(hero.name)
        for ch in hero.changes:
            tone = classify_change_tone(hero.name, ch.ability, ch.text)
            parts.append(f"{ch.ability}|{ch.mode or ''}|{ch.text}|{tone}")
    return "\n".join(parts)


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
    fun_mode: bool = False
    fun_label: str = ""
    fun_note: str = ""

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


# Lower value = better for the hero (reducing these is a buff).
_LESS_IS_BETTER = (
    "cooldown",
    "cool down",
    "charge time",
    "charge delay",
    "cast time",
    "cast delay",
    "wind-up",
    "windup",
    "wind up",
    "startup",
    "recovery",
    "reload time",
    "reload delay",
    "reload duration",
    "time between",
    "delay",
    "fuel drain",
    "resource drain",
    "drain rate",
    "ammo consumption",
    "ammo cost",
    "resource cost",
    "energy cost",
    "ultimate cost",
    "ult cost",
    "cost per",
    "spread",
    "bloom",
    "recoil",
    "self-damage",
    "self damage",
    "movement penalty",
    "move penalty",
    "move slow",
    "slow amount",
    "slow %",
    "vulnerability",
    "knockback taken",
    "knockback received",
    "falloff min",
    "minimum spread",
)

# Higher value = better for the hero (reducing these is a nerf).
_MORE_IS_BETTER = (
    "damage",
    "healing",
    "heal ",
    "health",
    "hp",
    "armor",
    "armour",
    "shields",
    "overhealth",
    "ammo",
    "magazine",
    "clip size",
    "duration",
    "range",
    "radius",
    "projectile speed",
    "move speed",
    "movement speed",
    "speed boost",
    "crit",
    "headshot",
    "lifesteal",
    "fuel regen",
    "regen rate",
    "regeneration",
    "resource regen",
    "reload speed",
    "fire rate",
    "attack speed",
    "knockback",
    "stun",
    "slow duration",
    "falloff range",
    "max range",
    "bonus",
    "multiplier",
)


def _contains_any(text: str, phrases: tuple[str, ...]) -> bool:
    """Phrase match with word edges so 'range' does not hit 'increased'."""
    for phrase in phrases:
        pat = re.escape(phrase).replace(r"\ ", r"[\s-]+")
        if re.search(rf"(?<![a-z0-9]){pat}(?![a-z0-9])", text):
            return True
    return False


def _direction_from_text(text: str) -> str | None:
    """'up' | 'down' | 'add' | 'remove' | None from verbs and from→to numbers."""
    low = text.lower()
    # Prefer the actual numbers: 13s → 12s is down, even if the verb is missing.
    m = re.search(
        r"(-?\d+(?:\.\d+)?)\s*(?:%|s|m|sec|seconds?)?\s*(?:→|->)\s*"
        r"(-?\d+(?:\.\d+)?)",
        low,
    )
    if not m:
        m = re.search(
            r"(?:from|down from|up from)\s+(-?\d+(?:\.\d+)?)\s*(?:%|s|m)?\s*"
            r"(?:→|->|to)\s*(-?\d+(?:\.\d+)?)",
            low,
        )
    if m:
        try:
            a, b = float(m.group(1)), float(m.group(2))
            if b > a:
                return "up"
            if b < a:
                return "down"
        except ValueError:
            pass

    if re.search(r"\b(no longer|removed|removes)\b", low):
        if _contains_any(
            low, ("cooldown", "penalty", "restriction", "self-damage", "self damage")
        ):
            return "add"  # removing a downside
        return "remove"
    if re.search(
        r"\b(added|adds|now grants|now applies|now restores|can now|now has)\b",
        low,
    ):
        return "add"
    if re.search(r"\b(increased|increases|up from|raised|higher)\b", low):
        return "up"
    if re.search(r"\b(reduced|reduces|decreased|decreases|down from|lowered|lower)\b", low):
        return "down"
    return None


def _stat_polarity(text: str) -> str | None:
    """'less' (lower is better) | 'more' (higher is better) | None."""
    low = text.lower()
    # Cooldown always: less waiting = more ability uptime = buff.
    if re.search(r"\bcool\s*-?downs?\b", low):
        return "less"
    if _contains_any(low, _LESS_IS_BETTER):
        return "less"
    if _contains_any(low, _MORE_IS_BETTER):
        return "more"
    return None


def classify_change_tone(*parts: str) -> str:
    """
    Buff/nerf from *what* changed, not just increased/reduced.
    Cooldown/cost/drain down = buff; damage/heal/HP down = nerf.
    """
    blob = " ".join(p for p in parts if p)
    low = blob.lower()
    if re.search(r"\bnerf", low) and not re.search(r"\bbuff", low):
        return "▼"
    if re.search(r"\bbuff", low) and not re.search(r"\bnerf", low):
        return "▲"

    direction = _direction_from_text(low)
    polarity = _stat_polarity(low)

    if direction == "add":
        return "▲"
    if direction == "remove":
        return "▼"
    if direction is None:
        return "•"

    if polarity == "less":
        # Lower cooldown/cost/drain = buff
        return "▲" if direction == "down" else "▼"
    if polarity == "more":
        return "▲" if direction == "up" else "▼"

    # Unknown stat: don't guess — "reduced" is often a buff
    return "•"


def _resolved_tone(ability: str, ch: ChangeLine) -> str:
    """Re-classify at display time so old saved payloads pick up cooldown fixes."""
    tone = classify_change_tone(ability, ch.ability or "", ch.text or "")
    if tone in ("▲", "▼"):
        return tone
    return ch.tone if ch.tone in ("▲", "▼") else "•"


def _payload_tones_stale(summary: PatchSummary) -> bool:
    """True when stored ▲/▼ disagrees with cooldown-aware classification."""
    for hero in summary.heroes:
        for ch in hero.changes:
            resolved = _resolved_tone(ch.ability, ch)
            if ch.tone in ("▲", "▼") and resolved in ("▲", "▼") and ch.tone != resolved:
                return True
    return False


def _tone_from(text: str, *, ability: str = "", label: str = "") -> str:
    return classify_change_tone(ability, label, text)


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
    tone = _tone_from(clean, ability=ability, label=label)
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



def _tone_label(tone: str) -> str:
    """▲ / ▼ with an explicit buff|nerf word so the mark is never ambiguous."""
    if tone == "▲":
        return "▲ buff"
    if tone == "▼":
        return "▼ nerf"
    return "·"


def _format_ability_block(ability: str, lines: list[ChangeLine]) -> str:
    """Ability name, then each buff/nerf on its own indented line."""
    shared = [c for c in lines if not c.mode]
    v5 = [c for c in lines if c.mode == "5v5"]
    v6 = [c for c in lines if c.mode == "6v6"]

    rows: list[str] = [ability]
    for c in shared:
        rows.append(f"{_tone_label(_resolved_tone(ability, c))}  {c.text}")

    if v5 or v6:
        if v5 and v6 and len(v5) == len(v6):
            for a, b in zip(v5, v6):
                rows.append(
                    f"{_tone_label(_resolved_tone(ability, a))}  5v5  {a.text}"
                )
                rows.append(
                    f"{_tone_label(_resolved_tone(ability, b))}  6v6  {b.text}"
                )
        else:
            for c in v5:
                rows.append(
                    f"{_tone_label(_resolved_tone(ability, c))}  5v5  {c.text}"
                )
            for c in v6:
                rows.append(
                    f"{_tone_label(_resolved_tone(ability, c))}  6v6  {c.text}"
                )

    return "\n".join(rows)


def _hero_changes_compact(
    hero: HeroChange, *, max_lines: int | None = None
) -> str:
    """One line per tweak — glyph only; buff/nerf words live in the card legend."""
    changes = hero.changes if max_lines is None else hero.changes[:max_lines]
    rows: list[str] = []
    for ch in changes:
        tone = _resolved_tone(ch.ability, ch)
        if tone == "▲":
            mark = "▲ "
        elif tone == "▼":
            mark = "▼ "
        else:
            mark = ""
        mode = f" · {ch.mode}" if ch.mode else ""
        rows.append(f"{mark}{ch.ability}{mode} · {ch.text}")
    if max_lines is not None:
        extra = len(hero.changes) - max_lines
        if extra > 0:
            rows.append(f"_+{extra} more…_")
    return "\n".join(rows) if rows else "_No detail lines_"


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


def _is_fun_mode_section(title: str) -> bool:
    low = (title or "").lower()
    return any(m in low for m in _FUN_MODE_MARKERS)


def _extract_section_blurb(sec: str, *, limit: int = 480) -> str:
    """Plain intro text from a section, without hero kit cards."""
    # After split, the section starts with leftover class names + ">"
    cleaned = re.sub(r"^[^<>]*>", "", sec, count=1)
    cleaned = re.sub(
        r'<div class="PatchNotesHeroUpdate">.*?'
        r'(?=<div class="PatchNotesHeroUpdate">|<div class="PatchNotes-section|$)',
        "",
        cleaned,
        flags=re.S,
    )
    cleaned = re.sub(r"<h4.*?</h4>", "", cleaned, count=1, flags=re.S)
    text = _strip_tags(cleaned)
    if len(text) > limit:
        text = text[: limit - 1].rsplit(" ", 1)[0] + "…"
    return text


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
    if _is_fun_mode_section(title):
        summary.fun_mode = True
        summary.fun_label = "Community Crafted"

    sections = re.split(r'<div class="PatchNotes-section ', block)[1:]
    mode = "fun" if summary.fun_mode else "retail"
    current_role = ""

    for sec in sections:
        sec_title = _strip_tags(_first(r'PatchNotes-sectionTitle"[^>]*>(.*?)</h4>', sec, re.S))
        low = sec_title.lower()

        if _is_fun_mode_section(sec_title):
            mode = "fun"
            summary.fun_mode = True
            summary.fun_label = sec_title or "Community Crafted"
            if not summary.fun_note:
                summary.fun_note = _extract_section_blurb(sec)
            continue

        if low in ("stadium updates", "bug fixes", "custom game updates"):
            continue

        if mode == "fun":
            continue

        is_hero_section = sec.startswith("PatchNotes-section-hero_update") or (
            "hero_update" in sec[:60]
        )
        if sec_title in ROLE_ORDER:
            current_role = sec_title
        elif is_hero_section:
            # e.g. "Hero Updates" — Blizzard no longer always splits by role
            current_role = sec_title or "Hero Updates"

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
                        role=current_role or "Hero Updates",
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
    if is_fun_mode_patch(summary):
        summary.fun_mode = True
        if not summary.fun_label:
            summary.fun_label = "Community Crafted"
        if (summary.date or "").strip().lower() in _FUN_MODE_DATES:
            summary.heroes = []
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
        if not has_hero_balance(summary):
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
        if is_fun_mode_patch(summary):
            continue
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
        out = [latest] if latest and has_hero_balance(latest) else []
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
    out = [latest] if latest and has_hero_balance(latest) else []
    _PATCH_CACHE, _PATCH_CACHE_AT, _PATCH_CACHE_MONTHS = out, now, 1
    return out


def summary_to_payload(summary: PatchSummary) -> str:
    return json.dumps(
        {
            "patch_id": summary.patch_id,
            "date": summary.date,
            "title": summary.title,
            "url": summary.url,
            "fun_mode": bool(summary.fun_mode or is_fun_mode_patch(summary)),
            "fun_label": summary.fun_label,
            "fun_note": summary.fun_note,
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
            if (c.get("text") or "").strip()
        ]
        if not changes:
            continue
        heroes.append(
            HeroChange(
                name=h.get("name") or "Hero",
                role=h.get("role") or "Damage",
                icon_url=h.get("icon_url"),
                changes=changes,
            )
        )
    summary = PatchSummary(
        patch_id=data.get("patch_id") or data.get("title") or "patch",
        date=data.get("date") or "",
        title=data.get("title") or "Overwatch Patch Notes",
        heroes=heroes,
        url=data.get("url") or PATCH_URL,
        fun_mode=bool(data.get("fun_mode")),
        fun_label=data.get("fun_label") or "",
        fun_note=data.get("fun_note") or "",
    )
    if is_fun_mode_patch(summary):
        summary.fun_mode = True
        if not summary.fun_label:
            summary.fun_label = "Community Crafted"
    return summary


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
        if summary is None or not has_hero_balance(summary):
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
        usable: list[tuple[str, PatchSummary]] = []
        for row in rows:
            summary = summary_from_payload(row["payload"])
            if summary is None or not has_hero_balance(summary):
                continue
            usable.append((row["patch_id"], summary))
        if not usable:
            await interaction.response.send_message(
                "No previous patches saved yet.", ephemeral=True
            )
            return

        if len(usable) == 1:
            await interaction.response.defer(ephemeral=True)
            await send_summary_ephemeral(interaction, usable[0][1], archive=True)
            return

        options: list[discord.SelectOption] = []
        for patch_id, summary in usable[:25]:
            label = (summary.date or patch_id)[:100]
            desc = (summary.title or patch_id)[:100]
            options.append(
                discord.SelectOption(
                    label=label,
                    value=patch_id[:100],
                    description=desc,
                )
            )
        await interaction.response.send_message(
            "Pick a previous patch to view (only you will see it):",
            view=OwArchiveSelectView(options),
            ephemeral=True,
        )

    @discord.ui.button(
        label="Hero history",
        style=discord.ButtonStyle.primary,
        custom_id="ow_patch:hero_history",
    )
    async def hero_history(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        from overwatch_hero_history import HeroHistoryBrowseView

        await interaction.response.send_message(
            "Browse a hero’s balance history across patch notes:",
            view=HeroHistoryBrowseView(),
            ephemeral=True,
        )


def _patch_header_line(
    summary: PatchSummary, *, preview: bool, archive: bool
) -> str:
    date_label = summary.date or "Patch"
    if preview:
        return f"🧪 **Preview** · **[{date_label}]({summary.url})**"
    if archive:
        return f"📜 **Archive** · **[{date_label}]({summary.url})**"
    return f"**[{date_label}]({summary.url})**"


def _fun_mode_body(summary: PatchSummary) -> str:
    event = (summary.fun_label or "Community Crafted").strip() or "Fun Mode"
    note = (summary.fun_note or "").strip()
    if not note:
        note = (
            "Limited-time Arcade event with community-designed hero kits. "
            "This is a fun mode — not Competitive, Quick Play, or ranked balance."
        )
    return (
        f"**🎮  {event} · Arcade fun mode**\n"
        f"Not Competitive, Quick Play, or ranked — kits from this mode are "
        f"omitted from Search Hero Changes.\n\n"
        f"{note}"
    )


def _build_fun_mode_layout(
    summary: PatchSummary,
    *,
    preview: bool = False,
    archive: bool = False,
) -> discord.ui.LayoutView:
    view = discord.ui.LayoutView(timeout=None)
    view.add_item(
        discord.ui.TextDisplay(_patch_header_line(summary, preview=preview, archive=archive))
    )
    container = discord.ui.Container(accent_colour=FUN_MODE_COLOR)
    container.add_item(discord.ui.TextDisplay(_fun_mode_body(summary)))
    view.add_item(container)
    return view


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
    if is_fun_mode_patch(summary):
        return [_build_fun_mode_layout(summary, preview=preview, archive=archive)]

    by_role: dict[str, list[HeroChange]] = {}
    for h in summary.heroes:
        by_role.setdefault(h.role or "Hero Updates", []).append(h)

    header = _patch_header_line(summary, preview=preview, archive=archive)
    date_label = summary.date or "Patch"

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
        if archive:
            cont = f"📜 **Archive** · **[{date_label}]({summary.url})** · cont."
        elif preview:
            cont = f"🧪 **Preview** · **[{date_label}]({summary.url})** · cont."
        else:
            cont = f"**[{date_label}]({summary.url})** · cont."
        view.add_item(discord.ui.TextDisplay(cont))

    for role in _roles_for_display(summary.heroes):
        heroes = by_role.get(role, [])
        if not heroes:
            continue

        colour = ROLE_COLOR.get(role, OW_ORANGE)
        label = ROLE_HEADER.get(role, f"✨  {role.upper()}")

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
    if is_fun_mode_patch(summary):
        event = (summary.fun_label or "Community Crafted").strip() or "Fun Mode"
        emb = discord.Embed(
            title=summary.date or "Overwatch patch",
            description=(
                ("🧪 **Preview**\n\n" if preview else "") + _fun_mode_body(summary)
            ),
            color=FUN_MODE_COLOR,
            url=summary.url,
        )
        emb.set_author(name=f"Arcade · {event}")
        return [emb]

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

    by_role: dict[str, list[HeroChange]] = {}
    for h in summary.heroes:
        by_role.setdefault(h.role or "Hero Updates", []).append(h)

    embeds: list[discord.Embed] = [head]
    for role in _roles_for_display(summary.heroes):
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
        removed = self.bot.db.purge_empty_ow_patches()
        if removed:
            log.info("Removed %s empty OW patch archive entries", removed)
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

    async def enrich_hero_icons(self, summary: PatchSummary) -> None:
        """Prefer latest official Blizzard portraits when the roster has them."""
        try:
            from overwatch_tierlist import (
                fetch_blizzard_hero_icons,
                _blizzard_icon_indexes,
                _normalize_hero_token,
            )

            session = await self._get_session()
            icons = await fetch_blizzard_hero_icons(session)
            by_id, by_name = _blizzard_icon_indexes(icons)

            for hero in summary.heroes:
                token = _normalize_hero_token(hero.name)
                blizz = by_id.get(token) or by_name.get(token)
                if blizz:
                    hero.icon_url = blizz
        except Exception as exc:
            log.warning("Patch Blizzard icon enrich failed: %s", exc)

    async def refresh_app_hero_emojis(self) -> None:
        """Pull new/changed hero portraits into Discord app emojis."""
        tier_cog = self.bot.get_cog("OverwatchTierCog")
        if tier_cog is None:
            return
        try:
            await tier_cog.sync_blizzard_hero_emojis()
        except Exception as exc:
            log.warning("Post-patch hero emoji sync failed: %s", exc)

    async def post_to_channel(
        self,
        channel: discord.TextChannel | discord.ForumChannel,
        summary: PatchSummary,
        *,
        preview: bool = False,
        with_history: bool = False,
        existing_thread_id: int | None = None,
    ) -> tuple[list[discord.Message], int | None]:
        await self.enrich_hero_icons(summary)
        layouts = build_patch_layouts(summary, preview=preview)
        return await post_ow_announcement(
            channel,
            thread_name=patch_thread_title(
                date=summary.date,
                title=summary.title,
                fun_mode=is_fun_mode_patch(summary),
                fun_label=summary.fun_label or None,
            ),
            layouts=layouts,
            embeds_fallback=lambda: build_patch_embeds(summary, preview=preview),
            tag_names=OW_PATCH_TAG_NAMES,
            trailing_content=(
                "Earlier notes · hero history:" if with_history else None
            ),
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
        if not has_hero_balance(summary):
            log.info(
                "Skipping empty OW patch %s — no retail hero balance",
                summary.fingerprint,
            )
            return []
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
        await self.enrich_hero_icons(summary)
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

        if not has_hero_balance(summary):
            return False, f"Skipped `{summary.fingerprint}` — no hero balance."

        if self.bot.db.was_ow_patch_announced(guild.id, summary.fingerprint):
            # Re-edit the live post when Blizzard (or our parser) adds hero cards
            # that the first publish missed — e.g. "Hero Updates" without Tank/Damage/Support.
            old_raw = self.bot.db.get_ow_patch_payload(guild.id, summary.fingerprint)
            old = summary_from_payload(old_raw) if old_raw else None
            same_lines = (
                old is not None
                and _balance_fingerprint(old) == _balance_fingerprint(summary)
            )
            stale_tones = old is not None and _payload_tones_stale(old)
            if same_lines and not stale_tones:
                return False, f"Already posted `{summary.fingerprint}`."
            messages = await self.publish_live(channel, summary)
            if not messages:
                return False, f"Skipped `{summary.fingerprint}` — no hero balance."
            return True, f"Refreshed {summary.title}"

        messages = await self.publish_live(channel, summary)
        if not messages:
            return False, f"Skipped `{summary.fingerprint}` — no hero balance."
        return True, summary.title

    @tasks.loop(hours=config.OW_PATCH_CHECK_HOURS)
    async def check_patches(self) -> None:
        any_posted = False
        for guild in self.bot.guilds:
            try:
                posted, detail = await self.announce_if_new(guild)
                if posted:
                    any_posted = True
                    log.info("OW patch posted in %s: %s", guild.name, detail)
            except Exception as exc:
                log.warning("OW patch check failed for %s: %s", guild.name, exc)
        if any_posted:
            await self.refresh_app_hero_emojis()

    @check_patches.before_loop
    async def before_check(self) -> None:
        await self.bot.wait_until_ready()
