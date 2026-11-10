from __future__ import annotations

import asyncio
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
_SC_SEARCH_CANDIDATES = 5


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


def _sc_search_query(title: str, artist: str | None) -> str:
    parts = [p for p in (artist, title) if p and str(p).strip()]
    return " ".join(parts).strip() or title


def _candidate_score(entry: dict, search: str) -> tuple:
    """Prefer closer title matches; deprioritize obvious remix/cover noise."""
    title = (entry.get("title") or "").lower()
    search_l = search.lower()
    tokens = [t for t in re.split(r"\s+", search_l) if len(t) > 1]
    hit = sum(1 for t in tokens if t in title)
    noise = 0
    for word in ("remix", "cover", "karaoke", "nightcore", "sped up", "slowed"):
        if word in title and word not in search_l:
            noise += 1
    return (-hit, noise)


def _soundcloud_search_candidates(search: str) -> list[str]:
    """Return SoundCloud track URLs for a text search (best-effort)."""
    yt_dlp = _yt_dlp()
    opts = _ytdl_opts(
        skip_download=True,
        extract_flat="in_playlist",
        noplaylist=False,
        ignoreerrors=True,
        playlistend=_SC_SEARCH_CANDIDATES,
    )
    query = f"scsearch{_SC_SEARCH_CANDIDATES}:{search}"
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(query, download=False)
    if not info:
        return []
    entries = info.get("entries") if isinstance(info, dict) else None
    if not entries:
        url = (
            info.get("webpage_url") or info.get("url")
            if isinstance(info, dict)
            else None
        )
        return [url] if isinstance(url, str) and url.startswith("http") else []

    ranked = sorted(
        (e for e in entries if isinstance(e, dict)),
        key=lambda e: _candidate_score(e, search),
    )
    urls: list[str] = []
    for entry in ranked:
        for key in ("webpage_url", "url", "original_url"):
            val = entry.get(key)
            if isinstance(val, str) and val.startswith("http"):
                urls.append(val)
                break
    return urls


