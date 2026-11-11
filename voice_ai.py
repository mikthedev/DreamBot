"""Voice AI — join VC, wake on \"Dream\", answer with Inworld + TTS."""

from __future__ import annotations

import asyncio
import audioop
import io
import logging
import re
import ssl
import tempfile
import time
import wave
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import aiohttp
import certifi
import discord
from discord import app_commands
from discord.ext import commands, tasks

import config
from ai import (
    AIError,
    ai_configured,
    detect_user_language,
    voice_reply_from_wav,
    whisper_configured,
)

log = logging.getLogger("dream_team.voice_ai")

_SSL_CTX = ssl.create_default_context(cafile=certifi.where())

SAMPLE_RATE = 48000
CHANNELS = 2
SAMPLE_WIDTH = 2
# End-of-phrase gate — lower = faster reply, higher = fewer mid-sentence cuts.
SILENCE_SECONDS = 0.35
MIN_UTTERANCE_SECONDS = 0.30
MAX_UTTERANCE_SECONDS = 10.0
RMS_THRESHOLD = 420
# Clip must have enough real speech (not just noise spikes)
MIN_VOICED_RATIO = 0.18
WAKE_COOLDOWN_SECONDS = 0.35
ALONE_LEAVE_SECONDS = 10.0
# Leave VC if nobody wakes Dream with the wake word
IDLE_LEAVE_SECONDS = 150.0  # 2.5 minutes
# Voice transcript embeds (text copies of what Dream said)
TRANSCRIPT_TTL_SECONDS = 24 * 60 * 60
TRANSCRIPT_CLEANUP_SECONDS = 15 * 60
WHISPER_RATE = 16000


def _patch_voice_recv_router() -> None:
    """Keep the packet router alive if a single Opus frame fails to decode."""
    try:
        from discord.ext.voice_recv import router as vr_router
    except ImportError:
        return

    if getattr(vr_router.PacketRouter, "_dream_team_patched", False):
        return

    def _do_run_safe(self) -> None:  # type: ignore[no-untyped-def]
        while not self._end_thread.is_set():
            self.waiter.wait()
            with self._lock:
                for decoder in list(self.waiter.items):
                    try:
                        data = decoder.pop_data()
                    except Exception as exc:
                        log.debug("Skipping bad voice frame: %s", exc)
                        try:
                            decoder.reset()
                        except Exception:
                            pass
                        continue
                    if data is not None:
                        try:
                            self.sink.write(data.source, data)
                        except Exception:
                            log.exception("Sink write failed")

    vr_router.PacketRouter._do_run = _do_run_safe  # type: ignore[method-assign]
    vr_router.PacketRouter._dream_team_patched = True  # type: ignore[attr-defined]
    log.info("Patched voice_recv packet router for Opus error resilience")


def _quiet_rtcp_logs() -> None:
    # SenderReportPacket spam is harmless and drowns real voice logs
    logging.getLogger("discord.ext.voice_recv").setLevel(logging.WARNING)
    logging.getLogger("discord.ext.voice_recv.reader").setLevel(logging.WARNING)


def _pcm_to_wav_bytes(
    pcm: bytes, *, rate: int = SAMPLE_RATE, channels: int = CHANNELS
) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(SAMPLE_WIDTH)
        wf.setframerate(rate)
        wf.writeframes(pcm)
    return buf.getvalue()


def _pcm_to_whisper_wav(pcm_stereo_48k: bytes) -> bytes:
    """Downmix Discord PCM to mono 16 kHz with gentle AGC for Whisper."""
    if not pcm_stereo_48k:
        return _pcm_to_wav_bytes(b"", rate=WHISPER_RATE, channels=1)
    try:
        mono = audioop.tomono(pcm_stereo_48k, SAMPLE_WIDTH, 0.5, 0.5)
        # Target a healthy speech level — Discord VC is often too quiet
        try:
            rms = audioop.rms(mono, SAMPLE_WIDTH) or 1
            target = 2800
            if 80 < rms < target:
                factor = min(3.5, target / rms)
                mono = audioop.mul(mono, SAMPLE_WIDTH, factor)
            # Soft peak guard after gain
            peak = audioop.max(mono, SAMPLE_WIDTH) or 1
            if peak > 30000:
                mono = audioop.mul(mono, SAMPLE_WIDTH, 30000 / peak)
        except Exception:
            pass
        mono16, _ = audioop.ratecv(
            mono, SAMPLE_WIDTH, 1, SAMPLE_RATE, WHISPER_RATE, None
        )
    except Exception:
        return _pcm_to_wav_bytes(pcm_stereo_48k)
    return _pcm_to_wav_bytes(mono16, rate=WHISPER_RATE, channels=1)


