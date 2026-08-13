"""Look up real games (Steam → official site → Wikipedia)."""

from __future__ import annotations

import logging
import json
import re
import ssl
from dataclasses import dataclass
from urllib.parse import quote, urlparse

import aiohttp
import certifi

log = logging.getLogger("dream_team.game_search")

_SSL_CTX = ssl.create_default_context(cafile=certifi.where())
_UA = "DreamTeamBot/1.0 (+discord; game catalog lookup)"
_HEADERS = {"User-Agent": _UA, "Accept": "application/json"}

_GAME_CUE = re.compile(
    r"\b("
    r"video\s*games?|videogames?|computer\s+games?|sandbox\s+game|"
    r"multiplayer\s+game|survival\s+game|battle\s+royale|"
    r"action-adventure\s+game|platform(?:er)?\s+game|roguelike|"
    r"first-person\s+shooter|third-person\s+shooter|action\s+role-playing|"
    r"free-to-play"
    r")\b",
    re.I,
)
_NOT_GAME = re.compile(
    r"\b("
    r"film|movie|television|tv\s+series|album|song|novel|book|"
    r"media\s+franchise|video\s+game\s+series|disambiguation|modding|"
    r"soundtrack|downloadable"
    r")\b",
    re.I,
)
_STEAM_SKIP = re.compile(
    r"\b(dlc|soundtrack|ost|bundle|pack|demo|trailer|adventure\s+pass|season\s+pass|"
    r"starter\s+pack|battle\s+pass|hero\s+collection|walkthrough)\b",
    re.I,
)
_SEQUEL_SUFFIX = re.compile(
    r"^(ii|iii|iv|v|vi|2|3|4|remastered|definitive|goty)$",
    re.I,
)
_LANG_PATH = re.compile(r"^/(en|es|fr|de|ja|ko|pt|ru|th|vi|it|tr|zh-tw|id)(/|$)")
_TRADEMARKS = re.compile(r"[®™©]")
_YEAR_VIDEO_GAME = re.compile(
    r"\b((19|20)\d{2}\s+video\s+game|video\s+game\s+\((19|20)\d{2}\))\b",
    re.I,
)

# Reliable official sites when Steam has no page (and Wikidata may rate-limit).
_KNOWN_OFFICIAL = {
    "minecraft": "https://www.minecraft.net/",
    "genshin impact": "https://genshin.hoyoverse.com/",
    "honkai star rail": "https://hsr.hoyoverse.com/",
    "zenless zone zero": "https://zenless.hoyoverse.com/",
    "roblox": "https://www.roblox.com/",
    "fortnite": "https://www.fortnite.com/",
    "league of legends": "https://www.leagueoflegends.com/",
    "valorant": "https://playvalorant.com/",
    "osu!": "https://osu.ppy.sh/",
    "osu": "https://osu.ppy.sh/",
}


@dataclass(frozen=True)
class GameHit:
    name: str
    url: str
    source: str
    snippet: str = ""
    app_id: int | None = None

    def as_dict(self) -> dict[str, str | int | None]:
        return {
            "name": self.name,
            "url": self.url,
            "source": self.source,
            "snippet": self.snippet,
            "app_id": self.app_id,
        }


def hit_from_dict(raw: dict) -> GameHit:
    app_id = raw.get("app_id")
    return GameHit(
        name=str(raw.get("name") or ""),
        url=str(raw.get("url") or ""),
        source=str(raw.get("source") or ""),
        snippet=str(raw.get("snippet") or ""),
        app_id=int(app_id) if app_id else None,
    )


def _key(name: str) -> str:
    cleaned = _TRADEMARKS.sub("", name or "")
    return " ".join(cleaned.strip().lower().split())


def _bare_key(name: str) -> str:
    k = _key(name)
    if k.startswith("the "):
        return k[4:]
    return k


def is_video_game_page(description: str, extract: str, title: str = "") -> bool:
    blob = f"{title} {description} {extract}"
    if not _GAME_CUE.search(blob):
        return False
    desc = description or ""
    if re.search(r"\b(media\s+franchise|video\s+game\s+series)\b", desc, re.I):
        return False
    if _NOT_GAME.search(desc) and not _GAME_CUE.search(desc):
        return False
    if re.search(r"\b(film|movie)\b", desc, re.I):
        return False
    if re.search(r"\(franchise\)", title, re.I):
        return False
    if re.search(r"\bmodding\b", title, re.I):
        return False
    return True


