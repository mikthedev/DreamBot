from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import shutil
import ssl
import time
import traceback
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import aiohttp
import certifi
import discord
from discord import app_commands
from discord.ext import commands, tasks
from rich_presence import (
    presence_between_tracks,
    presence_idle,
    presence_now_playing,
    update_presence as apply_rich_presence,
)
import config

log = logging.getLogger("dream_team.music")

AUDIO_CACHE_DIR = config.DATA_DIR / "music_cache"
AUDIO_CACHE_DIR.mkdir(parents=True, exist_ok=True)

_SSL_CTX = ssl.create_default_context(cafile=certifi.where())
_SPOTIFY_HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}
_SPOTIFY_KIND_RE = re.compile(
    r"(?:https?://(?:open|play)\.spotify\.com/(?:intl-[a-z]{2}/)?|spotify:)"
    r"(track|album|playlist|episode|show)[/:]([A-Za-z0-9]+)",
    re.IGNORECASE,
)
_SPOTIFY_SHORT_HOSTS = {"spotify.link", "spotify.app.link", "spoti.fi"}
_SPOTIFY_PLAYLIST_CAP = 50
_BRIDGE_SEARCH_CANDIDATES = 8
_BRIDGE_NOISE = (
    "remix",
    "cover",
    "karaoke",
    "nightcore",
    "sped up",
    "speed up",
    "slowed",
    "reverb",
    "8d audio",
    "mashup",
    "bootleg",
    "live at",
    "live session",
    "acoustic version",
    "piano version",
    "instrumental",
)


def _yt_dlp():
    """Lazy import — yt-dlp is ~20 MB RSS and only needed when someone /play's."""
    import yt_dlp

    return yt_dlp


class _YTDLLogger:
    """Pipe yt-dlp messages into our logger."""

    def debug(self, msg: str) -> None:
        log.debug("yt-dlp: %s", msg)

    def info(self, msg: str) -> None:
        log.info("yt-dlp: %s", msg)

    def warning(self, msg: str) -> None:
        log.warning("yt-dlp: %s", msg)

    def error(self, msg: str) -> None:
        log.error("yt-dlp: %s", msg)


YTDL_OPTS = {
    "format": "bestaudio/best",
    "quiet": True,
    "no_warnings": False,
    "logger": _YTDLLogger(),
    # SoundCloud search for bare text — YouTube is not supported
    "default_search": "scsearch1",
    "noplaylist": True,
    "extract_flat": False,
    "ignore_no_formats_error": True,
}


def _ytdl_opts(**extra) -> dict:
    return {**YTDL_OPTS, **extra}


def _unwrap_info(info: dict) -> dict:
    if info is None:
        raise ValueError("Nothing found for that link/search.")
    if "entries" in info:
        entries = [e for e in info["entries"] if e]
        if not entries:
            raise ValueError("Nothing found for that search.")
        return entries[0]
    return info


def _format_media_url(fmt: dict) -> str | None:
    for key in ("url", "manifest_url"):
        val = fmt.get(key)
        if isinstance(val, str) and val.startswith("http"):
            return val
    return None


def _is_playable_format(fmt: dict) -> bool:
    if not isinstance(fmt, dict) or not _format_media_url(fmt):
        return False
    ext = (fmt.get("ext") or "").lower()
    if ext in {"mhtml", "storyboard"}:
        return False
    protocol = (fmt.get("protocol") or "").lower()
    if protocol.startswith("mhtml") or "storyboard" in protocol:
        return False
    return True


def _pick_stream_url(info: dict) -> str:
    """Pick any playable format with a direct URL (audio-only preferred)."""
    formats = [f for f in (info.get("formats") or []) if _is_playable_format(f)]
    direct = info.get("url")
    if not formats:
        if (
            isinstance(direct, str)
            and direct.startswith("http")
            and not direct.endswith((".jpg", ".webp", ".png"))
        ):
            return direct
        raise ValueError("Could not get an audio stream for that SoundCloud track.")

    def score(fmt: dict) -> tuple:
        vcodec = (fmt.get("vcodec") or "none").lower()
        audio_only = 1 if vcodec in {"none", "n/a"} else 0
        abr = fmt.get("abr") or fmt.get("tbr") or 0
        return (audio_only, float(abr))

    best = max(formats, key=score)
    url = _format_media_url(best)
    assert url is not None
    return url


def _extract_info(query: str, *, for_stream: bool) -> dict:
    yt_dlp = _yt_dlp()
    opts = _ytdl_opts(skip_download=True)
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = _unwrap_info(ydl.extract_info(query, download=False))
    if for_stream:
        _pick_stream_url(info)
    return info


_HTTP_HEADERS = (
    "User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36\r\n"
    "Accept: */*\r\n"
    "Referer: https://soundcloud.com/\r\n"
    "Origin: https://soundcloud.com\r\n"
)

SOURCE_COLORS = {
    "soundcloud": discord.Color.from_rgb(232, 98, 42),
    "spotify": discord.Color.from_rgb(30, 215, 96),
}

BRAND_COLOR = discord.Color.from_rgb(14, 28, 48)
ACCENT_TEAL = discord.Color.from_rgb(46, 230, 166)


def _ffmpeg_executable() -> str:
    found = shutil.which("ffmpeg")
    if found:
        return found
    for candidate in (
        "/opt/homebrew/bin/ffmpeg",
        "/usr/local/bin/ffmpeg",
        "/usr/bin/ffmpeg",
    ):
        if Path(candidate).is_file():
            return candidate
    return "ffmpeg"


FFMPEG_EXECUTABLE = _ffmpeg_executable()
# Keep reconnect flags conservative — old host ffmpeg builds reject unknown opts.
FFMPEG_BEFORE = (
    "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 "
    f"-headers '{_HTTP_HEADERS}'"
)
# PCM is more reliable than FFmpegOpusAudio+Ogg for HLS/AAC inputs.
FFMPEG_OPTIONS = "-vn"


def _ensure_opus() -> bool:
    if discord.opus.is_loaded():
        return True
    for name in (
        "opus",
        "libopus.so.0",
        "libopus.dylib",
        "/usr/lib/x86_64-linux-gnu/libopus.so.0",
        "/usr/lib/aarch64-linux-gnu/libopus.so.0",
        "/opt/homebrew/lib/libopus.dylib",
        "/usr/local/lib/libopus.dylib",
    ):
        try:
            discord.opus.load_opus(name)
            if discord.opus.is_loaded():
                log.info("Loaded Opus library via %s", name)
                return True
        except Exception:
            continue
    return discord.opus.is_loaded()