def _soundcloud_bridge(search: str) -> tuple[str, dict]:
    """Find a playable SoundCloud stream for a title/artist search string."""
    search = (search or "").strip()
    if not search:
        raise ValueError("Empty SoundCloud search.")
    candidates = _soundcloud_search_candidates(search)
    last_error: Exception | None = None
    for candidate in candidates:
        try:
            info = _extract_info(candidate, for_stream=True)
            _pick_stream_url(info)
            webpage = (
                info.get("webpage_url")
                or info.get("original_url")
                or candidate
            )
            return str(webpage), info
        except Exception as exc:  # noqa: BLE001 — try next SoundCloud hit
            last_error = exc
            log.info(
                "SoundCloud candidate failed for '%s': %s (%r)",
                search,
                candidate,
                exc,
            )
            continue

    # Last resort: let yt-dlp pick scsearch1 itself
    try:
        info = _extract_info(search, for_stream=True)
        webpage = info.get("webpage_url") or info.get("original_url") or search
        _pick_stream_url(info)
        return str(webpage), info
    except Exception as exc:
        last_error = exc

    detail = f" ({last_error})" if last_error else ""
    raise ValueError(
        f"Couldn’t find a playable SoundCloud match for “{search}”.{detail}"
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
    first_search = _sc_search_query(first.title, first.artist)
    sc_url, sc_info = await asyncio.to_thread(_soundcloud_bridge, first_search)
    tracks.append(
        _track_from_soundcloud_info(
            sc_info,
            requester=requester,
            source="spotify",
            webpage_url=f"https://open.spotify.com/track/{first.track_id}",
            query=sc_url,
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
                query=f"scbridge:{_sc_search_query(song.title, song.artist)}",
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
        if query.startswith("scbridge:"):
            search = query.removeprefix("scbridge:")
            sc_url, info = _soundcloud_bridge(search)
            return _pick_stream_url(info), sc_url

        info = _extract_info(query, for_stream=True)
        return _pick_stream_url(info), None

    stream_url, cached_query = await asyncio.to_thread(extract_url)
    if cached_query:
        track.query = cached_query
    return stream_url


def make_audio_source(stream_url: str) -> discord.AudioSource:
    # FFmpegPCMAudio → Discord encodes Opus. More reliable for SoundCloud HLS than
    # FFmpegOpusAudio (Ogg packet parsing often dies silently on m3u8/AAC).
    return discord.FFmpegPCMAudio(
        stream_url,
        executable=FFMPEG_EXECUTABLE,
        before_options=FFMPEG_BEFORE,
        options=FFMPEG_OPTIONS,
    )


class TrackLinkView(discord.ui.View):
    def __init__(self, url: str, platform: str) -> None:
        super().__init__(timeout=None)
        self.add_item(
            discord.ui.Button(
                label=f"Open on {platform}",
                style=discord.ButtonStyle.link,
                url=url,
            )
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
        embed.description = "Empty. Use `/play` to add something."

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
        if existing and existing.is_connected():
            if existing.channel and existing.channel.id != channel.id:
                await existing.move_to(channel)
            self.voice = existing
            return existing

        # Prefer VoiceRecvClient so /join can share this connection
        connect_kwargs: dict = {"reconnect": True, "self_deaf": True}
        try:
            from discord.ext import voice_recv

            connect_kwargs["cls"] = voice_recv.VoiceRecvClient
        except ImportError:
            pass

        self.voice = await channel.connect(**connect_kwargs)
        await asyncio.sleep(0.5)
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
                    try:
                        if voice.is_playing():
                            voice.stop()
                            await asyncio.sleep(0.2)

                        stream_url = await resolve_stream_url(track)
                        log.info(
                            "Starting '%s' via ffmpeg=%s url=%s",
                            track.title,
                            FFMPEG_EXECUTABLE,
                            stream_url[:96],
                        )
                        source = make_audio_source(stream_url)
                        track.started_at = time.time()
                        voice.play(source, after=after_play, signal_type="music")
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
            view = None
            if track is not None:
                view = TrackLinkView(
                    track.webpage_url, _source_label(track.source)
                )

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

        await interaction.followup.send(
            embed=embed,
            view=TrackLinkView(track.webpage_url, _source_label(track.source)),
        )

    @app_commands.command(name="skip", description="Skip the current track")
    async def skip(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                "Use this in a server.", ephemeral=True
            )
            return
        player = self.get_player(interaction.guild_id)
        skipped = await player.skip()
        if skipped is None and not player.queue:
            await interaction.response.send_message(
                "Nothing to skip.", ephemeral=True
            )
            return
        title = skipped.title if skipped else "current track"
        await interaction.response.send_message(f"Skipped **{title}**.")

    @app_commands.command(name="stop", description="Stop music and leave voice")
    async def stop(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                "Use this in a server.", ephemeral=True
            )
            return
        await self.get_player(interaction.guild_id).stop()
        await interaction.response.send_message(
            "Stopped and left the voice channel."
        )

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

    @app_commands.command(name="queue", description="Show the music queue")
    async def queue(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                "Use this in a server.", ephemeral=True
            )
            return
        player = self.get_player(interaction.guild_id)
        if not player.current and not player.queue:
            await interaction.response.send_message(
                "Queue is empty.", ephemeral=True
            )
            return

        await interaction.response.send_message(embed=queue_embed(player))

    @app_commands.command(
        name="nowplaying", description="Show the track currently playing"
    )
    async def nowplaying(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                "Use this in a server.", ephemeral=True
            )
            return
        track = self.get_player(interaction.guild_id).current
        if track is None:
            await interaction.response.send_message(
                "Nothing is playing.", ephemeral=True
            )
            return
        await interaction.response.send_message(
            embed=track_embed(track, heading="Now playing"),
            view=TrackLinkView(track.webpage_url, _source_label(track.source)),
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