def _voice_recv_cls():
    from discord.ext import voice_recv

    return voice_recv.VoiceRecvClient


async def ensure_recv_voice(
    channel: discord.VoiceChannel, *, hear: bool = True
) -> discord.VoiceClient:
    """Connect (or reconnect) with VoiceRecvClient so the bot can hear."""
    guild = channel.guild
    cls = _voice_recv_cls()
    existing = guild.voice_client

    if existing and existing.is_connected():
        same_cls = existing.__class__.__name__ == "VoiceRecvClient"
        if existing.channel and existing.channel.id != channel.id:
            await existing.move_to(channel)
        if not same_cls:
            await existing.disconnect(force=True)
            await asyncio.sleep(0.4)
            return await channel.connect(
                cls=cls, reconnect=True, self_deaf=not hear, self_mute=False
            )
        try:
            await guild.change_voice_state(
                channel=channel, self_deaf=not hear, self_mute=False
            )
        except Exception as exc:
            log.debug("Could not update deaf state: %s", exc)
        return existing

    return await channel.connect(
        cls=cls, reconnect=True, self_deaf=not hear, self_mute=False
    )


class UtteranceBuffer:
    def __init__(self) -> None:
        self.chunks: deque[bytes] = deque()
        self.last_voice_at = 0.0
        self.quiet_since: float | None = None
        self.started_at = 0.0
        self.bytes_total = 0
        self.voiced_bytes = 0

    def add(self, pcm: bytes, now: float, *, voiced: bool) -> None:
        if not self.chunks:
            self.started_at = now
        self.chunks.append(pcm)
        self.bytes_total += len(pcm)
        if voiced:
            self.voiced_bytes += len(pcm)
            self.last_voice_at = now
            self.quiet_since = None
        elif self.quiet_since is None:
            self.quiet_since = now

    def duration(self, now: float | None = None) -> float:
        if not self.chunks:
            return 0.0
        end = now if now is not None else self.last_voice_at
        return max(0.0, end - self.started_at)

    def quiet_for(self, now: float) -> float:
        if self.quiet_since is None:
            return 0.0
        return max(0.0, now - self.quiet_since)

    def voiced_ratio(self) -> float:
        if self.bytes_total <= 0:
            return 0.0
        return self.voiced_bytes / self.bytes_total

    def flush(self) -> bytes:
        data = b"".join(self.chunks)
        self.chunks.clear()
        self.bytes_total = 0
        self.voiced_bytes = 0
        self.started_at = 0.0
        self.last_voice_at = 0.0
        self.quiet_since = None
        return data


def _build_sink_class():
    from discord.ext import voice_recv

    class WakeAudioSink(voice_recv.AudioSink):
        def __init__(self, cog: VoiceAICog, guild_id: int) -> None:
            super().__init__()
            self.cog = cog
            self.guild_id = guild_id
            self._buffers: dict[int, UtteranceBuffer] = defaultdict(UtteranceBuffer)
            self._packets_seen = 0

        def wants_opus(self) -> bool:
            # Library still DAVE-decrypts before handing us PCM when False.
            return False

        def write(self, user: discord.User | discord.Member | None, data) -> None:
            self._packets_seen += 1
            if self._packets_seen == 1:
                log.info("Voice packets flowing in guild %s", self.guild_id)
            if user is None or getattr(user, "bot", False):
                return
            # Only drop while Dream is speaking / cooling down — keep buffering
            # during Whisper so mid-sentence audio is not lost.
            if self.cog.is_paused(self.guild_id):
                return
            pcm = getattr(data, "pcm", None)
            if not pcm:
                return
            try:
                rms = audioop.rms(pcm, SAMPLE_WIDTH)
            except Exception:
                return

            now = time.monotonic()
            buf = self._buffers[user.id]
            voiced = rms >= RMS_THRESHOLD
            if voiced or buf.chunks:
                # Keep quiet frames inside an active utterance so pauses
                # between words don't chop the clip for Whisper.
                buf.add(pcm, now, voiced=voiced)

            max_bytes = int(
                SAMPLE_RATE * CHANNELS * SAMPLE_WIDTH * MAX_UTTERANCE_SECONDS
            )
            if buf.bytes_total > max_bytes:
                buf.quiet_since = now - SILENCE_SECONDS - 0.01

        def cleanup(self) -> None:
            self._buffers.clear()

        def pop_ready(self, user_id: int, now: float) -> bytes | None:
            buf = self._buffers.get(user_id)
            if buf is None or not buf.chunks:
                return None
            if buf.duration(now) < MIN_UTTERANCE_SECONDS:
                return None
            if (
                buf.quiet_for(now) < SILENCE_SECONDS
                and buf.duration(now) < MAX_UTTERANCE_SECONDS
            ):
                return None
            if buf.voiced_ratio() < MIN_VOICED_RATIO:
                # Mostly noise / silence padding — drop
                log.debug(
                    "Dropping low-voice clip guild=%s ratio=%.2f",
                    self.guild_id,
                    buf.voiced_ratio(),
                )
                buf.flush()
                return None
            return buf.flush()

        def ready_user_ids(self, now: float) -> list[int]:
            ready: list[int] = []
            for uid, buf in list(self._buffers.items()):
                if not buf.chunks:
                    continue
                if buf.duration(now) < MIN_UTTERANCE_SECONDS:
                    continue
                if (
                    buf.quiet_for(now) >= SILENCE_SECONDS
                    or buf.duration(now) >= MAX_UTTERANCE_SECONDS
                ):
                    ready.append(uid)
            return ready

    return WakeAudioSink