def steam_title_matches(query: str, steam_name: str) -> bool:
    """True when a Steam app is the same game, not a spin-off or pack."""
    qk = _bare_key(query)
    sk = _bare_key(steam_name)
    if not qk or not sk:
        return False
    if _STEAM_SKIP.search(steam_name):
        return False
    if qk == sk:
        return True
    # "Overwatch" ↔ "Overwatch 2"
    if sk.startswith(qk + " "):
        suffix = sk[len(qk) :].strip()
        return bool(_SEQUEL_SUFFIX.match(suffix))
    if qk.startswith(sk + " "):
        suffix = qk[len(sk) :].strip()
        return bool(_SEQUEL_SUFFIX.match(suffix))
    return False


def _query_variants(query: str) -> list[str]:
    q = " ".join((query or "").split())
    if len(q) < 2:
        return []
    out = [q]
    bare = _bare_key(q)
    if bare and bare != _key(q):
        out.append(bare)
    if not _key(q).startswith("the "):
        out.append(f"The {q}")
    # unique preserve order
    seen: set[str] = set()
    uniq: list[str] = []
    for item in out:
        k = _key(item)
        if k in seen:
            continue
        seen.add(k)
        uniq.append(item)
    return uniq


def prefer_official_url(urls: list[str]) -> str | None:
    """Pick one official site — prefer English / root domains."""
    cleaned: list[str] = []
    for raw in urls:
        url = (raw or "").strip()
        if not url or "wikipedia.org" in url.lower():
            continue
        cleaned.append(url)
    if not cleaned:
        return None

    def score(url: str) -> tuple:
        low = url.lower()
        path = urlparse(url).path or "/"
        return (
            0 if re.search(r"/en(/|$)", low) or path in ("/", "") else 1,
            0 if not _LANG_PATH.match(path) else 1,
            len(url),
        )

    return sorted(cleaned, key=score)[0]


def store_link_label(url: str) -> str:
    low = (url or "").lower()
    if "steampowered.com" in low or "steamcommunity.com" in low:
        return "Steam"
    if "wikipedia.org" in low:
        return "About"
    if any(
        host in low
        for host in (
            "minecraft.net",
            "hoyoverse.com",
            "mihoyo.com",
            "blizzard.com",
            "xbox.com",
            "epicgames.com",
            "store.playstation.com",
            "nintendo.com",
            "gog.com",
            "roblox.com",
            "fortnite.com",
            "leagueoflegends.com",
            "playvalorant.com",
            "osu.ppy.sh",
        )
    ):
        return "Website"
    return "Website"


def store_link_markdown(url: str | None) -> str:
    url = (url or "").strip()
    if not url:
        return ""
    return f"[{store_link_label(url)}]({url})"


def merge_game_hits(
    steam: list[GameHit], wiki: list[GameHit], query: str
) -> list[GameHit]:
    by_key: dict[str, GameHit] = {}
    for hit in steam + wiki:
        key = _key(hit.name)
        if not key:
            continue
        existing = by_key.get(key)
        if existing is None:
            by_key[key] = hit
            continue
        if existing.source != "steam" and hit.source == "steam":
            by_key[key] = hit
    qk = _key(query)

    def sort_key(hit: GameHit) -> tuple:
        nk = _key(hit.name)
        exact = 0 if nk == qk else 1
        prefix = 0 if nk.startswith(qk) or qk.startswith(nk) else 1
        source = 0 if hit.source == "steam" else (1 if hit.source == "website" else 2)
        return (exact, prefix, source, hit.name.lower())

    return sorted(by_key.values(), key=sort_key)[:15]


async def search_catalog_games(query: str) -> list[GameHit]:
    q = " ".join((query or "").split())
    if len(q) < 2:
        return []
    timeout = aiohttp.ClientTimeout(total=15)
    async with aiohttp.ClientSession(headers=_HEADERS, timeout=timeout) as session:
        steam = await _safe_steam(session, q)
        wiki = await _safe_wiki(session, q)
        hits = merge_game_hits(steam, wiki, q)
        # Enrich non-Steam hits with an official website when possible.
        out: list[GameHit] = []
        for hit in hits:
            if hit.source == "steam":
                out.append(hit)
                continue
            known = _KNOWN_OFFICIAL.get(_bare_key(hit.name))
            official = known or await _wikidata_official_site(session, hit.name)
            if official:
                out.append(
                    GameHit(
                        name=hit.name,
                        url=official,
                        source="website",
                        snippet=hit.snippet or "Official site",
                    )
                )
            else:
                out.append(hit)
        return out