def _voice_stack_ok() -> tuple[bool, str]:
    """Discord DAVE requires discord.py >= 2.7 or clients hear silence."""
    ver = tuple(int(p) for p in discord.__version__.split(".")[:2])
    try:
        import davey  # noqa: F401
        has_davey = True
    except Exception:
        has_davey = False
    opus_ok = _ensure_opus()
    ok = ver >= (2, 7) and has_davey
    detail = (
        f"discord.py={discord.__version__} davey={'yes' if has_davey else 'NO'} "
        f"opus={'yes' if opus_ok else 'NO'} ffmpeg={FFMPEG_EXECUTABLE}"
    )
    return ok, detail


@dataclass
class Track:
    title: str
    webpage_url: str
    requester: discord.Member
    source: str
    query: str
    thumbnail: str | None = None
    uploader: str | None = None
    duration: int | None = None
    started_at: float | None = field(default=None, repr=False)


def _detect_source(query: str) -> str:
    lower = query.lower().strip()
    if "soundcloud.com" in lower or "snd.sc/" in lower:
        return "soundcloud"
    if _is_spotify_query(lower):
        return "spotify"
    if (
        "youtube.com" in lower
        or "youtu.be" in lower
        or "music.youtube.com" in lower
    ):
        return "youtube"
    return "search"


def _is_spotify_query(query: str) -> bool:
    lower = query.lower().strip()
    if lower.startswith("spotify:"):
        return True
    try:
        host = (urlparse(lower).hostname or "").lower()
    except Exception:
        host = ""
    if host in _SPOTIFY_SHORT_HOSTS:
        return True
    if "spotify.com" in lower:
        return True
    return False


def _reject_unsupported(source: str) -> None:
    if source == "youtube":
        raise ValueError(
            "YouTube isn’t supported anymore — paste a SoundCloud / Spotify "
            "link or search by track/artist name."
        )


def _parse_spotify_ref(query: str) -> tuple[str, str] | None:
    match = _SPOTIFY_KIND_RE.search(query.strip())
    if not match:
        return None
    return match.group(1).lower(), match.group(2)


async def _follow_spotify_short_link(url: str) -> str:
    timeout = aiohttp.ClientTimeout(total=20)
    connector = aiohttp.TCPConnector(ssl=_SSL_CTX)
    async with aiohttp.ClientSession(
        connector=connector, headers=_SPOTIFY_HTTP_HEADERS, timeout=timeout
    ) as session:
        async with session.get(url, allow_redirects=True) as resp:
            return str(resp.url)


async def _spotify_embed_entity(kind: str, spotify_id: str) -> dict[str, Any]:
    url = f"https://open.spotify.com/embed/{kind}/{spotify_id}"
    timeout = aiohttp.ClientTimeout(total=20)
    connector = aiohttp.TCPConnector(ssl=_SSL_CTX)
    async with aiohttp.ClientSession(
        connector=connector, headers=_SPOTIFY_HTTP_HEADERS, timeout=timeout
    ) as session:
        async with session.get(url) as resp:
            text = await resp.text()
            if resp.status >= 400:
                raise ValueError(
                    f"Spotify couldn’t open that {kind} (HTTP {resp.status})."
                )

    match = re.search(
        r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', text, re.S
    )
    if not match:
        raise ValueError("Spotify returned an unexpected page for that link.")
    try:
        payload = json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        raise ValueError("Couldn’t parse Spotify track info.") from exc

    page = payload.get("props", {}).get("pageProps", {})
    state = page.get("state")
    if not state:
        status = page.get("status")
        raise ValueError(
            f"Spotify couldn’t find that {kind}"
            + (f" (HTTP {status})." if status else ".")
        )
    entity = (state.get("data") or {}).get("entity")
    if not isinstance(entity, dict):
        raise ValueError("Spotify didn’t return track details for that link.")
    return entity


def _spotify_thumb(entity: dict[str, Any]) -> str | None:
    images = (entity.get("visualIdentity") or {}).get("image") or []
    if isinstance(images, list) and images:
        best = max(
            (img for img in images if isinstance(img, dict) and img.get("url")),
            key=lambda img: (img.get("maxHeight") or 0) * (img.get("maxWidth") or 0),
            default=None,
        )
        if best and isinstance(best.get("url"), str):
            return best["url"]
    return None