_TTS_LOCK: asyncio.Lock | None = None


def _tts_lock() -> asyncio.Lock:
    global _TTS_LOCK
    if _TTS_LOCK is None:
        _TTS_LOCK = asyncio.Lock()
    return _TTS_LOCK


def _tts_voice_for_text(text: str) -> str:
    """
    Pick Edge voice from the reply script — not detect_user_language().
    Latin hero names in a Russian sentence used to flip voice to EN, and
    GuyNeural then returns NoAudioReceived on Cyrillic.
    """
    t = text or ""
    if re.search(r"[іїєґІЇЄҐ]", t):
        return config.TTS_VOICE_UK
    cyr = len(re.findall(r"[а-яА-ЯёЁ]", t))
    latin = len(re.findall(r"[A-Za-z]", t))
    if cyr >= 2 and cyr >= latin:
        return config.TTS_VOICE_RU
    return config.TTS_VOICE


def _sanitize_tts_text(text: str) -> str:
    """Strip characters that make Edge TTS return empty audio."""
    import re as _re

    t = (text or "").strip()
    # Remove markup / XML-ish bits Edge embeds into SSML poorly
    t = _re.sub(r"[<>]", " ", t)
    t = _re.sub(r"https?://\S+", " ", t)
    # Drop most emoji / symbols; keep letters, digits, basic punctuation
    t = _re.sub(
        r"[^\w\s.,!?;:'\"%\-–—()+а-яА-ЯёЁіІїЇєЄґҐ]",
        " ",
        t,
        flags=_re.UNICODE,
    )
    t = _re.sub(r"\s{2,}", " ", t).strip()
    # Edge sometimes chokes on very long utterances
    if len(t) > 400:
        cut = t[:400]
        sp = cut.rfind(" ")
        t = (cut[:sp] if sp > 200 else cut).rstrip(" ,;:") + "."
    return t


async def synthesize_speech(text: str, out_path: Path) -> Path:
    import edge_tts

    from ai import AIError

    clean = _sanitize_tts_text(text)
    if not clean:
        raise AIError("Nothing to speak.")

    primary = _tts_voice_for_text(clean)
    # DmitryNeural occasionally returns empty audio for valid RU text;
    # Svetlana is the reliable RU fallback. Keep attempts short for latency.
    ru_alt = "ru-RU-SvetlanaNeural"
    attempts: list[tuple[str, str, str]] = [
        (primary, config.TTS_RATE, config.TTS_PITCH),
        (primary, "+0%", "+0Hz"),
    ]
    if primary == config.TTS_VOICE_RU and ru_alt != primary:
        attempts.append((ru_alt, "+0%", "+0Hz"))
    for alt in (config.TTS_VOICE_RU, config.TTS_VOICE_UK, config.TTS_VOICE):
        if alt != primary and all(alt != a[0] for a in attempts):
            attempts.append((alt, "+0%", "+0Hz"))
    if ru_alt not in {a[0] for a in attempts}:
        attempts.append((ru_alt, "+0%", "+0Hz"))

    last_exc: BaseException | None = None
    async with _tts_lock():
        for i, (voice, rate, pitch) in enumerate(attempts, start=1):
            try:
                communicate = edge_tts.Communicate(
                    clean,
                    voice,
                    rate=rate,
                    pitch=pitch,
                )
                await communicate.save(str(out_path))
                if out_path.is_file() and out_path.stat().st_size > 0:
                    if i > 1:
                        log.info(
                            "TTS recovered on attempt %s voice=%s", i, voice
                        )
                    return out_path
                raise RuntimeError("TTS returned an empty audio file.")
            except Exception as exc:
                last_exc = exc
                log.warning(
                    "TTS attempt %s failed voice=%s: %s", i, voice, exc
                )
                await asyncio.sleep(0.12)
    raise AIError(
        "Could not speak right now (TTS blocked). "
        "Try again in a moment."
    ) from last_exc


