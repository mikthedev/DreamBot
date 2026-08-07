"""Temporary media downloads for Discord attachments — never kept on disk."""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import aiohttp
import discord
import yt_dlp

import config

log = logging.getLogger("dream_team.media")

# YouTube + common short links
_YOUTUBE_RE = re.compile(
    r"""(?xi)
    https?://(?:www\.)?
    (?:
        youtube\.com/(?:watch\?[\w=&%-]*v=|shorts/|embed/|live/)|
        youtu\.be/
    )
    [\w\-?=&#.%]+
    """
)

_DIRECT_VIDEO_RE = re.compile(
    r"""(?xi)
    https?://[^\s<>\]]+\.(?:mp4|webm|mov)(?:\?[^\s<>\]]*)?
    """
)

_BSKY_VIDEO_RE = re.compile(
    r"""(?xi)
    https?://video\.bsky\.app/[^\s<>\]]+
    """
)


def extract_youtube_urls(text: str) -> list[str]:
    return list(dict.fromkeys(_YOUTUBE_RE.findall(text or "")))


def extract_direct_video_urls(text: str) -> list[str]:
    return list(dict.fromkeys(_DIRECT_VIDEO_RE.findall(text or "")))


def collect_video_candidates(
    *,
    text: str = "",
    explicit_urls: list[str] | None = None,
) -> list[str]:
    """Ordered unique video URLs to try (max one download later)."""
    found: list[str] = []
    for u in explicit_urls or []:
        if u and u not in found:
            found.append(u)
    for u in extract_youtube_urls(text):
        if u not in found:
            found.append(u)
    for u in extract_direct_video_urls(text):
        if u not in found:
            found.append(u)
    for u in _BSKY_VIDEO_RE.findall(text or ""):
        if u not in found:
            found.append(u)
    return found


