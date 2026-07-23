"""Hero patch lookup for Dream — always from Blizzard patch-notes page."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from overwatch_patches import (
    HeroChange,
    PatchSummary,
    fetch_all_patch_summaries,
    summary_from_payload,
)

log = logging.getLogger("dream_team.ai_ow")

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
    "ориса": "orisa",
    "бастион": "bastion",
    "симметра": "symmetra",
    "торбьорн": "torbjorn",
    "торб": "torbjorn",
    "эхо": "echo",
    "эш": "ashe",
    "кэссиди": "cassidy",
    "маккри": "cassidy",
    "фрея": "freja",
    "сиерра": "sierra",
    "шион": "shion",
    "вендетта": "vendetta",
    "мауга": "mauga",
    "думфист": "doomfist",
    "королева": "junker queen",
    "раматра": "ramattra",
}


@dataclass
class HeroPatchHit:
    hero_name: str
    patch_date: str
    in_latest: bool
    latest_date: str
    lines: list[str]
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
            if any(
                w in low
                for w in (
                    "increased",
                    "increases",
                    "buff",
                    "grants",
                    "added",
                    "boost",
                )
            ):
                buff = True
            if any(
                w in low
                for w in (
                    "reduced",
                    "reduces",
                    "nerf",
                    "decreased",
                    "decreases",
                    "removed",
                )
            ):
                nerf = True
    return buff, nerf


def extract_hero_query(text: str) -> str | None:
    """Best-effort hero name from a user question."""
    low = (text or "").lower()
    for alias, canon in sorted(HERO_ALIASES.items(), key=lambda x: -len(x[0])):
        if alias in low:
            return canon
    m = re.search(
        r"(?i)\b(?:was|is|did|has|how|about|про|был|была|бафф|нерф|патч[аеиу]?)\s+"
        r"([a-zа-яё0-9:.\-']{2,24})\b",
        text or "",
    )
    if m:
        token = m.group(1).strip(" ?.,!")
        return HERO_ALIASES.get(token.lower(), token)
    m = re.search(
        r"(?i)\b([a-z][a-z0-9:.\-']{1,24})\b(?:\s+(?:nerf|buff|patch|changed|updated|meta))",
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
        if name == q or name.startswith(q) or q in name.replace(" ", ""):
            return h
        if q.replace(" ", "") in name.replace(" ", ""):
            return h
    return None


async def lookup_hero_patch(
    bot, guild_id: int, hero_query: str
) -> tuple[HeroPatchHit | None, str]:
    """
    Always check Blizzard patch notes
    (https://overwatch.blizzard.com/en-us/news/patch-notes/), walking the
    same month calendar the site uses, newest→oldest, until this hero appears.
    Fall back to the guild archive only if the live site has nothing.
    """
    patches = await fetch_all_patch_summaries(limit=25, max_months=8)
    latest_date = (patches[0].date if patches else "") or ""

    for i, summary in enumerate(patches):
        hero = find_hero_in_summary(summary, hero_query)
        if hero is None:
            continue
        buff, nerf = _tone_flags(hero)
        return (
            HeroPatchHit(
                hero_name=hero.name,
                patch_date=summary.date or "",
                in_latest=(i == 0),
                latest_date=latest_date,
                lines=_hero_lines(hero),
                buffish=buff,
                nerfish=nerf,
            ),
            latest_date,
        )

    # Guild archive as backup if the live page didn't list older retail changes
    try:
        rows = bot.db.list_ow_patch_history(guild_id, limit=25)
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
        f"IN_LATEST_PATCH: {hit.in_latest}\n"
        f"LATEST_PATCH_DATE: {hit.latest_date or 'unknown'}\n"
        f"CHANGE_KIND: {kind}\n"
        f"CHANGES:\n{lines}\n"
        "IMPORTANT: Speak using PATCH_DATE / LATEST_PATCH_DATE only — "
        "never invent or say a patch title/name."
    )
