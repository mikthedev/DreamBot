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
    "d.mon",
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
    "дива": "d.va",
    "диво": "d.va",
    "діва": "d.va",
    "діво": "d.va",
    "diva": "d.va",
    "divas": "d.va",
    "zyva": "d.va",
    "ziva": "d.va",
    "deeva": "d.va",
    "д.мон": "d.mon",
    "дмон": "d.mon",
    "демон": "d.mon",
    "dmon": "d.mon",
    "d mon": "d.mon",
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
    "soldier 76": "soldier: 76",
    "ball": "wrecking ball",
    "hammond": "wrecking ball",
    "queen": "junker queen",
    "jq": "junker queen",
    "df": "doomfist",
    "dee va": "d.va",
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
    patch_id: str = ""
    patch_url: str = ""
    hero: HeroChange | None = None


def _hit_from_summary(
    summary: PatchSummary,
    hero: HeroChange,
    *,
    index: int,
    latest_date: str,
) -> HeroPatchHit:
    buff, nerf = _tone_flags(hero)
    return HeroPatchHit(
        hero_name=hero.name,
        patch_date=summary.date or "",
        in_latest=(index == 0),
        latest_date=latest_date,
        lines=_hero_lines(hero),
        buffish=buff,
        nerfish=nerf,
        patch_id=summary.patch_id or summary.fingerprint,
        patch_url=summary.url or "",
        hero=hero,
    )


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
    from overwatch_patches import classify_change_tone

    buff = nerf = False
    for ch in hero.changes:
        tone = ch.tone
        if tone not in ("▲", "▼"):
            tone = classify_change_tone(ch.ability or "", ch.text or "")
        if tone == "▲":
            buff = True
        elif tone == "▼":
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
    """Best-effort hero name from a user question (aliases for intent only)."""
    low = (text or "").lower()
    # Normalize spaced D.Va variants for matching without rewriting the utterance
    low = re.sub(r"\bd\s*\.\s*va\b", "d.va", low)
    low = re.sub(r"\bdee\s*va\b", "d.va", low)

    # Prefer longer aliases / names so "junker queen" wins over "queen"
    # Word-boundary match — avoid "два" inside unrelated words
    for alias, canon in sorted(HERO_ALIASES.items(), key=lambda x: -len(x[0])):
        if re.search(rf"(?i)(?<!\w){re.escape(alias)}(?!\w)", low):
            return canon

    for name in sorted(CANON_HEROES, key=len, reverse=True):
        if re.search(rf"(?i)(?<!\w){re.escape(name)}(?!\w)", low):
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
    hits, latest_date = await lookup_hero_patch_history(
        bot, guild_id, hero_query, max_hits=1
    )
    return (hits[0] if hits else None), latest_date


async def lookup_hero_patch_history(
    bot,
    guild_id: int,
    hero_query: str,
    *,
    max_hits: int = 25,
    max_months: int = 24,
) -> tuple[list[HeroPatchHit], str]:
    """
    Every retail balance touch for one hero, newest → oldest.
    Prefer live Blizzard notes; fill gaps from the guild's saved patch archive.
    """
    hero_query = HERO_ALIASES.get(hero_query.lower().strip(), hero_query.lower().strip())
    patches = await fetch_all_patch_summaries(
        limit=max(120, max_hits * 5),
        max_months=max_months,
        stop_hero=None,
    )
    latest_date = (patches[0].date if patches else "") or ""
    hits: list[HeroPatchHit] = []
    seen: set[str] = set()

    for i, summary in enumerate(patches):
        hero = find_hero_in_summary(summary, hero_query)
        if hero is None:
            continue
        key = summary.patch_id or summary.date or summary.title
        if key in seen:
            continue
        seen.add(key)
        hits.append(
            _hit_from_summary(
                summary, hero, index=i, latest_date=latest_date
            )
        )
        if len(hits) >= max_hits:
            return hits, latest_date

    # Guild archive (live + older posts) for anything Blizzard walk missed
    archive: list = []
    try:
        latest_id = bot.db.latest_ow_patch_id(guild_id)
        if latest_id:
            raw = bot.db.get_ow_patch_payload(guild_id, latest_id)
            if raw:
                archive.append({"patch_id": latest_id, "payload": raw})
        archive.extend(bot.db.list_ow_patch_history(guild_id, limit=40) or [])
    except Exception as exc:
        log.warning("Hero history archive read failed: %s", exc)

    for row in archive:
        if len(hits) >= max_hits:
            break
        raw = None
        try:
            raw = row["payload"]
            pid = row["patch_id"]
        except (KeyError, IndexError, TypeError):
            continue
        if not raw:
            try:
                raw = bot.db.get_ow_patch_payload(guild_id, pid)
            except Exception:
                continue
        summary = summary_from_payload(raw or "")
        if summary is None:
            continue
        key = summary.patch_id or summary.date or summary.title
        if key in seen:
            continue
        hero = find_hero_in_summary(summary, hero_query)
        if hero is None:
            continue
        seen.add(key)
        hits.append(
            _hit_from_summary(
                summary, hero, index=len(hits), latest_date=latest_date
            )
        )

    return hits[:max_hits], latest_date


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
            return f"{name} — {kind} у патчі {date}. Деталі?"
        if lang == "ru":
            return f"{name} — {kind} в патче {date}. Детали?"
        return f"{name} — {kind} on {date}. Want details?"
    if lang == "uk":
        return (
            f"{name} не чіпали цього разу ({latest}). "
            f"Останнє — {date}, {kind}. Деталі?"
        )
    if lang == "ru":
        return (
            f"{name} не трогали в этом патче ({latest}). "
            f"Последнее — {date}, {kind}. Детали?"
        )
    return (
        f"{name} wasn't in the latest patch ({latest}). "
        f"Last touch: {date}, {kind}. Want details?"
    )


def _shorten_change_line(line: str) -> str:
    """One short spoken clause from a patch bullet."""
    text = (line or "").strip()
    if ":" in text:
        ability, rest = text.split(":", 1)
        rest = re.split(r"[;.]", rest.strip())[0].strip()
        # Drop long "from X to Y to Z" walls — keep first ~12 words
        words = rest.split()
        if len(words) > 12:
            rest = " ".join(words[:12]) + "…"
        short = f"{ability.strip()}: {rest}"
    else:
        words = text.split()
        short = " ".join(words[:14]) + ("…" if len(words) > 14 else "")
    if len(short) > 110:
        short = short[:107] + "…"
    return short


def patch_details_from_facts(facts: str, *, hero: str, lang: str) -> str:
    """One brief highlight — not a full patch-note read."""
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
    if not changes:
        if lang == "uk":
            return f"По {hero} коротких деталей немає."
        if lang == "ru":
            return f"По {hero} коротких деталей нет."
        return f"No short details for {hero}."
    bit = _shorten_change_line(changes[0])
    if lang == "uk":
        return f"Коротко по {hero}: {bit}."
    if lang == "ru":
        return f"Коротко по {hero}: {bit}."
    return f"Quick hit on {hero}: {bit}."