def strip_urls_from_text(text: str, urls: list[str]) -> str:
    out = text or ""
    for u in urls:
        out = out.replace(u, "")
    out = re.sub(r"[ \t]{2,}", " ", out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()


def _is_youtube(url: str) -> bool:
    return bool(_YOUTUBE_RE.search(url))


def _is_direct_file(url: str) -> bool:
    return bool(_DIRECT_VIDEO_RE.fullmatch(url.strip()))


def _duration_ok(info: dict) -> bool:
    max_dur = config.OW_MEDIA_MAX_DURATION_SEC
    if max_dur <= 0:
        return True
    duration = info.get("duration")
    if duration is None:
        return True
    try:
        return float(duration) <= max_dur
    except (TypeError, ValueError):
        return True


def _pick_download_format_id(info: dict, max_bytes: int) -> str | None:
    """
    Prefer a single progressive file Discord can upload (no merge), under max_bytes.
    Falls back to a mergeable pair only if nothing progressive fits.
    """
    formats = [f for f in (info.get("formats") or []) if isinstance(f, dict)]
    if not formats:
        return None

    def size_of(fmt: dict) -> int | None:
        for key in ("filesize", "filesize_approx"):
            val = fmt.get(key)
            if val is not None:
                try:
                    return int(val)
                except (TypeError, ValueError):
                    pass
        return None

    def has_url(fmt: dict) -> bool:
        return bool(fmt.get("url") or fmt.get("fragment_base_url"))

    progressive: list[dict] = []
    for fmt in formats:
        if not has_url(fmt):
            continue
        vcodec = (fmt.get("vcodec") or "none").lower()
        acodec = (fmt.get("acodec") or "none").lower()
        if vcodec == "none" or acodec == "none":
            continue
        # Progressive / single-file
        protocol = (fmt.get("protocol") or "").lower()
        if "m3u8" in protocol or "dash" in protocol:
            # Still usable if yt-dlp can remux; prefer later
            pass
        height = fmt.get("height") or 0
        try:
            height = int(height)
        except (TypeError, ValueError):
            height = 0
        if height and height > 720:
            continue
        sz = size_of(fmt)
        if sz is not None and sz > max_bytes:
            continue
        progressive.append(fmt)

    def score(fmt: dict) -> tuple:
        height = fmt.get("height") or 0
        try:
            height = int(height)
        except (TypeError, ValueError):
            height = 0
        ext = (fmt.get("ext") or "").lower()
        # Prefer mp4 + higher (but ≤720) resolution
        return (
            1 if ext == "mp4" else 0,
            height,
            1 if (fmt.get("acodec") or "none") != "none" else 0,
        )

    if progressive:
        best = max(progressive, key=score)
        fid = best.get("format_id")
        if fid:
            return str(fid)

    # Fallback: let yt-dlp choose a small ladder (may merge)
    return None


def _ytdl_base_opts(*, use_cookies: bool, outtmpl: str, max_bytes: int) -> dict:
    try:
        from music import _ytdl_opts

        base = _ytdl_opts(
            use_cookies=use_cookies,
            quiet=True,
            no_warnings=True,
            noplaylist=True,
            skip_download=False,
        )
    except Exception:
        base = {
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
        }
        if config.YTDLP_PROXY:
            base["proxy"] = config.YTDLP_PROXY
        if use_cookies:
            cookies = Path(config.YTDLP_COOKIES)
            if cookies.is_file():
                base["cookiefile"] = str(cookies)

    # Progressive-first — format 18 is classic 360p mp4 (audio+video one file)
    base.update(
        {
            "format": (
                "18/"
                "22/"
                "best[ext=mp4][height<=720]/"
                "best[height<=480]/"
                "bv*[height<=720]+ba/"
                "best"
            ),
            "outtmpl": outtmpl,
            "socket_timeout": 30,
            "retries": 3,
            "fragment_retries": 3,
            "noprogress": True,
            "overwrites": True,
            "merge_output_format": "mp4",
            "ignore_no_formats_error": True,
            # Check size after download — max_filesize aborts mid-way on some formats
        }
    )
    return base


def _find_output_file(dest_dir: Path) -> Path | None:
    files = sorted(
        p
        for p in dest_dir.iterdir()
        if p.is_file() and p.suffix.lower() in {".mp4", ".webm", ".mov", ".mkv"}
    )
    if not files:
        files = [p for p in dest_dir.iterdir() if p.is_file() and p.stat().st_size > 0]
    return files[0] if files else None


def _download_with_ytdl(url: str, dest_dir: Path, max_bytes: int) -> Path | None:
    """
    Download using the same client/cookie retry ladder as music playback.
    Prefer progressive MP4 so Discord gets a single attachable file.
    """
    try:
        from music import _cookies_path, _unwrap_info
    except Exception:
        _cookies_path = lambda: None  # noqa: E731
        _unwrap_info = lambda info: info  # noqa: E731

    client_tries: list[list[str] | None] = [
        ["android_vr", "tv_downgraded"],
        ["android", "ios", "tv_downgraded"],
        ["tv_downgraded", "web_embedded", "web_creator", "android_vr"],
        ["web_safari", "web_embedded"],
        None,
    ]
    cookie_tries = (False, True) if _cookies_path() else (False,)
    outtmpl = str(dest_dir / "clip.%(ext)s")
    last_exc: Exception | None = None

    for use_cookies in cookie_tries:
        for clients in client_tries:
            label = f"cookies={use_cookies} clients={clients}"
            # Clean previous partials between tries
            for leftover in dest_dir.iterdir():
                if leftover.is_file():
                    leftover.unlink(missing_ok=True)
            try:
                opts = _ytdl_base_opts(
                    use_cookies=use_cookies, outtmpl=outtmpl, max_bytes=max_bytes
                )
                if clients is not None:
                    opts["extractor_args"] = {"youtube": {"player_client": clients}}

                with yt_dlp.YoutubeDL(opts) as ydl:
                    info = _unwrap_info(ydl.extract_info(url, download=False))
                    if not info:
                        raise RuntimeError("empty extract_info")
                    if not _duration_ok(info):
                        log.info(
                            "Video too long (%ss > %ss): %s",
                            info.get("duration"),
                            config.OW_MEDIA_MAX_DURATION_SEC,
                            url[:80],
                        )
                        return None

                    formats = info.get("formats") or []
                    if not formats and not info.get("url"):
                        raise RuntimeError("No video formats found")

                    fmt_id = _pick_download_format_id(info, max_bytes)
                    if fmt_id:
                        opts["format"] = fmt_id
                        # Re-enter with pinned format
                        with yt_dlp.YoutubeDL(opts) as ydl2:
                            ydl2.download([url])
                    else:
                        ydl.download([url])

                path = _find_output_file(dest_dir)
                if path is None:
                    raise RuntimeError("download produced no file")
                size = path.stat().st_size
                if size > max_bytes:
                    path.unlink(missing_ok=True)
                    log.info(
                        "Downloaded video too large (%s > %s) via %s",
                        size,
                        max_bytes,
                        label,
                    )
                    return None
                if size < 1024:
                    path.unlink(missing_ok=True)
                    raise RuntimeError("download file too small")
                log.info(
                    "Video downloaded (%s bytes) via %s → %s",
                    size,
                    label,
                    path.name,
                )
                return path
            except Exception as exc:
                last_exc = exc
                log.warning("yt-dlp video try failed (%s): %s", label, exc)

    log.info(
        "Video download skipped (%s): %s",
        url[:80],
        last_exc or "all strategies failed",
    )
    return None


async def _download_direct(
    session: aiohttp.ClientSession,
    url: str,
    dest: Path,
    max_bytes: int,
) -> Path | None:
    try:
        timeout = aiohttp.ClientTimeout(total=config.OW_MEDIA_DOWNLOAD_TIMEOUT)
        async with session.get(url, timeout=timeout) as resp:
            if resp.status != 200:
                return None
            cl = resp.headers.get("Content-Length")
            if cl and cl.isdigit() and int(cl) > max_bytes:
                log.info("Direct video Content-Length too large: %s", cl)
                return None
            size = 0
            with dest.open("wb") as fh:
                async for chunk in resp.content.iter_chunked(64 * 1024):
                    size += len(chunk)
                    if size > max_bytes:
                        fh.close()
                        dest.unlink(missing_ok=True)
                        log.info("Direct video exceeded max size while streaming")
                        return None
                    fh.write(chunk)
        if not dest.is_file() or dest.stat().st_size < 1024:
            dest.unlink(missing_ok=True)
            return None
        return dest
    except Exception as exc:
        log.info("Direct video download failed: %s", exc)
        dest.unlink(missing_ok=True)
        return None


async def download_one_video(
    session: aiohttp.ClientSession,
    url: str,
    dest_dir: Path,
    *,
    max_bytes: int,
) -> Path | None:
    """Download a single video into dest_dir. Caller must delete the directory."""
    if _is_direct_file(url) and not _is_youtube(url):
        ext = ".mp4"
        lower = url.lower().split("?", 1)[0]
        for candidate in (".webm", ".mov", ".mp4"):
            if lower.endswith(candidate):
                ext = candidate
                break
        return await _download_direct(session, url, dest_dir / f"clip{ext}", max_bytes)

    # YouTube on bot-hosting almost always hits "confirm you're not a bot".
    # Don't burn CPU on the full client/cookie ladder — keep the link in the post.
    if _is_youtube(url) and not config.YTDLP_PROXY:
        log.info(
            "Skipping YouTube download (no proxy; host IP usually blocked): %s",
            url[:80],
        )
        return None

    return await asyncio.to_thread(_download_with_ytdl, url, dest_dir, max_bytes)


def guild_upload_limit(guild: discord.Guild | None) -> int:
    """Cap at config max, never above the guild's Discord upload limit."""
    configured = config.OW_MEDIA_MAX_BYTES
    if guild is None:
        return configured
    return min(configured, int(guild.filesize_limit))


@asynccontextmanager
async def temporary_video_attachments(
    session: aiohttp.ClientSession,
    urls: list[str],
    *,
    guild: discord.Guild | None = None,
) -> AsyncIterator[tuple[list[discord.File], list[str]]]:
    """
    Download at most one video into a temp folder, yield Discord files + leftover links.

    Always deletes temp files after the with-block (after Discord upload should finish).
    """
    max_bytes = guild_upload_limit(guild)
    candidates = [u for u in urls if u][: config.OW_MEDIA_MAX_VIDEOS]
    files: list[discord.File] = []
    failed_links: list[str] = []
    tmp: Path | None = None

    try:
        if not candidates:
            yield [], []
            return

        tmp = Path(tempfile.mkdtemp(prefix="dream_media_"))
        attached = False
        for url in candidates:
            path = await download_one_video(
                session, url, tmp, max_bytes=max_bytes
            )
            if path is None:
                failed_links.append(url)
                continue
            files.append(discord.File(path, filename=path.name))
            attached = True
            # Only one video attachment to stay lightweight
            break

        if not attached:
            failed_links = list(dict.fromkeys(candidates))

        yield files, failed_links
    finally:
        files.clear()
        if tmp is not None:
            shutil.rmtree(tmp, ignore_errors=True)
