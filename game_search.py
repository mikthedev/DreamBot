"""Look up real games (Steam + Wikipedia) so the catalog cannot be free-typed."""

from __future__ import annotations

import logging
import re
import ssl
from dataclasses import dataclass
from urllib.parse import quote

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
    r"first-person\s+shooter|third-person\s+shooter"
    r")\b",
    re.I,
)
_NOT_GAME = re.compile(
    r"\b("
    r"film|movie|television|tv\s+series|album|song|novel|book|"
    r"media\s+franchise|disambiguation|modding"
    r")\b",
    re.I,
)
_STEAM_SKIP = re.compile(
    r"\b(dlc|soundtrack|ost|bundle|pack|demo|trailer|adventure\s+pass|season\s+pass)\b",
    re.I,
)
_SEQUEL_SUFFIX = re.compile(
    r"^(ii|iii|iv|v|vi|2|3|4|remastered|definitive|goty)$",
    re.I,
)


def _key(name: str) -> str:
    return " ".join((name or "").strip().lower().split())


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


def is_video_game_page(description: str, extract: str, title: str = "") -> bool:
    blob = f"{title} {description} {extract}"
    if not _GAME_CUE.search(blob):
        return False
    # A page about a film of a game still mentions "video game" — reject if
    # the short description is clearly not the game itself.
    desc = description or ""
    if _NOT_GAME.search(desc) and not _GAME_CUE.search(desc):
        return False
    if re.search(r"\b(film|movie)\b", desc, re.I):
        return False
    if re.search(r"\(franchise\)", title, re.I):
        return False
    if re.search(r"\bmodding\b", title, re.I):
        return False
    return True


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
        source = 0 if hit.source == "steam" else 1
        return (exact, prefix, source, hit.name.lower())

    return sorted(by_key.values(), key=sort_key)[:15]


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
            "xbox.com",
            "epicgames.com",
            "store.playstation.com",
            "nintendo.com",
            "gog.com",
        )
    ):
        return "Store"
    return "Link"


def store_link_markdown(url: str | None) -> str:
    url = (url or "").strip()
    if not url:
        return ""
    return f"[{store_link_label(url)}]({url})"


async def search_catalog_games(query: str) -> list[GameHit]:
    q = " ".join((query or "").split())
    if len(q) < 2:
        return []
    timeout = aiohttp.ClientTimeout(total=12)
    async with aiohttp.ClientSession(headers=_HEADERS, timeout=timeout) as session:
        steam, wiki = await _gather_sources(session, q)
    return merge_game_hits(steam, wiki, q)


async def _gather_sources(
    session: aiohttp.ClientSession, query: str
) -> tuple[list[GameHit], list[GameHit]]:
    steam: list[GameHit] = []
    wiki: list[GameHit] = []
    try:
        steam = await _search_steam(session, query)
    except Exception:
        log.warning("Steam game search failed for %r", query, exc_info=True)
    try:
        wiki = await _search_wikipedia(session, query)
    except Exception:
        log.warning("Wikipedia game search failed for %r", query, exc_info=True)
    return steam, wiki


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
    """Keep sequels, drop 'Base Game Extra Words' DLC sitting next to the base game."""
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
