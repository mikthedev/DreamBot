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


def _ytdl_download_opts(outtmpl: str, max_bytes: int) -> dict:
    """Lightweight download opts — reuse cookies/proxy/deno from music when available."""
    try:
        from music import _ytdl_opts

        base = _ytdl_opts(
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
        cookies = Path(config.YTDLP_COOKIES)
        if cookies.is_file():
            base["cookiefile"] = str(cookies)

    # Prefer ≤720p small files; fall back to smaller ladders
    fmt = (
        f"bv*[height<=720][filesize_approx<={max_bytes}]+ba/"
        f"b[height<=720][filesize_approx<={max_bytes}]/"
        f"bv*[height<=480]+ba/b[height<=480]/"
        f"bv*[height<=360]+ba/b[height<=360]/worst"
    )
    base.update(
        {
            "format": fmt,
            "outtmpl": outtmpl,
            "max_filesize": max_bytes,
            "socket_timeout": 30,
            "retries": 2,
            "fragment_retries": 2,
            "noprogress": True,
            "overwrites": True,
            # Audio merge → mp4 when possible
            "merge_output_format": "mp4",
        }
    )

    max_dur = config.OW_MEDIA_MAX_DURATION_SEC
    if max_dur > 0:

        def _duration_filter(info: dict, *, incomplete: bool = False):
            if incomplete:
                return None
            duration = info.get("duration")
            if duration is not None and float(duration) > max_dur:
                return f"Video longer than {max_dur}s"
            return None

        base["match_filter"] = _duration_filter

    return base


def _download_with_ytdl(url: str, dest_dir: Path, max_bytes: int) -> Path | None:
    outtmpl = str(dest_dir / "clip.%(ext)s")
    opts = _ytdl_download_opts(outtmpl, max_bytes)
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])
    except Exception as exc:
        log.info("Video download skipped (%s): %s", url[:80], exc)
        return None

    files = sorted(
        p
        for p in dest_dir.iterdir()
        if p.is_file() and p.suffix.lower() in {".mp4", ".webm", ".mov", ".mkv"}
    )
    if not files:
        files = [p for p in dest_dir.iterdir() if p.is_file() and p.stat().st_size > 0]
    if not files:
        return None
    path = files[0]
    size = path.stat().st_size
    if size > max_bytes or size < 1024:
        path.unlink(missing_ok=True)
        log.info("Downloaded video rejected (size=%s)", size)
        return None
    return path


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