def _spotify_duration_sec(raw: Any) -> int | None:
    try:
        ms = int(raw)
    except (TypeError, ValueError):
        return None
    if ms <= 0:
        return None
    return max(1, ms // 1000)


@dataclass
class _SpotifySong:
    title: str
    artist: str | None
    track_id: str
    duration: int | None = None
    thumbnail: str | None = None


def _spotify_songs_from_entity(entity: dict[str, Any]) -> list[_SpotifySong]:
    kind = (entity.get("type") or "").lower()
    cover = _spotify_thumb(entity)

    if kind == "track":
        artists = entity.get("artists") or []
        artist_names = [
            a.get("name")
            for a in artists
            if isinstance(a, dict) and a.get("name")
        ]
        track_id = entity.get("id") or ""
        uri = entity.get("uri") or ""
        if not track_id and uri.startswith("spotify:track:"):
            track_id = uri.rsplit(":", 1)[-1]
        title = entity.get("title") or entity.get("name") or "Unknown track"
        if not track_id:
            raise ValueError("Spotify track is missing an id.")
        return [
            _SpotifySong(
                title=str(title),
                artist=", ".join(artist_names) if artist_names else None,
                track_id=track_id,
                duration=_spotify_duration_sec(entity.get("duration")),
                thumbnail=cover,
            )
        ]

    if kind in {"playlist", "album"}:
        rows = entity.get("trackList") or []
        if not isinstance(rows, list) or not rows:
            raise ValueError(f"That Spotify {kind} has no playable tracks.")
        songs: list[_SpotifySong] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            uri = str(row.get("uri") or "")
            if not uri.startswith("spotify:track:"):
                continue
            track_id = uri.rsplit(":", 1)[-1]
            title = row.get("title") or row.get("name") or "Unknown track"
            artist = row.get("subtitle") or None
            songs.append(
                _SpotifySong(
                    title=str(title),
                    artist=str(artist) if artist else None,
                    track_id=track_id,
                    duration=_spotify_duration_sec(row.get("duration")),
                    thumbnail=cover,
                )
            )
            if len(songs) >= _SPOTIFY_PLAYLIST_CAP:
                break
        if not songs:
            raise ValueError(f"No songs found in that Spotify {kind}.")
        return songs

    raise ValueError(
        "Only Spotify tracks, albums, and playlists are supported "
        "(not podcasts/episodes)."
    )


async def _resolve_spotify_songs(query: str) -> list[_SpotifySong]:
    text = query.strip()
    host = (urlparse(text).hostname or "").lower()
    if host in _SPOTIFY_SHORT_HOSTS:
        text = await _follow_spotify_short_link(text)

    parsed = _parse_spotify_ref(text)
    if parsed is None:
        raise ValueError(
            "That doesn’t look like a Spotify track/album/playlist link."
        )
    kind, spotify_id = parsed
    if kind in {"episode", "show"}:
        raise ValueError(
            "Spotify podcasts aren’t supported — paste a song, album, or playlist."
        )

    entity = await _spotify_embed_entity(kind, spotify_id)
    return _spotify_songs_from_entity(entity)


def _match_query(title: str, artist: str | None) -> str:
    title = (title or "").strip()
    artist = (artist or "").strip()
    if artist and title:
        return f"{artist} - {title}"
    return title or artist


def _encode_spotbridge(song: "_SpotifySong") -> str:
    # Keep Spotify identity for lazy playlist resolves.
    artist = (song.artist or "").replace("|", "/")
    title = (song.title or "").replace("|", "/")
    return (
        f"spotbridge:{song.track_id}|{song.duration or ''}|{artist}|{title}"
    )


def _decode_spotbridge(query: str) -> "_SpotifySong":
    raw = query.removeprefix("spotbridge:")
    parts = raw.split("|", 3)
    if len(parts) < 4:
        raise ValueError("Bad Spotify bridge payload.")
    track_id, dur_s, artist, title = parts
    duration = int(dur_s) if dur_s.isdigit() else None
    return _SpotifySong(
        title=title,
        artist=artist or None,
        track_id=track_id,
        duration=duration,
    )


def _bridge_candidate_score(
    entry: dict,
    *,
    title: str,
    artist: str | None,
    duration: int | None,
) -> int:
    """Higher is better — prefer official uploads close to Spotify duration."""
    etitle = (entry.get("title") or "").lower()
    echan = (
        entry.get("uploader")
        or entry.get("channel")
        or entry.get("creator")
        or ""
    ).lower()
    want_title = (title or "").lower()
    want_artist = (artist or "").lower()

    score = 0
    if want_title and want_title in etitle:
        score += 60
    for tok in re.split(r"\s+", want_title):
        if len(tok) > 2 and tok in etitle:
            score += 4
    if want_artist and want_artist in etitle:
        score += 25
    if want_artist and want_artist in echan:
        score += 35
    for badge in ("topic", "vevo", "official", "records", "music"):
        if badge in echan:
            score += 12
    if "official audio" in etitle or "official video" in etitle:
        score += 20

    for word in _BRIDGE_NOISE:
        if word in etitle and word not in want_title:
            score -= 55

    edur = entry.get("duration")
    try:
        edur_i = int(edur) if edur is not None else None
    except (TypeError, ValueError):
        edur_i = None
    if duration and edur_i:
        diff = abs(edur_i - int(duration))
        if diff <= 2:
            score += 40
        elif diff <= 6:
            score += 22
        elif diff <= 12:
            score += 8
        elif diff > 40:
            score -= 35
    return score


def _search_flat_entries(query: str) -> list[dict]:
    yt_dlp = _yt_dlp()
    opts = _ytdl_opts(
        skip_download=True,
        extract_flat="in_playlist",
        noplaylist=False,
        ignoreerrors=True,
        playlistend=_BRIDGE_SEARCH_CANDIDATES,
        default_search=None,
    )
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(query, download=False)
    if not info:
        return []
    entries = info.get("entries") if isinstance(info, dict) else None
    if not entries:
        return [info] if isinstance(info, dict) else []
    return [e for e in entries if isinstance(e, dict)]


def _entry_url(entry: dict) -> str | None:
    for key in ("webpage_url", "url", "original_url"):
        val = entry.get(key)
        if isinstance(val, str) and val.startswith("http"):
            return val
    entry_id = entry.get("id")
    ie = (entry.get("ie_key") or entry.get("extractor_key") or "").lower()
    if entry_id and "youtube" in ie:
        return f"https://www.youtube.com/watch?v={entry_id}"
    return None


def _extract_bridge_info(url: str) -> dict:
    """Extract playable info; YouTube gets mobile clients to avoid JS challenges."""
    extra: dict[str, Any] = {}
    if "youtube.com" in url or "youtu.be" in url:
        extra["extractor_args"] = {
            "youtube": {"player_client": ["android", "ios", "web"]}
        }
    return _extract_info(url, for_stream=True) if not extra else _unwrap_and_check(
        url, **extra
    )


def _unwrap_and_check(query: str, **extra: Any) -> dict:
    yt_dlp = _yt_dlp()
    opts = _ytdl_opts(skip_download=True, **extra)
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = _unwrap_info(ydl.extract_info(query, download=False))
    _pick_stream_url(info)
    return info


def _bridge_from_platform(
    *,
    platform: str,
    title: str,
    artist: str | None,
    duration: int | None,
) -> tuple[str, dict]:
    """Search one platform and pick the best playable match."""
    base = _match_query(title, artist)
    if platform == "youtube":
        searches = [
            f"ytsearch{_BRIDGE_SEARCH_CANDIDATES}:{base}",
            f"ytsearch{_BRIDGE_SEARCH_CANDIDATES}:{base} official audio",
        ]
    else:
        searches = [
            f"scsearch{_BRIDGE_SEARCH_CANDIDATES}:{base}",
        ]

    ranked: list[tuple[int, str, dict]] = []
    for search in searches:
        try:
            entries = _search_flat_entries(search)
        except Exception as exc:
            log.info("%s search failed for %r: %r", platform, search, exc)
            continue
        for entry in entries:
            url = _entry_url(entry)
            if not url:
                continue
            score = _bridge_candidate_score(
                entry, title=title, artist=artist, duration=duration
            )
            ranked.append((score, url, entry))

    ranked.sort(key=lambda row: row[0], reverse=True)
    last_error: Exception | None = None
    seen: set[str] = set()
    for score, url, flat in ranked:
        if url in seen or score < -20:
            continue
        seen.add(url)
        try:
            if platform == "youtube":
                info = _extract_bridge_info(url)
            else:
                info = _extract_info(url, for_stream=True)
                _pick_stream_url(info)
            # Re-check with full metadata (duration is often missing in flat results).
            full_score = _bridge_candidate_score(
                info, title=title, artist=artist, duration=duration
            )
            if full_score < 0:
                log.info(
                    "Skipping weak %s match score=%s title=%r",
                    platform,
                    full_score,
                    info.get("title"),
                )
                continue
            webpage = (
                info.get("webpage_url")
                or info.get("original_url")
                or url
            )
            log.info(
                "Spotify bridge via %s score=%s → %s",
                platform,
                full_score,
                info.get("title"),
            )
            return str(webpage), info
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            log.info("%s candidate failed (%s): %r", platform, url[:80], exc)
            continue

    detail = f" ({last_error})" if last_error else ""
    raise ValueError(f"No good {platform} match for “{base}”.{detail}")


def _bridge_spotify_song(song: "_SpotifySong") -> tuple[str, dict]:
    """
    Spotify can't stream DRM audio. Match the Spotify track on YouTube first
    (much more accurate), then fall back to SoundCloud.
    """
    errors: list[str] = []
    for platform in ("youtube", "soundcloud"):
        try:
            return _bridge_from_platform(
                platform=platform,
                title=song.title,
                artist=song.artist,
                duration=song.duration,
            )
        except Exception as exc:
            errors.append(f"{platform}: {exc}")
            log.info("Spotify→%s bridge missed for %s: %r", platform, song.title, exc)
    raise ValueError(
        "Couldn’t match that Spotify track on YouTube or SoundCloud. "
        + " | ".join(errors)
    )


def _pick_thumbnail(info: dict) -> str | None:
    thumb = info.get("thumbnail")
    if isinstance(thumb, str) and thumb.startswith("http"):
        return thumb
    thumbs = info.get("thumbnails") or []
    if isinstance(thumbs, list) and thumbs:
        best = max(
            (t for t in thumbs if isinstance(t, dict) and t.get("url")),
            key=lambda t: (t.get("height") or 0) * (t.get("width") or 0),
            default=None,
        )
        if best:
            return best.get("url")
    return None


def _format_duration(seconds: int | None) -> str:
    if not seconds or seconds <= 0:
        return "Live / unknown"
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def _source_label(source: str) -> str:
    if source == "soundcloud":
        return "SoundCloud"
    if source == "spotify":
        return "Spotify"
    return source.title()


def _clip(text: str, limit: int) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _duration_int(raw: Any) -> int | None:
    try:
        return int(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def _track_from_soundcloud_info(
    info: dict,
    *,
    requester: discord.Member,
    source: str,
    webpage_url: str,
    query: str,
    title: str | None = None,
    uploader: str | None = None,
    thumbnail: str | None = None,
    duration: int | None = None,
) -> Track:
    return Track(
        title=title or info.get("title") or "Unknown track",
        webpage_url=webpage_url,
        requester=requester,
        source=source,
        query=query,
        thumbnail=thumbnail or _pick_thumbnail(info),
        uploader=uploader
        or info.get("uploader")
        or info.get("channel")
        or info.get("creator"),
        duration=duration
        if duration is not None
        else _duration_int(info.get("duration")),
    )


async def _tracks_from_spotify(
    query: str, requester: discord.Member
) -> list[Track]:
    songs = await _resolve_spotify_songs(query)
    tracks: list[Track] = []

    # Resolve the first song now so /play fails fast if nothing is playable.
    first = songs[0]
    media_url, media_info = await asyncio.to_thread(_bridge_spotify_song, first)
    tracks.append(
        _track_from_soundcloud_info(
            media_info,
            requester=requester,
            source="spotify",
            webpage_url=f"https://open.spotify.com/track/{first.track_id}",
            query=media_url,
            title=first.title,
            uploader=first.artist,
            thumbnail=first.thumbnail,
            duration=first.duration,
        )
    )

    # Remaining playlist/album songs bridge lazily at play time.
    for song in songs[1:]:
        tracks.append(
            Track(
                title=song.title,
                webpage_url=f"https://open.spotify.com/track/{song.track_id}",
                requester=requester,
                source="spotify",
                query=_encode_spotbridge(song),
                thumbnail=song.thumbnail,
                uploader=song.artist,
                duration=song.duration,
            )
        )
    return tracks


async def resolve_tracks(query: str, requester: discord.Member) -> list[Track]:
    source = _detect_source(query)
    _reject_unsupported(source)

    if source == "spotify":
        return await _tracks_from_spotify(query, requester)

    info = await asyncio.to_thread(_extract_info, query, for_stream=True)
    webpage = info.get("webpage_url") or info.get("original_url") or query
    extractor = (info.get("extractor") or "").lower()
    resolved_source = source
    if source == "search":
        if "soundcloud" not in extractor:
            raise ValueError(
                "Search only looks on SoundCloud right now. "
                "Try a SoundCloud / Spotify URL or different keywords."
            )
        resolved_source = "soundcloud"

    return [
        _track_from_soundcloud_info(
            info,
            requester=requester,
            source=resolved_source,
            webpage_url=str(webpage),
            query=str(webpage),
        )
    ]


async def resolve_track(query: str, requester: discord.Member) -> Track:
    tracks = await resolve_tracks(query, requester)
    return tracks[0]


async def resolve_stream_url(track: Track) -> str:
    def extract_url() -> tuple[str, str | None]:
        query = track.query
        if query.startswith("spotbridge:") or query.startswith("scbridge:"):
            if query.startswith("spotbridge:"):
                song = _decode_spotbridge(query)
                media_url, info = _bridge_spotify_song(song)
            else:
                # Legacy playlist items from older builds.
                song = _SpotifySong(
                    title=query.removeprefix("scbridge:"),
                    artist=None,
                    track_id="unknown",
                )
                media_url, info = _bridge_spotify_song(song)
            return _pick_stream_url(info), media_url

        if "youtube.com" in query or "youtu.be" in query:
            info = _extract_bridge_info(query)
            return _pick_stream_url(info), None

        info = _extract_info(query, for_stream=True)
        return _pick_stream_url(info), None

    stream_url, cached_query = await asyncio.to_thread(extract_url)
    if cached_query:
        track.query = cached_query
    return stream_url


def _cache_stem(query: str) -> str:
    return hashlib.sha1(query.encode("utf-8")).hexdigest()[:20]


def _download_audio_file(query: str) -> Path:
    """Download a playable local file via yt-dlp (avoids flaky live HLS in Discord)."""
    stem = _cache_stem(query)
    existing = list(AUDIO_CACHE_DIR.glob(f"{stem}.*"))
    for path in existing:
        if path.is_file() and path.stat().st_size > 10_000:
            return path

    outtmpl = str(AUDIO_CACHE_DIR / f"{stem}.%(ext)s")
    yt_dlp = _yt_dlp()
    opts = _ytdl_opts(
        outtmpl=outtmpl,
        quiet=True,
        noplaylist=True,
        overwrites=True,
        # Keep container ffmpeg can decode without post-processing plugins.
        format="bestaudio[ext=m4a]/bestaudio[ext=mp3]/bestaudio/best",
    )
    if "youtube.com" in query or "youtu.be" in query:
        opts["extractor_args"] = {
            "youtube": {"player_client": ["android", "ios", "web"]}
        }
    # Drop extract flags that skip writing a file.
    opts.pop("skip_download", None)
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = _unwrap_info(ydl.extract_info(query, download=True))

    requested = info.get("requested_downloads") or []
    if requested and isinstance(requested[0], dict):
        filepath = requested[0].get("filepath")
        if filepath and Path(filepath).is_file():
            return Path(filepath)

    # Fallback: whatever landed with our stem.
    matches = sorted(
        AUDIO_CACHE_DIR.glob(f"{stem}.*"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for path in matches:
        if path.is_file() and path.stat().st_size > 10_000:
            return path

    raise ValueError(f"Download produced no audio file for {query}")


async def prepare_playable_path(track: Track) -> Path:
    """Resolve Spotify bridge (YouTube→SoundCloud) and download a local file."""

    def work() -> Path:
        query = track.query
        if query.startswith("spotbridge:") or query.startswith("scbridge:"):
            if query.startswith("spotbridge:"):
                song = _decode_spotbridge(query)
            else:
                song = _SpotifySong(
                    title=query.removeprefix("scbridge:"),
                    artist=track.uploader,
                    track_id="unknown",
                    duration=track.duration,
                )
            media_url, _info = _bridge_spotify_song(song)
            track.query = media_url
            query = media_url
        return _download_audio_file(query)

    return await asyncio.to_thread(work)


def make_audio_source(path_or_url: str) -> discord.AudioSource:
    # Local file: no reconnect/headers needed. Remote URL: keep stream flags.
    is_remote = path_or_url.startswith("http://") or path_or_url.startswith("https://")
    before = FFMPEG_BEFORE if is_remote else None
    source = discord.FFmpegPCMAudio(
        path_or_url,
        executable=FFMPEG_EXECUTABLE,
        before_options=before,
        options=FFMPEG_OPTIONS,
    )
    # Slight boost — Discord + PCM often feels quiet otherwise.
    return discord.PCMVolumeTransformer(source, volume=1.0)


def _dave_debug(conn: Any) -> str:
    session = getattr(conn, "dave_session", None)
    if session is None:
        return "session=None"
    try:
        return (
            f"ready={session.ready} status={session.status} "
            f"epoch={session.epoch} sess_can_encrypt={session.can_encrypt}"
        )
    except Exception as exc:
        return f"session_err={exc!r}"


async def _wait_for_dave(voice: discord.VoiceClient, *, timeout: float = 20.0) -> bool:
    """Discord drops unencrypted voice; wait until DAVE can encrypt packets."""
    conn = getattr(voice, "_connection", None)
    if conn is None:
        return False
    deadline = time.monotonic() + timeout
    last_log = 0.0
    while time.monotonic() < deadline:
        # can_encrypt is a @property on VoiceConnectionState (bool, not callable)
        can = bool(getattr(conn, "can_encrypt", False))
        ver = getattr(conn, "dave_protocol_version", None)
        if can:
            log.info("DAVE ready (protocol=%s %s)", ver, _dave_debug(conn))
            return True
        now = time.monotonic()
        if now - last_log >= 3.0:
            log.info(
                "DAVE waiting… protocol=%s can_encrypt=%s %s",
                ver,
                can,
                _dave_debug(conn),
            )
            last_log = now
            # Nudge a stuck pending session once.
            session = getattr(conn, "dave_session", None)
            if session is not None and not session.ready:
                try:
                    session.prepare_transition(0, int(ver or 1))
                    session.execute_transition(0)
                except Exception:
                    pass
        await asyncio.sleep(0.15)
    log.warning(
        "DAVE not ready after %.1fs (protocol=%s can_encrypt=%s %s) — audio may be silent",
        timeout,
        getattr(conn, "dave_protocol_version", None),
        bool(getattr(conn, "can_encrypt", False)),
        _dave_debug(conn),
    )
    return False


class MusicControlsView(discord.ui.View):
    """Persistent Stop / Queue / Skip controls on now-playing cards."""

    def __init__(self) -> None:
        super().__init__(timeout=None)

    @staticmethod
    def _cog(interaction: discord.Interaction) -> "MusicCog | None":
        return interaction.client.get_cog("MusicCog")  # type: ignore[return-value]

    @discord.ui.button(
        label="Stop",
        style=discord.ButtonStyle.danger,
        custom_id="dream_team:music:stop",
    )
    async def stop_button(
        self, interaction: discord.Interaction, _button: discord.ui.Button
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                "Use this in a server.", ephemeral=True
            )
            return
        cog = self._cog(interaction)
        if cog is None:
            await interaction.response.send_message(
                "Music isn’t loaded.", ephemeral=True
            )
            return
        await cog.get_player(interaction.guild.id).stop()
        await interaction.response.send_message(
            "Stopped and left the voice channel.", ephemeral=True
        )

    @discord.ui.button(
        label="Queue",
        style=discord.ButtonStyle.secondary,
        custom_id="dream_team:music:queue",
    )
    async def queue_button(
        self, interaction: discord.Interaction, _button: discord.ui.Button
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                "Use this in a server.", ephemeral=True
            )
            return
        cog = self._cog(interaction)
        if cog is None:
            await interaction.response.send_message(
                "Music isn’t loaded.", ephemeral=True
            )
            return
        player = cog.get_player(interaction.guild.id)
        if not player.current and not player.queue:
            await interaction.response.send_message(
                "Queue is empty.", ephemeral=True
            )
            return
        await interaction.response.send_message(
            embed=queue_embed(player), ephemeral=True
        )

    @discord.ui.button(
        label="Skip",
        style=discord.ButtonStyle.primary,
        custom_id="dream_team:music:skip",
    )
    async def skip_button(
        self, interaction: discord.Interaction, _button: discord.ui.Button
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                "Use this in a server.", ephemeral=True
            )
            return
        cog = self._cog(interaction)
        if cog is None:
            await interaction.response.send_message(
                "Music isn’t loaded.", ephemeral=True
            )
            return
        player = cog.get_player(interaction.guild.id)
        skipped = await player.skip()
        if skipped is None and not player.queue:
            await interaction.response.send_message(
                "Nothing to skip.", ephemeral=True
            )
            return
        title = skipped.title if skipped else "current track"
        await interaction.response.send_message(
            f"Skipped **{title}**.", ephemeral=True
        )


def track_embed(
    track: Track,
    *,
    heading: str = "Now playing",
    queue_position: int | None = None,
) -> discord.Embed:
    """Clean card: cover + title + a few quiet meta lines."""
    platform = _source_label(track.source)
    color = SOURCE_COLORS.get(track.source, BRAND_COLOR)

    artist = _clip(track.uploader, 80) if track.uploader else None
    length = _format_duration(track.duration)

    lines = [f"**[{_clip(track.title, 180)}]({track.webpage_url})**"]
    if artist:
        lines.append(artist)
    meta = f"{platform}  ·  {length}"
    if queue_position is not None:
        meta += f"  ·  #{queue_position} in queue"
    lines.append(f"*{meta}*")

    embed = discord.Embed(
        title=heading,
        description="\n".join(lines),
        color=color,
    )
    if track.thumbnail:
        embed.set_image(url=track.thumbnail)

    embed.set_author(name="Dream Team")
    embed.set_footer(
        text=track.requester.display_name,
        icon_url=track.requester.display_avatar.url,
    )
    return embed


def idle_music_embed() -> discord.Embed:
    embed = discord.Embed(
        title="Now playing",
        description="Nothing yet.\n`/play` a SoundCloud / Spotify link or search.",
        color=BRAND_COLOR,
    )
    embed.set_author(name="Dream Team")
    embed.set_footer(text="Music")
    return embed


def queue_embed(player: "GuildPlayer") -> discord.Embed:
    embed = discord.Embed(title="Queue", color=BRAND_COLOR)
    embed.set_author(name="Dream Team")

    if player.current:
        cur = player.current
        embed.description = (
            f"**Now** — [{_clip(cur.title, 90)}]({cur.webpage_url})"
        )
        if cur.thumbnail:
            embed.set_thumbnail(url=cur.thumbnail)

    if player.queue:
        lines = [
            f"`{i:02d}`  [{_clip(t.title, 55)}]({t.webpage_url})"
            for i, t in enumerate(list(player.queue)[:10], start=1)
        ]
        if len(player.queue) > 10:
            lines.append(f"*+{len(player.queue) - 10} more*")
        embed.add_field(name="Up next", value="\n".join(lines), inline=False)
    elif not player.current:
        embed.description = "Empty. `/play` something to fill it."

    return embed


class GuildPlayer:
    def __init__(self, bot: commands.Bot, guild_id: int) -> None:
        self.bot = bot
        self.guild_id = guild_id
        self.queue: deque[Track] = deque()
        self.current: Track | None = None
        self.voice: discord.VoiceClient | None = None
        self.announce_channel_id: int | None = None
        self._play_next = asyncio.Event()
        self._audio_lock = asyncio.Lock()
        self._player_task: asyncio.Task | None = None
        self._stopped = False
        self._playback_error: Exception | None = None

    async def _announce(self, message: str) -> None:
        if not self.announce_channel_id:
            return
        guild = self.bot.get_guild(self.guild_id)
        if guild is None:
            return
        channel = guild.get_channel(self.announce_channel_id)
        if not isinstance(channel, discord.TextChannel):
            return
        try:
            await channel.send(message)
        except discord.HTTPException as exc:
            log.debug("Could not announce music error: %s", exc)

    def ensure_player_loop(self) -> None:
        if self._player_task is None or self._player_task.done():
            self._stopped = False
            self._player_task = asyncio.create_task(self._player_loop())

    def _sync_voice(self) -> discord.VoiceClient | None:
        guild = self.bot.get_guild(self.guild_id)
        if guild and guild.voice_client and guild.voice_client.is_connected():
            self.voice = guild.voice_client
        return self.voice

    async def connect(self, channel: discord.VoiceChannel) -> discord.VoiceClient:
        guild = self.bot.get_guild(self.guild_id)
        if guild is None:
            raise RuntimeError("Guild not found.")

        existing = guild.voice_client
        # Always prefer a stock VoiceClient for music. VoiceRecvClient (from /join)
        # often never finishes the DAVE MLS handshake → silent audio on hosts.
        if existing and existing.is_connected():
            is_recv = existing.__class__.__name__ == "VoiceRecvClient"
            wrong_channel = bool(
                existing.channel and existing.channel.id != channel.id
            )
            if is_recv or wrong_channel:
                log.info(
                    "Reconnecting voice for music (recv=%s move=%s)",
                    is_recv,
                    wrong_channel,
                )
                try:
                    await existing.disconnect(force=True)
                except Exception as exc:
                    log.debug("Disconnect before music reconnect: %r", exc)
                await asyncio.sleep(0.5)
            else:
                self.voice = existing
                if await _wait_for_dave(existing):
                    return existing
                log.warning("Existing voice DAVE stuck — forcing reconnect")
                try:
                    await existing.disconnect(force=True)
                except Exception:
                    pass
                await asyncio.sleep(0.5)

        self.voice = await channel.connect(
            reconnect=True, self_deaf=False, self_mute=False
        )
        await asyncio.sleep(0.4)
        if not await _wait_for_dave(self.voice):
            # One hard reset of the voice websocket / MLS session.
            log.warning("DAVE still not ready — cycling voice connection")
            try:
                await self.voice.disconnect(force=True)
            except Exception:
                pass
            await asyncio.sleep(0.8)
            self.voice = await channel.connect(
                reconnect=True, self_deaf=False, self_mute=False
            )
            await asyncio.sleep(0.4)
            await _wait_for_dave(self.voice)
        return self.voice

    async def enqueue(self, track: Track) -> int:
        self.queue.append(track)
        self.ensure_player_loop()
        self._play_next.set()
        return len(self.queue)

    async def skip(self) -> Track | None:
        skipped = self.current
        voice = self._sync_voice()
        if voice and voice.is_playing():
            voice.stop()
        return skipped

    async def stop(self) -> None:
        self.queue.clear()
        self.current = None
        self._stopped = True
        voice = self._sync_voice()
        if voice and (voice.is_playing() or voice.is_paused()):
            voice.stop()
        if voice and voice.is_connected():
            await voice.disconnect()
        self.voice = None
        if self._player_task and not self._player_task.done():
            self._player_task.cancel()
            try:
                await self._player_task
            except asyncio.CancelledError:
                pass
        self._player_task = None
        await self._set_presence(None)

    async def _set_presence(self, track: Track | None) -> None:
        cog = self.bot.get_cog("MusicCog")
        if isinstance(cog, MusicCog):
            await cog.update_presence(track, owner_guild_id=self.guild_id)

    def pause(self) -> bool:
        voice = self._sync_voice()
        if voice and voice.is_playing():
            voice.pause()
            return True
        return False

    def resume(self) -> bool:
        voice = self._sync_voice()
        if voice and voice.is_paused():
            voice.resume()
            return True
        return False

    async def _player_loop(self) -> None:
        try:
            while not self._stopped:
                if not self.queue:
                    self.current = None
                    self._play_next.clear()
                    try:
                        await asyncio.wait_for(self._play_next.wait(), timeout=180)
                    except asyncio.TimeoutError:
                        voice = self._sync_voice()
                        if voice and voice.is_connected() and not self.queue:
                            await voice.disconnect()
                            self.voice = None
                        break
                    continue

                voice = self._sync_voice()
                if voice is None or not voice.is_connected():
                    log.warning("Voice disconnected; clearing music queue")
                    self.queue.clear()
                    break

                track = self.queue.popleft()
                self.current = track
                finished = asyncio.Event()
                self._playback_error = None

                def after_play(error: Exception | None) -> None:
                    if error:
                        self._playback_error = error
                        log.warning(
                            "Playback error for %s: %r\n%s",
                            track.title,
                            error,
                            "".join(traceback.format_exception(error)),
                        )
                    self.bot.loop.call_soon_threadsafe(finished.set)

                async with self._audio_lock:
                    local_path: Path | None = None
                    try:
                        if voice.is_playing():
                            voice.stop()
                            await asyncio.sleep(0.2)

                        await _wait_for_dave(voice)
                        try:
                            local_path = await prepare_playable_path(track)
                            play_target = str(local_path)
                            log.info(
                                "Starting '%s' via ffmpeg=%s file=%s (%s bytes)",
                                track.title,
                                FFMPEG_EXECUTABLE,
                                local_path.name,
                                local_path.stat().st_size,
                            )
                        except Exception as dl_exc:
                            # Fall back to live stream if download fails.
                            log.warning(
                                "Download failed for %s (%r) — streaming instead",
                                track.title,
                                dl_exc,
                            )
                            play_target = await resolve_stream_url(track)
                            log.info(
                                "Starting '%s' via ffmpeg=%s url=%s",
                                track.title,
                                FFMPEG_EXECUTABLE,
                                play_target[:96],
                            )

                        source = make_audio_source(play_target)
                        track.started_at = time.time()
                        voice.play(source, after=after_play)
                        await self._set_presence(track)
                    except Exception as exc:
                        log.warning(
                            "Failed to start track %s: %r\n%s",
                            track.title,
                            exc,
                            traceback.format_exc(),
                        )
                        self.current = None
                        await self._set_presence(None)
                        await self._announce(
                            f"Couldn’t start **{track.title}**: `{exc}`"
                        )
                        continue

                # Catch silent FFmpeg deaths (card said “Now playing”, audio never flowed).
                await asyncio.sleep(1.25)
                legitimately_short = bool(
                    track.duration is not None and track.duration <= 2
                )
                if finished.is_set() and not legitimately_short:
                    err = self._playback_error
                    detail = f" `{err}`" if err else ""
                    log.warning(
                        "Track ended within 1.25s (no audible play): %s%s",
                        track.title,
                        detail,
                    )
                    await self._announce(
                        f"Audio died immediately for **{track.title}**."
                        f"{detail} Try another link or `/play` again."
                    )
                    self.current = None
                    await self._set_presence(None)
                    continue

                if (
                    not finished.is_set()
                    and not (voice.is_playing() or voice.is_paused())
                ):
                    log.warning(
                        "Voice reports not playing after start: %s", track.title
                    )
                    await self._announce(
                        f"Joined voice but isn’t transmitting **{track.title}**. "
                        "Check the bot’s Speak permission in that channel."
                    )
                    self.current = None
                    await self._set_presence(None)
                    continue

                if not finished.is_set():
                    await finished.wait()
                if self._playback_error is not None:
                    await self._announce(
                        f"Playback error on **{track.title}**: `{self._playback_error}`"
                    )
                self.current = None
                if not self.queue:
                    await self._set_presence(None)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.exception("Music player crashed: %s", exc)
        finally:
            self.current = None
            if not self.queue:
                try:
                    await self._set_presence(None)
                except Exception:
                    pass


class MusicCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.players: dict[int, GuildPlayer] = {}
        self._presence_guild_id: int | None = None
        self._music_stopped_at: float | None = None
        self._showing_full_idle = True
        self._last_idle_rotate_at = time.time()
        self.idle_watchdog.start()
        voice_ok, voice_detail = _voice_stack_ok()
        if voice_ok:
            log.info("Music cog loaded (%s, SoundCloud + Spotify)", voice_detail)
        else:
            log.error(
                "Music cog loaded but voice stack is incomplete — "
                "clients will hear SILENCE. Upgrade: pip install -U "
                "'discord.py[voice]>=2.7.0' davey. (%s)",
                voice_detail,
            )

    def cog_unload(self) -> None:
        self.idle_watchdog.cancel()

    def _app_id(self) -> int | None:
        if self.bot.application_id:
            return int(self.bot.application_id)
        if self.bot.user:
            return self.bot.user.id
        return None

    def get_player(self, guild_id: int) -> GuildPlayer:
        if guild_id not in self.players:
            self.players[guild_id] = GuildPlayer(self.bot, guild_id)
        return self.players[guild_id]

    def _any_music_active(self) -> bool:
        return any(p.current is not None for p in self.players.values())

    @tasks.loop(seconds=20)
    async def idle_watchdog(self) -> None:
        """Idle after 2 min no music; rotate the Watching title while idle."""
        if self._any_music_active():
            return

        app_id = self._app_id()
        now = time.time()

        # Finish the post-music cooldown → full idle
        if (
            self._music_stopped_at is not None
            and not self._showing_full_idle
            and now - self._music_stopped_at >= config.IDLE_AFTER_SECONDS
        ):
            try:
                await apply_rich_presence(
                    self.bot, presence_idle(application_id=app_id)
                )
                self._showing_full_idle = True
                self._music_stopped_at = None
                self._last_idle_rotate_at = now
                log.info("Idle status restored after cooldown")
            except Exception as exc:
                log.warning("Idle watchdog failed: %r", exc)
            return

        # While idle, change the big title every IDLE_ROTATE_SECONDS
        if self._showing_full_idle and (
            now - self._last_idle_rotate_at >= config.IDLE_ROTATE_SECONDS
        ):
            try:
                await apply_rich_presence(
                    self.bot, presence_idle(application_id=app_id)
                )
                self._last_idle_rotate_at = now
                log.info("Rotated idle Watching title")
            except Exception as exc:
                log.warning("Idle rotate failed: %r", exc)

    @idle_watchdog.before_loop
    async def before_idle_watchdog(self) -> None:
        await self.bot.wait_until_ready()

    async def update_presence(
        self, track: Track | None, *, owner_guild_id: int, paused: bool = False
    ) -> None:
        app_id = self._app_id()
        if track is not None:
            self._presence_guild_id = owner_guild_id
            self._music_stopped_at = None
            self._showing_full_idle = False
            player = self.players.get(owner_guild_id)
            queue_len = len(player.queue) if player else 0
            platform = _source_label(track.source)
            presence = presence_now_playing(
                title=track.title,
                platform=platform,
                requester=track.requester.display_name,
                uploader=track.uploader,
                source_key=track.source if track.source in {"soundcloud", "spotify"} else "soundcloud",
                started_at=track.started_at,
                duration=track.duration,
                queue_len=queue_len,
                party_id=f"dream-team-{owner_guild_id}",
                track_url=track.webpage_url,
                thumbnail_url=track.thumbnail,
                application_id=app_id,
                paused=paused,
            )
            try:
                await apply_rich_presence(self.bot, presence)
            except Exception as exc:
                log.warning("Could not update presence: %r", exc)
            await self.refresh_now_playing_panel(
                owner_guild_id, track=track, queue_len=queue_len, paused=paused
            )
            return

        if (
            self._presence_guild_id is not None
            and self._presence_guild_id != owner_guild_id
        ):
            other = self.players.get(self._presence_guild_id)
            if other and other.current is not None:
                return

        for gid, player in self.players.items():
            if player.current is not None:
                voice = player._sync_voice()
                is_paused = bool(voice and voice.is_paused())
                await self.update_presence(
                    player.current, owner_guild_id=gid, paused=is_paused
                )
                return

        # Nothing playing — start 2-minute countdown to full idle
        self._presence_guild_id = None
        self._music_stopped_at = time.time()
        self._showing_full_idle = False
        try:
            await apply_rich_presence(
                self.bot, presence_between_tracks(application_id=app_id)
            )
        except Exception as exc:
            log.warning("Could not set between-tracks presence: %r", exc)
        await self.refresh_now_playing_panel(
            owner_guild_id, track=None, queue_len=0, paused=False
        )

    async def refresh_now_playing_panel(
        self,
        guild_id: int,
        *,
        track: Track | None,
        queue_len: int,
        paused: bool = False,
    ) -> None:
        """Editable channel message — this is the fancy UI bots CAN show."""
        channel_id, message_id = self.bot.db.get_now_playing_panel(guild_id)
        if not channel_id:
            return

        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return
        channel = guild.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            return

        if track is None:
            embed = idle_music_embed()
        else:
            heading = "Paused" if paused else "Now playing"
            embed = track_embed(track, heading=heading)
            if queue_len:
                embed.description = (
                    (embed.description or "")
                    + f"\n*{queue_len} waiting in queue*"
                )

        try:
            view = MusicControlsView() if track is not None else None

            if message_id:
                try:
                    msg = await channel.fetch_message(message_id)
                    await msg.edit(embed=embed, view=view)
                    return
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    pass

            msg = await channel.send(embed=embed, view=view)
            self.bot.db.set_now_playing_panel(guild_id, channel.id, msg.id)
            try:
                await msg.pin(reason="Dream Team now-playing panel")
            except discord.HTTPException:
                pass
        except discord.Forbidden:
            log.warning("Cannot update now-playing panel in %s", guild.name)
        except discord.HTTPException as exc:
            log.warning("Now-playing panel failed: %s", exc)

    async def _require_voice(
        self, interaction: discord.Interaction
    ) -> tuple[discord.Member, discord.VoiceChannel] | None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                "Use this in a server.", ephemeral=True
            )
            return None

        if interaction.user.voice is None or interaction.user.voice.channel is None:
            await interaction.response.send_message(
                "Join a voice channel first.", ephemeral=True
            )
            return None

        channel = interaction.user.voice.channel
        if not isinstance(channel, discord.VoiceChannel):
            await interaction.response.send_message(
                "I can only join regular voice channels.", ephemeral=True
            )
            return None
        return interaction.user, channel

    @app_commands.command(
        name="play",
        description="Play audio from SoundCloud or Spotify (link or search)",
    )
    @app_commands.describe(
        query="SoundCloud / Spotify URL, or SoundCloud search text"
    )
    async def play(self, interaction: discord.Interaction, query: str) -> None:
        ctx = await self._require_voice(interaction)
        if ctx is None:
            return
        member, channel = ctx

        await interaction.response.defer()
        player = self.get_player(interaction.guild_id)
        if interaction.channel_id:
            player.announce_channel_id = interaction.channel_id
        was_idle = player.current is None and not player.queue

        try:
            if FFMPEG_EXECUTABLE == "ffmpeg" and not shutil.which("ffmpeg"):
                raise RuntimeError(
                    "FFmpeg is not installed (or not on PATH). "
                    "Install it with: brew install ffmpeg"
                )
            voice_ok, voice_detail = _voice_stack_ok()
            if not voice_ok:
                raise RuntimeError(
                    "Voice stack can’t send audible audio "
                    f"({voice_detail}). Host needs "
                    "`pip install -U 'discord.py[voice]>=2.7.0' davey` "
                    "then restart."
                )
            await player.connect(channel)
            tracks = await resolve_tracks(query.strip(), member)
            positions: list[int] = []
            for track in tracks:
                positions.append(await player.enqueue(track))
        except Exception as exc:
            log.warning("/play failed: %r\n%s", exc, traceback.format_exc())
            msg = str(exc)
            msg = re.sub(r"\x1b\[[0-9;]*m", "", msg)
            await interaction.followup.send(f"Couldn't play that: {msg}")
            return

        track = tracks[0]
        position = positions[0]
        if len(tracks) == 1:
            if was_idle:
                embed = track_embed(track, heading="Now playing")
            else:
                embed = track_embed(
                    track, heading="Queued", queue_position=position
                )
        else:
            heading = "Now playing" if was_idle else "Queued"
            embed = track_embed(track, heading=heading)
            extra = len(tracks) - 1
            embed.description = (
                (embed.description or "")
                + f"\n*+{extra} more from Spotify"
                + (
                    f" (capped at {_SPOTIFY_PLAYLIST_CAP})"
                    if len(tracks) >= _SPOTIFY_PLAYLIST_CAP
                    else ""
                )
                + "*"
            )

        await interaction.followup.send(embed=embed, view=MusicControlsView())

    @app_commands.command(name="pause", description="Pause the current track")
    async def pause(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                "Use this in a server.", ephemeral=True
            )
            return
        player = self.get_player(interaction.guild_id)
        if player.pause():
            if player.current:
                await self.update_presence(
                    player.current, owner_guild_id=interaction.guild_id, paused=True
                )
            await interaction.response.send_message("Paused.")
        else:
            await interaction.response.send_message(
                "Nothing is playing.", ephemeral=True
            )

    @app_commands.command(name="resume", description="Resume paused music")
    async def resume(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                "Use this in a server.", ephemeral=True
            )
            return
        player = self.get_player(interaction.guild_id)
        if player.resume():
            if player.current:
                await self.update_presence(
                    player.current, owner_guild_id=interaction.guild_id, paused=False
                )
            await interaction.response.send_message("Resumed.")
        else:
            await interaction.response.send_message(
                "Nothing is paused.", ephemeral=True
            )

    @app_commands.command(name="leave", description="Disconnect the bot from voice")
    async def leave(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                "Use this in a server.", ephemeral=True
            )
            return
        voice_ai = self.bot.get_cog("VoiceAICog")
        if voice_ai is not None and hasattr(voice_ai, "_leave_voice"):
            await voice_ai._leave_voice(interaction.guild_id)
        else:
            await self.get_player(interaction.guild_id).stop()
        await interaction.response.send_message("Left the voice channel.")
