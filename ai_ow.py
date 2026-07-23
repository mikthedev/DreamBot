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

# Canon English names (as they appear on Blizzard notes)
CANON_HEROES: tuple[str, ...] = (
    "doomfist",
    "tracer",
    "genji",
    "hanzo",
    "widowmaker",
    "reinhardt",
    "winston",
    "d.va",
    "roadhog",
    "junkrat",
    "mei",
    "ana",
    "mercy",
    "lucio",
    "lúcio",
    "kiriko",
    "junker queen",
    "ramattra",
    "orisa",
    "sigma",
    "cassidy",
    "ashe",
    "soldier: 76",
    "sojourn",
    "sombra",
    "symmetra",
    "torbjörn",
    "torbjorn",
    "pharah",
    "echo",
    "freja",
    "venture",
    "illari",
    "lifeweaver",
    "baptiste",
    "zenyatta",
    "moira",
    "brigitte",
    "zarya",
    "wrecking ball",
    "mauga",
    "hazard",
    "vendetta",
    "shion",
    "sierra",
    "wuyang",
    "juno",
    "bastion",
    "reaper",
)

HERO_ALIASES: dict[str, str] = {
    # Russian / UA nicknames
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
    "дум": "doomfist",
    "королева": "junker queen",
    "раматра": "ramattra",
    "трейсерка": "tracer",
    "винстон": "winston",
    "фара": "pharah",
    "фарах": "pharah",
    "хомкраб": "wrecking ball",
    "хог": "roadhog",
    "жнец": "reaper",
    "рипер": "reaper",
    # English ASR / slang
    "doom": "doomfist",
    "doom fist": "doomfist",
    "dumfist": "doomfist",
    "dum fist": "doomfist",
    "mccree": "cassidy",
    "soldier": "soldier: 76",
    "soldier76": "soldier: 76",
    "ball": "wrecking ball",
    "hammond": "wrecking ball",
    "queen": "junker queen",
    "jq": "junker queen",
    "df": "doomfist",
}

# Whisper often mangles hero names — rewrite before intent parsing
_ASR_HERO_FIXES: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(pat, re.I), repl)
    for pat, repl in (
        (r"\bdoom\s*fist\b", "Doomfist"),
        (r"\bdum\s*fist\b", "Doomfist"),
        (r"\bdon'?t\s*fist\b", "Doomfist"),
        (r"\bnew\s*fist\b", "Doomfist"),
        (r"\bdoomfist\b", "Doomfist"),
        (r"\btrace\s*her\b", "Tracer"),
        (r"\btracer\b", "Tracer"),
        (r"\bgen\s*g[iy]\b", "Genji"),
        (r"\bgenji\b", "Genji"),
        (r"\bwidow\s*maker\b", "Widowmaker"),
        (r"\brein\s*hardt\b", "Reinhardt"),
        (r"\bjunker\s*queen\b", "Junker Queen"),
        (r"\brock\s*hog\b", "Roadhog"),
        (r"\broad\s*hog\b", "Roadhog"),
        (r"\bjunk\s*rat\b", "Junkrat"),
        (r"\bsoldier\s*(?:76|seventy\s*six)?\b", "Soldier: 76"),
        (r"\bmc\s*cree\b", "Cassidy"),
        (r"\bcassidy\b", "Cassidy"),
        (r"\bkiri\s*ko\b", "Kiriko"),
        (r"\blife\s*weaver\b", "Lifeweaver"),
        (r"\bwrecking\s*ball\b", "Wrecking Ball"),
        (r"\bramattra\b", "Ramattra"),
        (r"\bmauga\b", "Mauga"),
        (r"\bsojourn\b", "Sojourn"),
        (r"\bpharah\b", "Pharah"),
        (r"\bbaptiste\b", "Baptiste"),
        (r"\bzenyatta\b", "Zenyatta"),
        (r"\bbrigitte\b", "Brigitte"),
        (r"\billari\b", "Illari"),
        (r"\bfreja\b", "Freja"),
        (r"\bventure\b", "Venture"),
        (r"\bhaz\s*ard\b", "Hazard"),
        (r"\bd\.?\s*va\b", "D.Va"),
    )
)


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


def fix_ow_asr(text: str) -> str:
    """Correct common Whisper mangling of Overwatch hero names."""
    out = text or ""
    for pat, repl in _ASR_HERO_FIXES:
        out = pat.sub(repl, out)
    return out


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


_STOP_HERO_TOKENS = frozenset(
    {
        "he",
        "she",
        "they",
        "him",
        "her",
        "his",
        "the",
        "a",
        "an",
        "was",
        "is",
        "are",
        "were",
        "been",
        "nerf",
        "nerfs",
        "buff",
        "buffs",
        "patch",
        "meta",
        "hero",
        "that",
        "this",
        "it",
        "me",
        "my",
        "you",
        "your",
        "about",
        "what",
        "how",
        "when",
        "who",
        "why",
        "and",
        "for",
        "with",
        "from",
        "just",
        "like",
        "have",
        "has",
        "had",
        "did",
        "does",
        "will",
        "would",
        "could",
        "should",
        "also",
        "same",
        "last",
        "latest",
        "recent",
    }
)


