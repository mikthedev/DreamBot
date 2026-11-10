#!/usr/bin/env python3
"""
Standalone voice playback smoke test.

Usage (join a voice channel first, then):
  python test_voice_play.py
  python test_voice_play.py --channel CHANNEL_ID
  python test_voice_play.py --url 'https://soundcloud.com/…'

Plays a short tone (or a SoundCloud/Spotify URL) so you can tell whether
Discord voice + FFmpeg + DAVE are working without the full music cog.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import shutil
import struct
import tempfile
import wave
from pathlib import Path

import discord
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("dream_team.voice_test")


def _token() -> str:
    for name in ("DISCORD_TOKEN", "TOKEN", "BOT_TOKEN", "DISCORD_BOT_TOKEN"):
        val = (os.getenv(name) or "").strip()
        if val:
            return val
    raise SystemExit("Set DISCORD_TOKEN in .env")


def _ffmpeg() -> str:
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
    raise SystemExit("ffmpeg not found on PATH")


def _ensure_opus() -> bool:
    if discord.opus.is_loaded():
        return True
    for name in (
        "opus",
        "libopus.so.0",
        "libopus.dylib",
        "/opt/homebrew/lib/libopus.dylib",
        "/usr/local/lib/libopus.dylib",
        "/usr/lib/x86_64-linux-gnu/libopus.so.0",
    ):
        try:
            discord.opus.load_opus(name)
            if discord.opus.is_loaded():
                log.info("Loaded Opus via %s", name)
                return True
        except Exception:
            continue
    return discord.opus.is_loaded()


def _write_tone_wav(path: Path, *, seconds: float = 4.0, hz: float = 440.0) -> None:
    rate = 48000
    channels = 2
    amp = 12000
    n = int(rate * seconds)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        frames = bytearray()
        for i in range(n):
            # Simple square-ish beep so it's obvious in Discord
            import math

            sample = int(amp * math.sin(2 * math.pi * hz * (i / rate)))
            frames.extend(struct.pack("<hh", sample, sample))
        wf.writeframes(frames)


def _print_env() -> None:
    log.info("discord.py %s", discord.__version__)
    try:
        import davey

        log.info("davey %s", getattr(davey, "__version__", "present"))
    except Exception as exc:
        log.error("davey MISSING — voice will be silent on modern Discord: %s", exc)
    log.info("opus loaded=%s", _ensure_opus())
    log.info("ffmpeg=%s", _ffmpeg())
    ver = tuple(int(p) for p in discord.__version__.split(".")[:2])
    if ver < (2, 7):
        log.error(
            "discord.py %s has no DAVE support. Need >= 2.7.0 "
            "(pip install -U 'discord.py[voice]').",
            discord.__version__,
        )


async def _resolve_url(query: str) -> str:
    """Return a direct stream URL, or the original path if it's a local file."""
    if Path(query).is_file():
        return query
    from music import resolve_stream_url, resolve_track

    class _Req:
        display_name = "voice-test"
        display_avatar = type("A", (), {"url": "https://cdn.discordapp.com/embed/avatars/0.png"})()

    track = await resolve_track(query, _Req())  # type: ignore[arg-type]
    log.info("Resolved %s (%s) → %s", track.title, track.source, track.query)
    return await resolve_stream_url(track)


def main() -> None:
    parser = argparse.ArgumentParser(description="Dream Team voice playback smoke test")
    parser.add_argument(
        "--channel",
        type=int,
        default=0,
        help="Voice channel ID (default: first channel you're in)",
    )
    parser.add_argument(
        "--url",
        default="",
        help="SoundCloud/Spotify URL or local file (default: generated beep)",
    )
    parser.add_argument(
        "--seconds",
        type=float,
        default=6.0,
        help="How long to keep the connection up after play starts",
    )
    args = parser.parse_args()
    _print_env()

    intents = discord.Intents.default()
    intents.guilds = True
    intents.voice_states = True
    bot = commands.Bot(command_prefix="!", intents=intents)

    @bot.event
    async def on_ready() -> None:
        log.info("Logged in as %s (%s)", bot.user, bot.user and bot.user.id)
        channel: discord.VoiceChannel | None = None
        if args.channel:
            ch = bot.get_channel(args.channel)
            if isinstance(ch, discord.VoiceChannel):
                channel = ch
        else:
            me = bot.user
            for guild in bot.guilds:
                for vc in guild.voice_channels:
                    if me and any(m.id == me.id for m in vc.members):
                        continue
                    # Prefer a channel that has a human member
                    humans = [m for m in vc.members if not m.bot]
                    if humans:
                        channel = vc
                        break
                if channel:
                    break

        if channel is None:
            log.error(
                "No voice channel found. Join one, or pass --channel ID. "
                "Guilds: %s",
                [g.name for g in bot.guilds],
            )
            await bot.close()
            return

        log.info("Connecting to #%s (%s) in %s", channel.name, channel.id, channel.guild.name)
        voice = await channel.connect(reconnect=True, self_deaf=True)
        await asyncio.sleep(0.5)

        tmp_path: Path | None = None
        try:
            if args.url:
                source_path = await _resolve_url(args.url.strip())
                log.info("Playing URL/file: %s", source_path[:120])
                headers = (
                    "User-Agent: Mozilla/5.0\r\n"
                    "Referer: https://soundcloud.com/\r\n"
                )
                before = (
                    "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 "
                    f"-headers '{headers}'"
                )
                source = discord.FFmpegPCMAudio(
                    source_path,
                    executable=_ffmpeg(),
                    before_options=before if source_path.startswith("http") else None,
                    options="-vn",
                )
            else:
                tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
                tmp_path = Path(tmp.name)
                tmp.close()
                _write_tone_wav(tmp_path, seconds=min(args.seconds, 8.0))
                log.info("Playing generated tone: %s", tmp_path)
                source = discord.FFmpegPCMAudio(str(tmp_path), executable=_ffmpeg())

            done = asyncio.Event()

            def after(err: Exception | None) -> None:
                if err:
                    log.error("Playback error: %r", err)
                else:
                    log.info("Playback finished cleanly")
                bot.loop.call_soon_threadsafe(done.set)

            voice.play(source, after=after)
            log.info(
                "voice.is_playing=%s — you should hear audio in #%s now",
                voice.is_playing(),
                channel.name,
            )
            try:
                await asyncio.wait_for(done.wait(), timeout=max(args.seconds, 3.0))
            except asyncio.TimeoutError:
                log.info("Stopping after %.1fs timeout", args.seconds)
                if voice.is_playing():
                    voice.stop()
                    await asyncio.sleep(0.3)
        finally:
            if voice.is_connected():
                await voice.disconnect()
            if tmp_path is not None:
                tmp_path.unlink(missing_ok=True)
            await bot.close()

    bot.run(_token(), log_handler=None)


if __name__ == "__main__":
    main()