async def resolve_game_link(game_name: str) -> GameHit | None:
    """Steam first (exact title), else official website, else Wikipedia."""
    q = " ".join((game_name or "").split())
    if len(q) < 2:
        return None
    timeout = aiohttp.ClientTimeout(total=20)
    async with aiohttp.ClientSession(headers=_HEADERS, timeout=timeout) as session:
        steam_hit = await _best_steam_match(session, q)
        if steam_hit is not None:
            return steam_hit

        # Wikidata first (2 requests) — avoid Wikipedia summaries until needed.
        qid = await _wikidata_game_id(session, q)
        if qid:
            official = await _p856_for_qid(session, qid)
            if official:
                return GameHit(
                    name=q,
                    url=official,
                    source="website",
                    snippet="Official site",
                )

        known = _KNOWN_OFFICIAL.get(_bare_key(q))
        if known:
            return GameHit(
                name=q,
                url=known,
                source="website",
                snippet="Official site",
            )

        wiki = await _safe_wiki(session, q)
        for hit in wiki:
            if _bare_key(hit.name) == _bare_key(q) or steam_title_matches(q, hit.name):
                official = await _official_site_for_wiki_title(session, hit.name)
                if official:
                    return GameHit(
                        name=hit.name,
                        url=official,
                        source="website",
                        snippet="Official site",
                    )
                return hit
        return wiki[0] if wiki else None


async def _best_steam_match(
    session: aiohttp.ClientSession, query: str
) -> GameHit | None:
    for variant in _query_variants(query):
        for hit in await _safe_steam(session, variant):
            if steam_title_matches(query, hit.name) or steam_title_matches(
                variant, hit.name
            ):
                return hit
    return None


async def _safe_steam(session: aiohttp.ClientSession, query: str) -> list[GameHit]:
    try:
        return await _search_steam(session, query)
    except Exception:
        log.warning("Steam game search failed for %r", query, exc_info=True)
        return []


async def _safe_wiki(session: aiohttp.ClientSession, query: str) -> list[GameHit]:
    try:
        return await _search_wikipedia(session, query)
    except Exception:
        log.warning("Wikipedia game search failed for %r", query, exc_info=True)
        return []


async def _search_steam(
    session: aiohttp.ClientSession, query: str
) -> list[GameHit]:
    async with session.get(
        "https://store.steampowered.com/api/storesearch/",
        params={"term": query, "l": "english", "cc": "us"},
        ssl=_SSL_CTX,
    ) as resp:
        if resp.status >= 400:
            return []
        data = await resp.json(content_type=None)
    if not isinstance(data, dict):
        return []
    hits: list[GameHit] = []
    for item in data.get("items") or []:
        if not isinstance(item, dict) or item.get("type") != "app":
            continue
        name = str(item.get("name") or "").strip()
        app_id = item.get("id")
        if not name or not app_id:
            continue
        if _STEAM_SKIP.search(name):
            continue
        hits.append(
            GameHit(
                name=name,
                url=f"https://store.steampowered.com/app/{int(app_id)}",
                source="steam",
                snippet="Steam",
                app_id=int(app_id),
            )
        )
        if len(hits) >= 10:
            break
    return _drop_steam_addons(hits)


def _drop_steam_addons(hits: list[GameHit]) -> list[GameHit]:
    names = [hit.name for hit in hits]
    kept: list[GameHit] = []
    for hit in hits:
        drop = False
        for other in names:
            if other == hit.name or not hit.name.startswith(other):
                continue
            suffix = hit.name[len(other) :].strip(" -:–")
            if suffix and not _SEQUEL_SUFFIX.match(suffix):
                drop = True
                break
        if not drop:
            kept.append(hit)
    return kept


async def _search_wikipedia(
    session: aiohttp.ClientSession, query: str
) -> list[GameHit]:
    titles = await _wiki_opensearch(session, query)
    extra = await _wiki_opensearch(session, f"{query} video game")
    seen: set[str] = set()
    ordered: list[str] = []
    for title in titles + extra:
        key = title.casefold()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(title)
    hits: list[GameHit] = []
    for title in ordered[:8]:
        hit = await _wiki_summary_if_game(session, title)
        if hit is None:
            continue
        hits.append(hit)
        if len(hits) >= 6:
            break
    return hits


async def _wiki_opensearch(
    session: aiohttp.ClientSession, query: str
) -> list[str]:
    async with session.get(
        "https://en.wikipedia.org/w/api.php",
        params={
            "action": "opensearch",
            "search": query,
            "limit": 6,
            "namespace": 0,
            "format": "json",
        },
        ssl=_SSL_CTX,
    ) as resp:
        if resp.status >= 400:
            return []
        data = await resp.json(content_type=None)
    if not isinstance(data, list) or len(data) < 2:
        return []
    return [str(t) for t in data[1] if t]


