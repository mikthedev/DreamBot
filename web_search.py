"""Free factual web lookup — Wikipedia + DuckDuckGo (no API key)."""

from __future__ import annotations

import logging
import re
import ssl
from html import unescape
from urllib.parse import quote

import aiohttp
import certifi

log = logging.getLogger("dream_team.web_search")

_SSL_CTX = ssl.create_default_context(cafile=certifi.where())
_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)
_BOT_UA = "DreamTeamBot/1.0 (+discord; factual lookup)"

_FACT_CUE_RE = re.compile(
    r"(?i)\b("
    r"who|what|when|where|which|whom|whose|how\s+old|born|real\s+name|"
    r"actress|actor|singer|celebrity|famous|biography|bio|"
    r"is|was|were|are|does|did|mean|definition|fact|"
    r"кто|что|когда|где|какой|какая|какое|сколько|родил|"
    r"актрис|актёр|актер|певиц|знаменит|"
    r"хто|що|коли|де|який|яка|скільки|народил|"
    r"акторк|співач"
    r")\b"
)

_SMALLTALK_RE = re.compile(
    r"(?i)^\s*("
    r"hi|hey|hello|yo|sup|thanks|thank you|ty|ok|okay|sure|lol|haha|"
    r"привет|хай|спасибо|да|нет|ок|ладно|"
    r"привіт|дякую|так|ні|"
    r"tell\s+(me\s+)?(a\s+)?joke|шутк|анекдот|"
    r"how\s+are\s+you|как\s+дела|як\s+справи"
    r")\s*[.!]?\s*$"
)


def needs_web_search(text: str) -> bool:
    """Heuristic: look up the web for factual / who-is questions."""
    t = (text or "").strip()
    if len(t) < 3 or _SMALLTALK_RE.match(t):
        return False
    if _FACT_CUE_RE.search(t):
        return True
    if "?" in t:
        return True
    # "Millie Bobby Brown" / multi-word proper-looking names
    caps = re.findall(r"\b[A-ZА-ЯІЇЄҐЁ][\w'’\-]+(?:\s+[A-ZА-ЯІЇЄҐЁ][\w'’\-]+){1,4}\b", t)
    if caps:
        return True
    return False


def _clean_html(text: str) -> str:
    text = unescape(re.sub(r"<[^>]+>", " ", text or ""))
    text = re.sub(r"\s+", " ", text).strip()
    return text


async def _wikipedia_facts(
    query: str, session: aiohttp.ClientSession
) -> list[str]:
    facts: list[str] = []
    headers = {"User-Agent": _BOT_UA, "Accept": "application/json"}
    try:
        async with session.get(
            "https://en.wikipedia.org/w/api.php",
            params={
                "action": "opensearch",
                "search": query,
                "limit": 3,
                "namespace": 0,
                "format": "json",
            },
            headers=headers,
            ssl=_SSL_CTX,
            timeout=aiohttp.ClientTimeout(total=8),
        ) as resp:
            if resp.status >= 400:
                return facts
            data = await resp.json(content_type=None)
    except Exception:
        log.debug("Wikipedia opensearch failed", exc_info=True)
        return facts

    titles = data[1] if isinstance(data, list) and len(data) > 1 else []
    for title in titles[:2]:
        if not title:
            continue
        try:
            async with session.get(
                "https://en.wikipedia.org/api/rest_v1/page/summary/"
                + quote(str(title).replace(" ", "_"), safe="_()':,"),
                headers=headers,
                ssl=_SSL_CTX,
                timeout=aiohttp.ClientTimeout(total=8),
            ) as resp:
                if resp.status >= 400:
                    continue
                page = await resp.json(content_type=None)
        except Exception:
            continue
        if not isinstance(page, dict):
            continue
        if page.get("type") == "disambiguation":
            continue
        extract = (page.get("extract") or "").strip()
        label = (page.get("title") or title).strip()
        if extract:
            # Keep first ~2 sentences for voice brevity
            parts = re.split(r"(?<=[.!?])\s+", extract)
            short = " ".join(parts[:2]).strip()
            facts.append(f"Wikipedia ({label}): {short}")
            break
    return facts