def extract_hero_query(text: str) -> str | None:
    """Best-effort hero name from a user question."""
    text = fix_ow_asr(text or "")
    low = text.lower()

    # Prefer longer aliases / names so "junker queen" wins over "queen"
    for alias, canon in sorted(HERO_ALIASES.items(), key=lambda x: -len(x[0])):
        if alias in low:
            return canon

    for name in sorted(CANON_HEROES, key=len, reverse=True):
        if name in low:
            return HERO_ALIASES.get(name, name)

    m = re.search(
        r"(?i)\b(?:was|is|did|has|how|about|про|был|была|бафф|нерф|патч[аеиу]?)\s+"
        r"([a-zа-яё0-9:.\-']{2,24})\b",
        text,
    )
    if m:
        token = m.group(1).strip(" ?.,!").lower()
        if token not in _STOP_HERO_TOKENS:
            return HERO_ALIASES.get(token, token)
    m = re.search(
        r"(?i)\b([a-z][a-z0-9:.\-']{2,24})\b(?:\s+(?:nerfs?|nerfed|buffs?|buffed|patch(?:ed|es)?|changed|updated|meta))\b",
        text,
    )
    if m:
        token = m.group(1).lower()
        if token not in _STOP_HERO_TOKENS:
            return HERO_ALIASES.get(token, token)
    return None


def find_hero_in_summary(
    summary: PatchSummary, hero_query: str
) -> HeroChange | None:
    q = hero_query.lower().strip()
    q = HERO_ALIASES.get(q, q)
    compact_q = q.replace(" ", "").replace(":", "")
    for h in summary.heroes:
        name = (h.name or "").lower()
        compact = name.replace(" ", "").replace(":", "")
        if name == q or name.startswith(q) or compact_q in compact:
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
    hero_query = HERO_ALIASES.get(hero_query.lower().strip(), hero_query)
    patches = await fetch_all_patch_summaries(
        limit=40, max_months=6, stop_hero=hero_query
    )
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


def change_kind(hit: HeroPatchHit) -> str:
    if hit.buffish and not hit.nerfish:
        return "buff"
    if hit.nerfish and not hit.buffish:
        return "nerf"
    return "mixed"


def change_kind_phrase(hit: HeroPatchHit, lang: str) -> str:
    kind = change_kind(hit)
    if lang == "uk":
        return {"buff": "баф", "nerf": "нерф", "mixed": "змішані зміни"}[kind]
    if lang == "ru":
        return {"buff": "бафф", "nerf": "нерф", "mixed": "микс изменений"}[kind]
    return {"buff": "a buff", "nerf": "a nerf", "mixed": "mixed changes"}[kind]


def facts_block(hit: HeroPatchHit) -> str:
    kind = change_kind(hit)
    lines = "\n".join(f"- {ln}" for ln in hit.lines[:8]) or "- (no detail lines)"
    return (
        f"HERO: {hit.hero_name}\n"
        f"PATCH_DATE: {hit.patch_date or 'unknown'}\n"
        f"IN_LATEST_PATCH: {hit.in_latest}\n"
        f"LATEST_PATCH_DATE: {hit.latest_date or 'unknown'}\n"
        f"CHANGE_KIND: {kind}\n"
        f"CHANGES:\n{lines}\n"
        "IMPORTANT: Speak using PATCH_DATE / LATEST_PATCH_DATE only — "
        "never invent or say a patch title/name. "
        "CHANGE_KIND is authoritative for buff vs nerf — do not flip it."
    )


def patch_teaser(hit: HeroPatchHit, lang: str) -> str:
    """Factual first reply — no LLM, so buff/nerf/date can't be invented."""
    name = hit.hero_name
    date = hit.patch_date or "unknown"
    latest = hit.latest_date or "unknown"
    kind = change_kind_phrase(hit, lang)
    if hit.in_latest:
        if lang == "uk":
            return f"{name} у патчі {date} отримав {kind}. Розкажу деталі?"
        if lang == "ru":
            return f"{name} в патче {date} получил {kind}. Рассказать детали?"
        return f"{name} got {kind} in the {date} patch. Want the highlights?"
    if lang == "uk":
        return (
            f"{name} не чіпали в останньому патчі ({latest}). "
            f"Останні правки — {date} ({kind}). Хочеш деталі?"
        )
    if lang == "ru":
        return (
            f"{name} не трогали в последнем патче ({latest}). "
            f"Последние правки — {date} ({kind}). Хочешь детали?"
        )
    return (
        f"{name} wasn't touched in the latest patch ({latest}). "
        f"Last change was {date} ({kind}). Want the highlights?"
    )


def patch_details_from_facts(facts: str, *, hero: str, lang: str) -> str:
    """Speak only the stored Blizzard change lines — no freeform inventing."""
    changes: list[str] = []
    in_changes = False
    for line in (facts or "").splitlines():
        if line.startswith("CHANGES:"):
            in_changes = True
            continue
        if in_changes:
            if line.startswith("- "):
                bit = line[2:].strip()
                if bit and bit != "(no detail lines)":
                    changes.append(bit)
            elif line.strip():
                break
    picks = changes[:2]
    if not picks:
        if lang == "uk":
            return f"По {hero} детальних рядків у нотатках немає."
        if lang == "ru":
            return f"По {hero} детальных строк в заметках нет."
        return f"No detailed lines for {hero} in the notes."
    body = "; ".join(picks)
    if lang == "uk":
        return f"По {hero}: {body}"
    if lang == "ru":
        return f"По {hero}: {body}"
    return f"On {hero}: {body}"