async def _wiki_summary_if_game(
    session: aiohttp.ClientSession, title: str
) -> GameHit | None:
    path = quote(title.replace(" ", "_"), safe="_()':,")
    async with session.get(
        f"https://en.wikipedia.org/api/rest_v1/page/summary/{path}",
        ssl=_SSL_CTX,
    ) as resp:
        if resp.status >= 400:
            return None
        page = await resp.json(content_type=None)
    if not isinstance(page, dict) or page.get("type") == "disambiguation":
        return None
    name = str(page.get("title") or title).strip()
    description = str(page.get("description") or "").strip()
    extract = str(page.get("extract") or "").strip()
    if not is_video_game_page(description, extract, name):
        return None
    urls = page.get("content_urls") or {}
    desktop = urls.get("desktop") if isinstance(urls, dict) else None
    url = ""
    if isinstance(desktop, dict):
        url = str(desktop.get("page") or "").strip()
    if not url:
        url = f"https://en.wikipedia.org/wiki/{path}"
    snippet = description or extract[:80]
    return GameHit(
        name=name,
        url=url,
        source="wikipedia",
        snippet=snippet,
    )


async def _wikidata_official_site(
    session: aiohttp.ClientSession, query: str
) -> str | None:
    qid = await _wikidata_game_id(session, query)
    if qid:
        url = await _p856_for_qid(session, qid)
        if url:
            return url
    known = _KNOWN_OFFICIAL.get(_bare_key(query))
    if known:
        return known
    return None


async def _official_site_for_wiki_title(
    session: aiohttp.ClientSession, title: str
) -> str | None:
    qid = await _wikidata_id_for_wiki_title(session, title)
    if not qid:
        return None
    return await _p856_for_qid(session, qid)


async def _wikidata_id_for_wiki_title(
    session: aiohttp.ClientSession, title: str
) -> str | None:
    try:
        async with session.get(
            "https://en.wikipedia.org/w/api.php",
            params={
                "action": "query",
                "prop": "pageprops",
                "ppprop": "wikibase_item",
                "titles": title,
                "format": "json",
            },
            ssl=_SSL_CTX,
        ) as resp:
            if resp.status >= 400:
                return None
            text = await resp.text()
    except Exception:
        log.debug("Wikipedia pageprops failed for %r", title, exc_info=True)
        return None
    if not text.lstrip().startswith("{"):
        return None
    try:
        data = json.loads(text)
    except Exception:
        return None
    pages = ((data.get("query") or {}).get("pages") or {})
    for page in pages.values():
        if not isinstance(page, dict):
            continue
        qid = ((page.get("pageprops") or {}).get("wikibase_item") or "").strip()
        if qid:
            return qid
    return None


async def _p856_for_qid(session: aiohttp.ClientSession, qid: str) -> str | None:
    try:
        async with session.get(
            f"https://www.wikidata.org/wiki/Special:EntityData/{qid}.json",
            ssl=_SSL_CTX,
        ) as resp:
            if resp.status >= 400:
                return None
            text = await resp.text()
    except Exception:
        log.debug("Wikidata entity fetch failed for %s", qid, exc_info=True)
        return None
    if not text.lstrip().startswith("{"):
        return None
    try:
        data = json.loads(text)
    except Exception:
        return None
    ent = (data.get("entities") or {}).get(qid) or {}
    claims = ent.get("claims") or {}
    urls: list[str] = []
    for claim in claims.get("P856") or []:
        value = ((claim.get("mainsnak") or {}).get("datavalue") or {}).get("value")
        if isinstance(value, str) and value.strip():
            urls.append(value.strip())
    return prefer_official_url(urls)


async def _wikidata_game_id(
    session: aiohttp.ClientSession, query: str
) -> str | None:
    try:
        async with session.get(
            "https://www.wikidata.org/w/api.php",
            params={
                "action": "wbsearchentities",
                "search": query,
                "language": "en",
                "uselang": "en",
                "type": "item",
                "limit": 8,
                "format": "json",
            },
            ssl=_SSL_CTX,
        ) as resp:
            if resp.status >= 400:
                return None
            text = await resp.text()
    except Exception:
        log.debug("Wikidata search failed for %r", query, exc_info=True)
        return None
    if not text.lstrip().startswith("{"):
        return None
    try:
        data = json.loads(text)
    except Exception:
        return None

    scored: list[tuple[int, str]] = []
    for item in data.get("search") or []:
        label = str(item.get("label") or "")
        desc = str(item.get("description") or "")
        qid = str(item.get("id") or "")
        if not qid:
            continue
        if re.search(r"\b(media\s+franchise|video\s+game\s+series)\b", desc, re.I):
            continue
        if not is_video_game_page(desc, "", label):
            continue
        if not (
            _bare_key(label) == _bare_key(query)
            or steam_title_matches(query, label)
            or _bare_key(query) in _bare_key(label)
        ):
            continue
        rank = 0 if _YEAR_VIDEO_GAME.search(desc) else 1
        if _bare_key(label) != _bare_key(query):
            rank += 2
        scored.append((rank, qid))
    if not scored:
        return None
    scored.sort(key=lambda x: x[0])
    return scored[0][1]
