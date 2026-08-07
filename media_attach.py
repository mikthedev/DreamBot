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


def _size_of(fmt: dict, duration: float | None = None) -> int | None:
    for key in ("filesize", "filesize_approx"):
        val = fmt.get(key)
        if val is not None:
            try:
                return int(val)
            except (TypeError, ValueError):
                pass
    # HLS often has no filesize — estimate from bitrate × duration
    tbr = fmt.get("tbr") or fmt.get("vbr") or fmt.get("abr")
    if tbr is not None and duration and duration > 0:
        try:
            return int(float(tbr) * 1000 / 8 * float(duration))
        except (TypeError, ValueError):
            pass
    return None


def _pick_format_for_budget(info: dict, max_bytes: int) -> str | None:
    """
    Choose the best quality that should fit under max_bytes.
    Prefer progressive; else HLS video+audio pairs. Fall back to lowest height.
    """
    formats = [f for f in (info.get("formats") or []) if isinstance(f, dict)]
    if not formats:
        return None

    try:
        duration = float(info["duration"]) if info.get("duration") is not None else None
    except (TypeError, ValueError):
        duration = None

    budget = int(max_bytes * 0.92)  # leave headroom for mux/container

    progressive: list[tuple[int, int, dict]] = []
    for fmt in formats:
        if fmt.get("vcodec") in (None, "none"):
            continue
        if fmt.get("acodec") in (None, "none"):
            continue
        size = _size_of(fmt, duration)
        height = int(fmt.get("height") or 0)
        if size is not None and size > budget:
            continue
        # Unknown size: only keep lower ladders so we don't blow the upload cap
        if size is None and height > 480:
            continue
        progressive.append((height, size or 0, fmt))

    if progressive:
        progressive.sort(key=lambda x: (-x[0], x[1]))
        fid = progressive[0][2].get("format_id")
        if fid:
            return str(fid)

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
        audio_only.sort(key=lambda f: float(f.get("abr") or f.get("tbr") or 0))
        af = audio_only[0]
        asize = _size_of(af, duration) or 0
        candidates: list[tuple[int, int, dict]] = []
        for vf in video_only:
            height = int(vf.get("height") or 0)
            vs = _size_of(vf, duration)
            total = (vs or 0) + asize
            if vs is not None and total > budget:
                continue
            if vs is None and height > 480:
                continue
            candidates.append((height, total if vs is not None else 10**12, vf))
        if candidates:
            candidates.sort(key=lambda x: (-x[0], x[1]))
            vf = candidates[0][2]
            return f"{vf.get('format_id')}+{af.get('format_id')}"
        # Nothing estimated under budget — take the smallest video rung
        video_only.sort(key=lambda f: (f.get("height") or 0, _size_of(f, duration) or 0))
        vf = video_only[0]
        return f"{vf.get('format_id')}+{af.get('format_id')}"

    return None


def _format_ladder(max_bytes: int) -> list[str]:
    """yt-dlp format strings from high → low quality for retry-on-size."""
    # Aim for ~max_bytes; lower heights for small Discord limits (10 MB)
    if max_bytes <= 12 * 1024 * 1024:
        heights = (480, 360, 240, 144)
    elif max_bytes <= 20 * 1024 * 1024:
        heights = (720, 480, 360, 240)
    else:
        heights = (720, 480, 360)
    ladder: list[str] = []
    for h in heights:
        ladder.append(
            f"best[height<={h}][ext=mp4]/"
            f"best[height<={h}]/"
            f"bv*[height<={h}]+ba/"
            f"worst[height<={h}]"
        )
    ladder.append("worst")
    return ladder


def _ytdl_opts(outtmpl: str, *, format_selector: str) -> dict:
    return {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "format": format_selector,
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


def _shrink_with_ffmpeg(src: Path, dest: Path, max_bytes: int) -> Path | None:
    """Re-encode smaller if Discord's upload cap is tight (e.g. 10 MB non-boosted)."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return None
    dest.unlink(missing_ok=True)
    # Target ~85% of budget so Discord accepts it
    target = max(512_000, int(max_bytes * 0.85))
    cmd = [
        ffmpeg,
        "-y",
        "-i",
        str(src),
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "28",
        "-vf",
        "scale='min(854,iw)':-2",
        "-c:a",
        "aac",
        "-b:a",
        "96k",
        "-movflags",
        "+faststart",
        "-fs",
        str(target),
        str(dest),
    ]
    try:
        proc = __import__("subprocess").run(
            cmd,
            capture_output=True,
            timeout=120,
            check=False,
        )
    except Exception as exc:
        log.info("ffmpeg shrink failed to start: %s", exc)
        return None
    if not dest.is_file() or dest.stat().st_size < 1024:
        log.info(
            "ffmpeg shrink produced nothing (code=%s): %s",
            proc.returncode,
            (proc.stderr or b"")[-400:],
        )
        dest.unlink(missing_ok=True)
        return None
    if dest.stat().st_size > max_bytes:
        dest.unlink(missing_ok=True)
        return None
    log.info(
        "ffmpeg shrunk video %s → %s bytes (limit %s)",
        src.stat().st_size,
        dest.stat().st_size,
        max_bytes,
    )
    return dest


def _download_with_ytdl(url: str, dest_dir: Path, max_bytes: int) -> Path | None:
    """Download Bluesky HLS / other non-YouTube streams under the Discord size cap."""
    outtmpl = str(dest_dir / "clip.%(ext)s")
    last_exc: Exception | None = None

    # Probe once for duration / format ids
    try:
        probe_opts = _ytdl_opts(outtmpl, format_selector="best")
        with yt_dlp.YoutubeDL(probe_opts) as ydl:
            info = _unwrap_info(ydl.extract_info(url, download=False))
    except Exception as exc:
        log.warning("yt-dlp probe failed: %s", exc)
        return None

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
        log.warning("yt-dlp: no formats for %s", url[:80])
        return None

    selectors: list[str] = []
    picked = _pick_format_for_budget(info, max_bytes)
    if picked:
        selectors.append(picked)
    selectors.extend(_format_ladder(max_bytes))

    seen: set[str] = set()
    for selector in selectors:
        if selector in seen:
            continue
        seen.add(selector)
        for leftover in list(dest_dir.iterdir()):
            if leftover.is_file():
                leftover.unlink(missing_ok=True)
        try:
            opts = _ytdl_opts(outtmpl, format_selector=selector)
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([url])
            path = _find_output_file(dest_dir)
            if path is None:
                raise RuntimeError("download produced no file")
            size = path.stat().st_size
            if size < 1024:
                path.unlink(missing_ok=True)
                raise RuntimeError("download file too small")
            if size <= max_bytes:
                log.info(
                    "Video downloaded (%s bytes, format=%s) → %s",
                    size,
                    selector[:60],
                    path.name,
                )
                return path
            # Too big for this guild — try ffmpeg shrink, then lower ladder
            shrunk = _shrink_with_ffmpeg(
                path, dest_dir / "clip_small.mp4", max_bytes
            )
            path.unlink(missing_ok=True)
            if shrunk is not None:
                return shrunk
            log.info(
                "Downloaded video too large (%s > %s) via %s — trying lower quality",
                size,
                max_bytes,
                selector[:40],
            )
        except Exception as exc:
            last_exc = exc
            log.warning("yt-dlp video try failed (%s): %s", selector[:40], exc)

    log.info(
        "Video download skipped (%s): %s",
        url[:80],
        last_exc or "all qualities over Discord upload limit",
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
    log.info(
        "Video attach budget %s bytes (guild limit / config)",
        max_bytes,
    )
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
