"""Hero patch lookup for Dream voice/chat answers."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from overwatch_patches import (
    HeroChange,
    PatchSummary,
    fetch_latest_summary,
    summary_from_payload,
)

log = logging.getLogger("dream_team.ai_ow")

# Common RU → EN hero names (wake questions may be mixed)
HERO_ALIASES: dict[str, str] = {
    "гендзи": "genji",
    "генджи": "genji",
    "ханзо": "hanzo",
    "трейсер": "tracer",
    "трасер": "tracer",
    "солдат": "soldier: 76",
    "солдат 76": "soldier: 76",
    "вдова": "widowmaker",
    "видовмейкер": "widowmaker",
    "рейнхардт": "reinhardt",
    "рейн": "reinhardt",
    "заря": "zarya",
    "мэй": "mei",
    "мей": "mei",
    "лусио": "lucio",
    "люсио": "lucio",
    "ана": "ana",
    "батист": "baptiste",
    "кирико": "kiriko",
    "соджорн": "sojourn",
    "джанкрат": "junkrat",
    "роадхог": "roadhog",
    "д.ва": "d.va",
    "два": "d.va",
    "сигма": "sigma",
    "орisa": "orisa",
    "ориса": "orisa",
    "бастион": "bastion",
    "симметра": "symmetra",
    "торбьорн": "torbjorn",
    "торб": "torbjorn",
    "эхо": "echo",
    "эштион": "ashe",
    "эш": "ashe",
    "кэссиди": "cassidy",
    "кэссиди": "cassidy",
    "маккри": "cassidy",
    "фенн": "freja",
    "хойер": "hazard",
}


@dataclass
class HeroPatchHit:
    hero_name: str
    patch_date: str
    patch_title: str
    in_latest: bool
    latest_date: str
    lines: list[str]  # plain spoken-friendly change lines
    buffish: bool
    nerfish: bool


def _plain(text: str) -> str:
    return re.sub(r"[`*_]", "", text or "").strip()


def _hero_lines(hero: HeroChange) -> list[str]:
    lines: list[str] = []
    by_ability: dict[str, list[str]] = {}
    for ch in hero.changes:
        by_ability.setdefault(ch.ability or "General", []).append(_plain(ch.text))
    for ability, bits in by_ability.items():
        joined = "; ".join(b for b in bits if b)
        if joined:
            lines.append(f"{ability}: {joined}")
    return lines


def _tone_flags(hero: HeroChange) -> tuple[bool, bool]:
    buff = nerf = False
    for ch in hero.changes:
        if ch.tone == "▲":
            buff = True
        elif ch.tone == "▼":
            nerf = True
        else:
            low = (ch.text or "").lower()
            if any(w in low for w in ("increased", "buff", "grants", "added")):
                buff = True
            if any(w in low for w in ("reduced", "nerf", "decreased", "removed")):
                nerf = True
    return buff, nerf


def extract_hero_query(text: str) -> str | None:
    """Best-effort hero name from a user question."""
    low = (text or "").lower()
    # Explicit alias first
    for alias, canon in sorted(HERO_ALIASES.items(), key=lambda x: -len(x[0])):
        if alias in low:
            return canon
    # "was Genji nerfed" / "гендзи баффнули"
    m = re.search(
        r"(?i)\b(?:was|is|did|has|про|был|была|бафф|нерф|патч[аеиу]?)\s+"
        r"([a-zа-яё0-9:.\-']{2,20})\b",
        text or "",
    )
    if m:
        token = m.group(1).strip(" ?.,!")
        return HERO_ALIASES.get(token.lower(), token)
    m = re.search(
        r"(?i)\b([a-z][a-z0-9:.\-']{1,20})\b(?:\s+(?:nerf|buff|patch|changed|updated))",
        text or "",
    )
    if m:
        return m.group(1)
    return None


def find_hero_in_summary(
    summary: PatchSummary, hero_query: str
) -> HeroChange | None:
    q = hero_query.lower().strip()
    q = HERO_ALIASES.get(q, q)
    for h in summary.heroes:
        name = (h.name or "").lower()
        if name == q or name.startswith(q) or q in name:
            return h
    return None


async def lookup_hero_patch(
    bot, guild_id: int, hero_query: str
) -> tuple[HeroPatchHit | None, str]:
    """
    Search live latest notes, then guild archive.
    Returns (hit_or_none, latest_patch_date).
    """
    latest = await fetch_latest_summary()
    latest_date = (latest.date if latest else "") or ""
    latest_title = (latest.title if latest else "") or ""

    if latest:
        hero = find_hero_in_summary(latest, hero_query)
        if hero:
            buff, nerf = _tone_flags(hero)
            return (
                HeroPatchHit(
                    hero_name=hero.name,
                    patch_date=latest_date,
                    patch_title=latest_title,
                    in_latest=True,
                    latest_date=latest_date,
                    lines=_hero_lines(hero),
                    buffish=buff,
                    nerfish=nerf,
                ),
                latest_date,
            )

    # Archive (newest first)
    try:
        rows = bot.db.list_ow_patch_history(guild_id, limit=20)
    except Exception:
        rows = []

    for row in rows:
        raw = None
        try:
            raw = row["payload"]
        except (KeyError, IndexError, TypeError):
            raw = None
        if not raw:
            try:
                raw = bot.db.get_ow_patch_payload(guild_id, row["patch_id"])
            except Exception:
                continue
        summary = summary_from_payload(raw or "")
        if summary is None:
            continue
        hero = find_hero_in_summary(summary, hero_query)
        if hero is None:
            continue
        buff, nerf = _tone_flags(hero)
        return (
            HeroPatchHit(
                hero_name=hero.name,
                patch_date=summary.date or "",
                patch_title=summary.title or "",
                in_latest=False,
                latest_date=latest_date,
                lines=_hero_lines(hero),
                buffish=buff,
                nerfish=nerf,
            ),
            latest_date,
        )

    return None, latest_date


def facts_block(hit: HeroPatchHit) -> str:
    kind = "mixed"
    if hit.buffish and not hit.nerfish:
        kind = "buff"
    elif hit.nerfish and not hit.buffish:
        kind = "nerf"
    lines = "\n".join(f"- {ln}" for ln in hit.lines[:8]) or "- (no detail lines)"
    return (
        f"HERO: {hit.hero_name}\n"
        f"PATCH_DATE: {hit.patch_date or 'unknown'}\n"
        f"PATCH_TITLE: {hit.patch_title or ''}\n"
        f"IN_LATEST_PATCH: {hit.in_latest}\n"
        f"LATEST_PATCH_DATE: {hit.latest_date or 'unknown'}\n"
        f"CHANGE_KIND: {kind}\n"
        f"CHANGES:\n{lines}"
    )