class VoiceAICog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._session: aiohttp.ClientSession | None = None
        self._listening: dict[int, Any] = {}
        self._paused_until: dict[int, float] = {}
        self._busy: set[int] = set()
        self._poll_task: asyncio.Task | None = None
        self._text_channel: dict[int, int] = {}
        # /join interaction — ephemeral transcripts ("Only you can see this")
        self._join_interaction: dict[int, discord.Interaction] = {}
        self._join_user_id: dict[int, int] = {}
        self._alone_timers: dict[int, asyncio.Task] = {}
        self._idle_deadline: dict[int, float] = {}
        self._idle_leave_pending: set[int] = set()

    async def cog_load(self) -> None:
        _quiet_rtcp_logs()
        _patch_voice_recv_router()
        self._session = aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(ssl=_SSL_CTX)
        )
        self._poll_task = asyncio.create_task(self._poll_loop())
        self._cleanup_transcripts.start()

    async def cog_unload(self) -> None:
        self._cleanup_transcripts.cancel()
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
        for guild_id in list(self._listening):
            await self._stop_listening(guild_id, leave=False)
        for guild_id in list(self._alone_timers):
            self._cancel_alone_timer(guild_id)
        self._idle_deadline.clear()
        self._idle_leave_pending.clear()
        if self._session and not self._session.closed:
            await self._session.close()

    def is_paused(self, guild_id: int) -> bool:
        return time.monotonic() < self._paused_until.get(guild_id, 0.0)

    def pause_listening(self, guild_id: int, seconds: float = 6.0) -> None:
        self._paused_until[guild_id] = time.monotonic() + seconds

    def _session_or_raise(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            raise AIError("AI session not ready.")
        return self._session

    async def _poll_loop(self) -> None:
        try:
            while True:
                # Tight poll — silence gate already decides readiness.
                await asyncio.sleep(0.08)
                now = time.monotonic()
                for guild_id, sink in list(self._listening.items()):
                    deadline = self._idle_deadline.get(guild_id)
                    if (
                        deadline is not None
                        and now >= deadline
                        and guild_id not in self._idle_leave_pending
                    ):
                        self._idle_leave_pending.add(guild_id)
                        asyncio.create_task(self._idle_leave_now(guild_id))
                    if self.is_paused(guild_id) or guild_id in self._busy:
                        continue
                    for user_id in sink.ready_user_ids(now):
                        asyncio.create_task(
                            self.handle_possible_utterance(guild_id, user_id)
                        )
        except asyncio.CancelledError:
            raise

    async def handle_possible_utterance(self, guild_id: int, user_id: int) -> None:
        if guild_id in self._busy or self.is_paused(guild_id):
            return
        sink = self._listening.get(guild_id)
        if sink is None:
            return
        pcm = sink.pop_ready(user_id, time.monotonic())
        if not pcm or len(pcm) < SAMPLE_RATE * CHANNELS * SAMPLE_WIDTH // 2:
            return

        self._busy.add(guild_id)
        try:
            wav = _pcm_to_whisper_wav(pcm)
            if len(wav) > 4_500_000:
                log.info("Voice clip too large in guild %s — skipped", guild_id)
                return

            t0 = time.monotonic()
            log.info(
                "Processing voice clip in guild %s user %s (%s bytes wav)",
                guild_id,
                user_id,
                len(wav),
            )
            # Stay quiet unless Dream / pending yes actually fires —
            # no "Hearing you…" spam while people chat normally.
            turn = await voice_reply_from_wav(
                wav,
                session=self._session_or_raise(),
                bot=self.bot,
                guild_id=guild_id,
                user_id=user_id,
            )
            if turn is None or (not turn.reply and not turn.leave):
                log.info(
                    "No wake / ignored clip (guild %s) in %.0fms",
                    guild_id,
                    (time.monotonic() - t0) * 1000,
                )
                return

            guild = self.bot.get_guild(guild_id)
            if guild is None:
                return
            member = guild.get_member(user_id)
            who = member.display_name if member else f"user {user_id}"
            think_ms = (time.monotonic() - t0) * 1000
            log.info(
                "Voice wake from %s in %s (think=%.0fms)",
                who,
                guild.name,
                think_ms,
            )

            self._bump_idle_timer(guild_id)
            reply = (turn.reply or "").strip()
            if reply:
                # Default: voice only. Text chat only if they asked for it.
                if getattr(turn, "show_text", False):
                    asyncio.create_task(
                        self._announce_text(guild_id, who, reply)
                    )
                tts_t0 = time.monotonic()
                await self._speak(guild, reply)
                log.info(
                    "Voice reply done guild=%s think=%.0fms speak=%.0fms total=%.0fms",
                    guild_id,
                    think_ms,
                    (time.monotonic() - tts_t0) * 1000,
                    (time.monotonic() - t0) * 1000,
                )
            if turn.leave:
                await self._leave_voice(guild_id)
                return
            self.pause_listening(guild_id, WAKE_COOLDOWN_SECONDS)
        except AIError as exc:
            log.warning("Voice AI: %s", exc)
            await self._announce_status(guild_id, f"Voice AI error: {exc}")
        except Exception:
            log.exception("Voice AI utterance failed")
            await self._announce_status(guild_id, "Voice AI failed — check bot logs.")
        finally:
            self._busy.discard(guild_id)
            # Short cooldown only after a real reply path finished speaking;
            # non-wake returns above skip long pause so the next phrase is kept.

    async def _announce_status(self, guild_id: int, text: str) -> None:
        channel_id = self._text_channel.get(guild_id)
        channel = self.bot.get_channel(channel_id) if channel_id else None
        if not isinstance(channel, discord.TextChannel):
            return
        try:
            await channel.send(text, delete_after=12)
        except (discord.Forbidden, discord.HTTPException):
            pass

    async def _announce_text(self, guild_id: int, who: str, reply: str) -> None:
        """Post the spoken answer in text chat — only when the user asked for text."""
        embed = discord.Embed(
            title="Dream",
            description=reply[:4096],
            color=discord.Color.from_rgb(46, 230, 166),
        )
        embed.set_footer(text=f"Voice reply for {who}")

        # Prefer the configured voice-log channel, else the /join text channel.
        channel = None
        try:
            log_id = self.bot.db.get_voice_log_channel(guild_id)
        except Exception:
            log_id = None
        if log_id:
            channel = self.bot.get_channel(int(log_id))
        if not isinstance(channel, discord.TextChannel):
            channel_id = self._text_channel.get(guild_id)
            channel = self.bot.get_channel(channel_id) if channel_id else None
        if not isinstance(channel, discord.TextChannel):
            return
        try:
            msg = await channel.send(embed=embed)
            try:
                delete_at = datetime.now(timezone.utc) + timedelta(
                    seconds=TRANSCRIPT_TTL_SECONDS
                )
                self.bot.db.schedule_voice_log_delete(
                    guild_id,
                    channel.id,
                    msg.id,
                    delete_at,
                )
            except Exception:
                log.debug("Could not schedule voice text delete", exc_info=True)
        except (discord.Forbidden, discord.HTTPException) as exc:
            log.info("Voice text post failed guild=%s: %s", guild_id, exc)

    @tasks.loop(seconds=TRANSCRIPT_CLEANUP_SECONDS)
    async def _cleanup_transcripts(self) -> None:
        try:
            due = self.bot.db.list_due_voice_log_deletes()
        except Exception:
            log.exception("Failed listing voice transcript deletes")
            return
        for row in due:
            channel_id = int(row["channel_id"])
            message_id = int(row["message_id"])
            channel = self.bot.get_channel(channel_id)
            # Text channel or DM
            if channel is not None and hasattr(channel, "fetch_message"):
                try:
                    msg = await channel.fetch_message(message_id)
                    await msg.delete()
                except discord.NotFound:
                    pass
                except (discord.Forbidden, discord.HTTPException) as exc:
                    log.debug(
                        "Could not delete voice transcript %s/%s: %s",
                        channel_id,
                        message_id,
                        exc,
                    )
            try:
                self.bot.db.remove_voice_log_message(channel_id, message_id)
            except Exception:
                log.exception("Failed removing voice transcript row")

    @_cleanup_transcripts.before_loop
    async def _cleanup_transcripts_before(self) -> None:
        await self.bot.wait_until_ready()

    async def _speak(self, guild: discord.Guild, text: str) -> None:
        voice = guild.voice_client
        if voice is None or not voice.is_connected():
            return

        music = self.bot.get_cog("MusicCog")
        if music is not None:
            player = getattr(music, "get_player", lambda _gid: None)(guild.id)
            if player is not None and getattr(player, "current", None) is not None:
                log.info("Skipping TTS while music is playing in %s", guild.name)
                return

        # Pause only while synthesizing + playing (avoid hearing ourselves).
        self.pause_listening(guild.id, 30.0)
        tmp = Path(tempfile.mkdtemp(prefix="dream_tts_"))
        mp3 = tmp / "reply.mp3"
        try:
            await synthesize_speech(text, mp3)
            if voice.is_playing():
                voice.stop()
            done = asyncio.Event()

            def _after(err: Exception | None) -> None:
                if err:
                    log.warning("TTS playback error: %s", err)
                self.bot.loop.call_soon_threadsafe(done.set)

            # Low-latency decode — skip probing fluff.
            source = discord.FFmpegPCMAudio(
                str(mp3),
                before_options="-nostdin",
                options="-vn -ac 2 -ar 48000",
            )
            voice.play(source, after=_after)
            try:
                await asyncio.wait_for(done.wait(), timeout=90)
            except asyncio.TimeoutError:
                voice.stop()
        finally:
            try:
                for p in tmp.glob("*"):
                    p.unlink(missing_ok=True)
                tmp.rmdir()
            except OSError:
                pass
            self.pause_listening(guild.id, WAKE_COOLDOWN_SECONDS)

    async def _stop_listening(self, guild_id: int, *, leave: bool) -> None:
        guild = self.bot.get_guild(guild_id)
        sink = self._listening.pop(guild_id, None)
        self._text_channel.pop(guild_id, None)
        self._join_user_id.pop(guild_id, None)
        self._join_interaction.pop(guild_id, None)
        if guild and guild.voice_client:
            vc = guild.voice_client
            stop_listening = getattr(vc, "stop_listening", None)
            if callable(stop_listening):
                try:
                    stop_listening()
                except Exception:
                    pass
            if leave:
                await vc.disconnect(force=True)
        if sink is not None:
            try:
                sink.cleanup()
            except Exception:
                pass

    @app_commands.command(
        name="join",
        description="Join your VC and answer when you say Dream …",
    )
    async def join_cmd(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                "Use this in a server.", ephemeral=True
            )
            return
        if not ai_configured():
            await interaction.response.send_message(
                "Set `INWORLD_API_KEY` in `.env` first "
                "(Inworld Portal → API key).",
                ephemeral=True,
            )
            return
        if not whisper_configured():
            await interaction.response.send_message(
                "Voice needs `GROQ_API_KEY` for Whisper STT "
                "(https://console.groq.com/keys). Text `/ask` still works.",
                ephemeral=True,
            )
            return
        if interaction.user.voice is None or interaction.user.voice.channel is None:
            await interaction.response.send_message(
                "Join a voice channel first.", ephemeral=True
            )
            return
        channel = interaction.user.voice.channel
        if not isinstance(channel, discord.VoiceChannel):
            await interaction.response.send_message(
                "I can only listen in regular voice channels.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)
        try:
            _voice_recv_cls()
        except ImportError:
            await interaction.followup.send(
                "Voice listen needs `discord-ext-voice-recv`. "
                "Run `pip install discord-ext-voice-recv edge-tts`.",
                ephemeral=True,
            )
            return

        try:
            vc = await ensure_recv_voice(channel, hear=True)
        except Exception as exc:
            log.exception("Listen connect failed: %s", exc)
            await interaction.followup.send(
                f"Could not join voice: {exc}", ephemeral=True
            )
            return

        SinkCls = _build_sink_class()
        sink = SinkCls(self, interaction.guild.id)
        if interaction.guild.id in self._listening:
            stop_listening = getattr(vc, "stop_listening", None)
            if callable(stop_listening):
                try:
                    stop_listening()
                except Exception:
                    pass

        listen_fn = getattr(vc, "listen", None)
        if not callable(listen_fn):
            await interaction.followup.send(
                "Connected, but this voice client cannot receive audio.",
                ephemeral=True,
            )
            return

        def _after_listen(err: Exception | None) -> None:
            if err:
                log.warning("Listen stopped with error: %s", err)
            # Auto-restart listen if we still expect to be listening
            if interaction.guild_id in self._listening:

                def _restart() -> None:
                    asyncio.create_task(self._restart_listen(interaction.guild_id))

                try:
                    self.bot.loop.call_soon_threadsafe(_restart)
                except RuntimeError:
                    pass

        listen_fn(sink, after=_after_listen)
        self._listening[interaction.guild.id] = sink
        self._join_user_id[interaction.guild.id] = interaction.user.id
        self._join_interaction[interaction.guild.id] = interaction
        if isinstance(interaction.channel, discord.TextChannel):
            self._text_channel[interaction.guild.id] = interaction.channel.id

        music = self.bot.get_cog("MusicCog")
        if music is not None and hasattr(music, "get_player"):
            player = music.get_player(interaction.guild.id)
            if player is not None:
                player.voice = vc

        # Confirm DAVE session is ready (needed to decrypt voice on discord.py 2.7+)
        conn = getattr(vc, "_connection", None)
        dave = getattr(conn, "dave_session", None) if conn else None
        dave_ver = getattr(conn, "dave_protocol_version", None) if conn else None
        log.info(
            "Listen started guild=%s dave_ready=%s dave_ver=%s join_user=%s",
            interaction.guild.id,
            bool(dave and getattr(dave, "ready", False)),
            dave_ver,
            interaction.user.id,
        )

        self._bump_idle_timer(interaction.guild.id)

        # Warm patch-notes cache so the first hero question is faster
        async def _warm_patches() -> None:
            try:
                from overwatch_patches import fetch_all_patch_summaries

                await fetch_all_patch_summaries(limit=20, max_months=3)
            except Exception:
                log.debug("Patch cache warm failed", exc_info=True)

        async def _warm_tts() -> None:
            # First Edge handshake is the slow one — pay it on /join, not on reply.
            try:
                warm = Path(tempfile.mkdtemp(prefix="dream_tts_warm_")) / "w.mp3"
                await synthesize_speech("ok", warm)
                warm.unlink(missing_ok=True)
                warm.parent.rmdir()
            except Exception:
                log.debug("TTS warm failed", exc_info=True)

        asyncio.create_task(_warm_patches())
        asyncio.create_task(_warm_tts())

        await interaction.followup.send(
            f"Listening in **{channel.name}**. Say **Dream, …** to talk "
            f"(~90s follow-ups without repeating Dream). "
            f"I answer in **voice** only — say **show as text** / **напиши текстом** "
            f"if you also want it in chat. "
            f"To leave: **Dream leave** / **disconnect** / **выйди** / **вийди** / "
            f"**thank Dream** (EN/RU/UK). Idle 2.5 min → I leave. "
            f"Use `/disconnect` anytime.",
            ephemeral=True,
        )

    async def _restart_listen(self, guild_id: int) -> None:
        await asyncio.sleep(0.6)
        if guild_id not in self._listening:
            return
        guild = self.bot.get_guild(guild_id)
        if guild is None or guild.voice_client is None:
            return
        vc = guild.voice_client
        if not vc.is_connected():
            return
        if getattr(vc, "is_listening", lambda: False)():
            return
        SinkCls = _build_sink_class()
        sink = SinkCls(self, guild_id)
        listen_fn = getattr(vc, "listen", None)
        if not callable(listen_fn):
            return

        def _after(err: Exception | None) -> None:
            if err:
                log.warning("Listen stopped again: %s", err)
            if guild_id in self._listening:
                try:
                    self.bot.loop.call_soon_threadsafe(
                        lambda: asyncio.create_task(self._restart_listen(guild_id))
                    )
                except RuntimeError:
                    pass

        try:
            listen_fn(sink, after=_after)
            self._listening[guild_id] = sink
            log.info("Re-started voice listen in guild %s", guild_id)
        except Exception:
            log.exception("Failed to restart listen in guild %s", guild_id)

    @app_commands.command(
        name="disconnect",
        description="Leave the voice channel (stops music and Dream voice AI)",
    )
    async def disconnect_cmd(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                "Use this in a server.", ephemeral=True
            )
            return

        guild_id = interaction.guild.id
        was_listening = guild_id in self._listening
        vc = interaction.guild.voice_client
        was_connected = bool(vc and vc.is_connected())

        await self._leave_voice(guild_id)

        if was_connected or was_listening:
            await interaction.response.send_message(
                "Disconnected from voice.", ephemeral=True
            )
        else:
            await interaction.response.send_message(
                "I'm not in a voice channel.", ephemeral=True
            )

    async def _leave_voice(self, guild_id: int) -> None:
        """Stop wake listening, stop music, and disconnect from VC."""
        self._cancel_alone_timer(guild_id)
        self._cancel_idle_timer(guild_id)

        await self._stop_listening(guild_id, leave=False)

        music = self.bot.get_cog("MusicCog")
        if music is not None and hasattr(music, "get_player"):
            player = music.get_player(guild_id)
            if player is not None:
                try:
                    await player.stop()
                    return
                except Exception:
                    log.exception("Music stop failed during disconnect")

        guild = self.bot.get_guild(guild_id)
        if guild and guild.voice_client and guild.voice_client.is_connected():
            try:
                await guild.voice_client.disconnect(force=True)
            except Exception:
                pass

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        # Re-check every guild voice channel the bot is in
        guild = member.guild
        vc = guild.voice_client
        if vc is None or not vc.is_connected() or vc.channel is None:
            self._cancel_alone_timer(guild.id)
            return

        if self._humans_in_channel(vc.channel) == 0:
            self._schedule_alone_leave(guild.id)
        else:
            self._cancel_alone_timer(guild.id)

    def _humans_in_channel(self, channel: discord.VoiceChannel | discord.StageChannel) -> int:
        return sum(1 for m in channel.members if not m.bot)

    def _cancel_alone_timer(self, guild_id: int) -> None:
        task = self._alone_timers.pop(guild_id, None)
        if task and not task.done():
            task.cancel()

    def _schedule_alone_leave(self, guild_id: int) -> None:
        if guild_id in self._alone_timers and not self._alone_timers[guild_id].done():
            return
        self._alone_timers[guild_id] = asyncio.create_task(
            self._alone_leave_after(guild_id, ALONE_LEAVE_SECONDS)
        )

    async def _alone_leave_after(self, guild_id: int, delay: float) -> None:
        try:
            await asyncio.sleep(delay)
            guild = self.bot.get_guild(guild_id)
            if guild is None:
                return
            vc = guild.voice_client
            if vc is None or not vc.is_connected() or vc.channel is None:
                return
            if self._humans_in_channel(vc.channel) > 0:
                return
            log.info(
                "Leaving VC in %s — alone for %.0fs",
                guild.name,
                delay,
            )
            await self._leave_voice(guild_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Alone-leave failed in guild %s", guild_id)
        finally:
            self._alone_timers.pop(guild_id, None)

    def _cancel_idle_timer(self, guild_id: int) -> None:
        self._idle_deadline.pop(guild_id, None)
        self._idle_leave_pending.discard(guild_id)

    def _bump_idle_timer(self, guild_id: int) -> None:
        """Reset the 2.5‑min no-wake leave clock (join or successful Dream)."""
        self._idle_deadline[guild_id] = time.monotonic() + IDLE_LEAVE_SECONDS
        self._idle_leave_pending.discard(guild_id)
        log.info(
            "Idle leave armed guild=%s in %.0fs",
            guild_id,
            IDLE_LEAVE_SECONDS,
        )

    async def _idle_leave_now(self, guild_id: int) -> None:
        try:
            deadline = self._idle_deadline.get(guild_id)
            if deadline is None or time.monotonic() < deadline:
                return
            guild = self.bot.get_guild(guild_id)
            if guild is None:
                return
            vc = guild.voice_client
            if vc is None or not vc.is_connected():
                return
            if guild_id not in self._listening:
                return
            log.info(
                "Leaving VC in %s — no Dream wake for %.0fs",
                guild.name,
                IDLE_LEAVE_SECONDS,
            )
            await self._announce_status(
                guild_id,
                "Left voice — nobody said **Dream** for 2.5 minutes.",
            )
            await self._leave_voice(guild_id)
        except Exception:
            log.exception("Idle-leave failed in guild %s", guild_id)
        finally:
            self._idle_leave_pending.discard(guild_id)
            self._idle_deadline.pop(guild_id, None)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(VoiceAICog(bot))