async def _ddg_instant(
    query: str, session: aiohttp.ClientSession
) -> list[str]:
    facts: list[str] = []
    try:
        async with session.get(
            "https://api.duckduckgo.com/",
            params={
                "q": query,
                "format": "json",
                "no_html": "1",
                "skip_disambig": "1",
            },
            headers={"User-Agent": _BOT_UA},
            ssl=_SSL_CTX,
            timeout=aiohttp.ClientTimeout(total=8),
        ) as resp:
            if resp.status >= 400:
                return facts
            data = await resp.json(content_type=None)
    except Exception:
        log.debug("DuckDuckGo Instant failed", exc_info=True)
        return facts

    if not isinstance(data, dict):
        return facts
    abstract = (data.get("AbstractText") or "").strip()
    heading = (data.get("Heading") or "").strip()
    answer = _clean_html(str(data.get("Answer") or ""))
    if abstract:
        label = f" ({heading})" if heading else ""
        facts.append(f"DuckDuckGo{label}: {abstract}")
    elif answer:
        facts.append(f"DuckDuckGo answer: {answer}")
    return facts


async def _ddg_web_snippets(
    query: str, session: aiohttp.ClientSession, *, limit: int = 4
) -> list[str]:
    facts: list[str] = []
    try:
        async with session.post(
            "https://html.duckduckgo.com/html/",
            data={"q": query},
            headers={"User-Agent": _UA},
            ssl=_SSL_CTX,
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status >= 400:
                return facts
            html = await resp.text()
    except Exception:
        log.debug("DuckDuckGo HTML search failed", exc_info=True)
        return facts

    for m in re.finditer(
        r'class="result__a"[^>]*>(.*?)</a>.*?'
        r'class="result__snippet"[^>]*>(.*?)</(?:a|td)',
        html,
        re.S | re.I,
    ):
        title = _clean_html(m.group(1))
        snip = _clean_html(m.group(2))
        if not snip:
            continue
        # Prefer encyclopedic / reputable sources first
        low = f"{title} {snip}".lower()
        weight = 0
        if "wikipedia" in low or "britannica" in low:
            weight = 2
        elif any(x in low for x in ("imdb", "bbc", "reuters", "ap news")):
            weight = 1
        facts.append((weight, f"{title}: {snip}"))
        if len(facts) >= limit * 2:
            break

    facts.sort(key=lambda x: -x[0])
    return [t for _, t in facts[:limit]]


async def gather_web_facts(
    query: str,
    session: aiohttp.ClientSession,
    *,
    max_lines: int = 5,
) -> str:
    """
    Look up the query on Wikipedia + DuckDuckGo.
    Returns a plain-text FACTS block (may be empty if nothing found).
    """
    q = (query or "").strip()
    if not q:
        return ""

    # Prefer a clean entity query for Wikipedia ("Who is X?" → "X")
    entity = re.sub(
        r"(?i)^\s*(?:hey\s+)?(?:who|what|when|where)\s+(?:is|are|was|were)\s+",
        "",
        q,
    )
    entity = re.sub(
        r"(?i)^\s*(?:кто\s+так(?:ая|ой|ое)|что\s+так(?:ое|ой)|хто\s+так(?:а|ий))\s+",
        "",
        entity,
    )
    entity = entity.strip(" ?!.") or q

    wiki, instant, web = await _gather_parallel(entity, q, session)
    lines: list[str] = []
    seen: set[str] = set()

    def _add(items: list[str]) -> None:
        for item in items:
            key = item[:120].lower()
            if key in seen:
                continue
            seen.add(key)
            lines.append(item)
            if len(lines) >= max_lines:
                return

    _add(wiki)
    _add(instant)
    if len(lines) < 2:
        _add(web)
    elif len(lines) < max_lines:
        # One extra web snippet for corroboration
        _add(web[:1])

    if not lines:
        return ""
    return "WEB FACTS (verified lookup — use ONLY these; do not invent):\n" + "\n".join(
        f"- {ln}" for ln in lines
    )


async def _gather_parallel(
    entity: str, full_q: str, session: aiohttp.ClientSession
) -> tuple[list[str], list[str], list[str]]:
    import asyncio

    wiki_t = asyncio.create_task(_wikipedia_facts(entity, session))
    ddg_t = asyncio.create_task(_ddg_instant(entity, session))
    web_t = asyncio.create_task(_ddg_web_snippets(full_q, session, limit=4))
    wiki, instant, web = await asyncio.gather(
        wiki_t, ddg_t, web_t, return_exceptions=True
    )
    if isinstance(wiki, BaseException):
        log.debug("wiki task error: %s", wiki)
        wiki = []
    if isinstance(instant, BaseException):
        log.debug("ddg instant error: %s", instant)
        instant = []
    if isinstance(web, BaseException):
        log.debug("ddg web error: %s", web)
        web = []
    return wiki, instant, web
