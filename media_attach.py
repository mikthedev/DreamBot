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

# Ignore YouTube entirely — not supported for attach on this bot.
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


def extract_direct_video_urls(text: str) -> list[str]:
    return list(dict.fromkeys(_DIRECT_VIDEO_RE.findall(text or "")))


def is_youtube_url(url: str) -> bool:
    return bool(_YOUTUBE_RE.search(url or ""))


def collect_video_candidates(
    *,
    text: str = "",
    explicit_urls: list[str] | None = None,
) -> list[str]:
    """Ordered unique video URLs to try (Bluesky / direct only — no YouTube)."""
    found: list[str] = []

    def add(u: str) -> None:
        u = (u or "").strip()
        if not u or u in found or is_youtube_url(u):
            return
        found.append(u)

    for u in explicit_urls or []:
        add(u)
    for u in extract_direct_video_urls(text):
        add(u)
    for u in _BSKY_VIDEO_RE.findall(text or ""):
        add(u)
    return found


def strip_urls_from_text(text: str, urls: list[str]) -> str:
    out = text or ""
    for u in urls:
        out = out.replace(u, "")
    out = re.sub(r"[ \t]{2,}", " ", out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()


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

    progressive: list[tuple[int, dict]] = []
    for fmt in formats:
        if fmt.get("vcodec") in (None, "none"):
            continue
        if fmt.get("acodec") in (None, "none"):
            continue
        size = size_of(fmt)
        if size is not None and size > max_bytes:
            continue
        height = fmt.get("height") or 0
        progressive.append((int(height), fmt))

    if progressive:
        progressive.sort(key=lambda x: (-x[0], size_of(x[1]) or 0))
        fid = progressive[0][1].get("format_id")
        if fid:
            return str(fid)

    # Best video+audio under budget (HLS ladders from Bluesky)
    video_only = [
        f
        for f in formats
        if f.get("vcodec") not in (None, "none") and f.get("acodec") in (None, "none")
    ]
    audio_only = [
        f
        for f in formats
        if f.get("acodec") not in (None, "none") and f.get("vcodec") in (None, "none")
    ]
    if video_only and audio_only:
        video_only.sort(key=lambda f: -(f.get("height") or 0))
        for vf in video_only:
            vs = size_of(vf) or 0
            for af in audio_only:
                total = vs + (size_of(af) or 0)
                if total and total <= max_bytes:
                    return f"{vf.get('format_id')}+{af.get('format_id')}"
        best = video_only[0]
        fid = best.get("format_id")
        if fid:
            return str(fid)

    return None


def _ytdl_opts(outtmpl: str) -> dict:
    return {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "format": (
            "best[ext=mp4][height<=720]/"
            "best[height<=720]/"
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
    }


def _find_output_file(dest_dir: Path) -> Path | None:
    files = sorted(
        p
        for p in dest_dir.iterdir()
        if p.is_file() and p.suffix.lower() in {".mp4", ".webm", ".mov", ".mkv"}
    )
    if not files:
        files = [p for p in dest_dir.iterdir() if p.is_file() and p.stat().st_size > 0]
    return files[0] if files else None


def _unwrap_info(info: dict | None) -> dict:
    if not info:
        raise RuntimeError("empty extract_info")
    if "entries" in info:
        entries = [e for e in info["entries"] if e]
        if not entries:
            raise RuntimeError("empty playlist")
        return entries[0]
    return info


def _download_with_ytdl(url: str, dest_dir: Path, max_bytes: int) -> Path | None:
    """Download Bluesky HLS / other non-YouTube streams via yt-dlp."""
    outtmpl = str(dest_dir / "clip.%(ext)s")
    for leftover in dest_dir.iterdir():
        if leftover.is_file():
            leftover.unlink(missing_ok=True)
    try:
        opts = _ytdl_opts(outtmpl)
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = _unwrap_info(ydl.extract_info(url, download=False))
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
            log.info("Downloaded video too large (%s > %s)", size, max_bytes)
            return None
        if size < 1024:
            path.unlink(missing_ok=True)
            raise RuntimeError("download file too small")
        log.info("Video downloaded (%s bytes) → %s", size, path.name)
        return path
    except Exception as exc:
        log.warning("yt-dlp video try failed: %s", exc)
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
    if is_youtube_url(url):
        log.info("Skipping YouTube URL (not supported): %s", url[:80])
        return None

    if _is_direct_file(url):
        ext = ".mp4"
        lower = url.lower().split("?", 1)[0]
        for candidate in (".webm", ".mov", ".mp4"):
            if lower.endswith(candidate):
                ext = candidate
                break
        return await _download_direct(session, url, dest_dir / f"clip{ext}", max_bytes)

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
    candidates = [u for u in urls if u and not is_youtube_url(u)][
        : config.OW_MEDIA_MAX_VIDEOS
    ]
    files: list[discord.File] = []
    failed_links: list[str] = []
    tmp: Path | None = None

    try:
        if not candidates:
            # Preserve YouTube links in failed so callers can keep them as text
            yt_left = [u for u in urls if u and is_youtube_url(u)]
            yield [], yt_left
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
            break

        if not attached:
            failed_links = list(dict.fromkeys(candidates))

        yield files, failed_links
    finally:
        files.clear()
        if tmp is not None:
            shutil.rmtree(tmp, ignore_errors=True)
