"""Voice AI — join VC, wake on \"Dream\", answer with free Llama + TTS."""

from __future__ import annotations

import asyncio
import audioop
import io
import logging
import tempfile
import time
import wave
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks

import config
from ai import AIError, ai_configured, looks_cyrillic, voice_reply_from_wav

log = logging.getLogger("dream_team.voice_ai")

SAMPLE_RATE = 48000
CHANNELS = 2
SAMPLE_WIDTH = 2
# Wait for a clear end-of-phrase pause, but keep it snappy for voice latency.
SILENCE_SECONDS = 0.95
MIN_UTTERANCE_SECONDS = 0.55
MAX_UTTERANCE_SECONDS = 14.0
RMS_THRESHOLD = 260
WAKE_COOLDOWN_SECONDS = 1.8
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
    """Downmix Discord PCM to mono 16 kHz — Whisper hears this much more clearly."""
    if not pcm_stereo_48k:
        return _pcm_to_wav_bytes(b"", rate=WHISPER_RATE, channels=1)
    try:
        mono = audioop.tomono(pcm_stereo_48k, SAMPLE_WIDTH, 0.5, 0.5)
        # Mild gain — Discord VC is often quiet for Whisper
        try:
            rms = audioop.rms(mono, SAMPLE_WIDTH) or 1
            if rms < 1200:
                factor = min(4.0, 1200 / rms)
                mono = audioop.mul(mono, SAMPLE_WIDTH, factor)
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

    def add(self, pcm: bytes, now: float, *, voiced: bool) -> None:
        if not self.chunks:
            self.started_at = now
        self.chunks.append(pcm)
        self.bytes_total += len(pcm)
        if voiced:
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

    def flush(self) -> bytes:
        data = b"".join(self.chunks)
        self.chunks.clear()
        self.bytes_total = 0
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
            if buf.quiet_for(now) < SILENCE_SECONDS and buf.duration(now) < MAX_UTTERANCE_SECONDS:
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


async def synthesize_speech(text: str, out_path: Path) -> Path:
    import edge_tts

    # Masculine + a bit more punchy; Russian → Dmitry
    if looks_cyrillic(text):
        voice = config.TTS_VOICE_RU
    else:
        voice = config.TTS_VOICE
    communicate = edge_tts.Communicate(
        text,
        voice,
        rate=config.TTS_RATE,
        pitch=config.TTS_PITCH,
    )
    await communicate.save(str(out_path))
    return out_path


class VoiceAICog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._session: aiohttp.ClientSession | None = None
        self._listening: dict[int, Any] = {}
        self._paused_until: dict[int, float] = {}
        self._busy: set[int] = set()
        self._poll_task: asyncio.Task | None = None
        self._text_channel: dict[int, int] = {}
        self._alone_timers: dict[int, asyncio.Task] = {}
        self._idle_timers: dict[int, asyncio.Task] = {}

    async def cog_load(self) -> None:
        _quiet_rtcp_logs()
        _patch_voice_recv_router()
        self._session = aiohttp.ClientSession()
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
        for guild_id in list(self._idle_timers):
            self._cancel_idle_timer(guild_id)
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
                await asyncio.sleep(0.25)
                now = time.monotonic()
                for guild_id, sink in list(self._listening.items()):
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

            log.info(
                "Processing voice clip in guild %s user %s (%s bytes wav)",
                guild_id,
                user_id,
                len(wav),
            )
            # Stay quiet unless Dream / pending yes actually fires —
            # no "Hearing you…" spam while people chat normally.
            reply = await voice_reply_from_wav(
                wav,
                session=self._session_or_raise(),
                bot=self.bot,
                guild_id=guild_id,
                user_id=user_id,
            )
            if not reply:
                log.info("No wake word in clip (guild %s)", guild_id)
                return

            guild = self.bot.get_guild(guild_id)
            if guild is None:
                return
            member = guild.get_member(user_id)
            who = member.display_name if member else f"user {user_id}"
            log.info("Voice wake from %s in %s", who, guild.name)

            self._bump_idle_timer(guild_id)
            # Speak + text log in parallel to cut perceived latency
            await asyncio.gather(
                self._speak(guild, reply),
                self._announce_text(guild_id, who, reply),
            )
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

    def _transcript_channel(self, guild_id: int) -> discord.TextChannel | None:
        """Admin-configured voice log channel, else the channel used for /join."""
        configured = self.bot.db.get_voice_log_channel(guild_id)
        channel_id = configured or self._text_channel.get(guild_id)
        if not channel_id:
            return None
        channel = self.bot.get_channel(channel_id)
        return channel if isinstance(channel, discord.TextChannel) else None

    async def _announce_text(self, guild_id: int, who: str, reply: str) -> None:
        channel = self._transcript_channel(guild_id)
        if channel is None:
            return
        embed = discord.Embed(
            title="Dream (voice)",
            description=reply[:4096],
            color=discord.Color.from_rgb(46, 230, 166),
        )
        embed.set_footer(text=f"Heard {who} · auto-deletes in 24h")
        try:
            msg = await channel.send(embed=embed)
        except (discord.Forbidden, discord.HTTPException):
            return
        delete_at = datetime.now(timezone.utc) + timedelta(seconds=TRANSCRIPT_TTL_SECONDS)
        try:
            self.bot.db.schedule_voice_log_delete(
                guild_id, channel.id, msg.id, delete_at
            )
        except Exception:
            log.exception("Could not schedule voice transcript delete")

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
            if isinstance(channel, discord.TextChannel):
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

            source = discord.FFmpegPCMAudio(str(mp3))
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
                "Set a free `GROQ_API_KEY` in `.env` first "
                "(https://console.groq.com/keys — no billing).",
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
            "Listen started guild=%s dave_ready=%s dave_ver=%s",
            interaction.guild.id,
            bool(dave and getattr(dave, "ready", False)),
            dave_ver,
        )

        self._bump_idle_timer(interaction.guild.id)

        # Warm patch-notes cache so the first hero question is faster
        async def _warm_patches() -> None:
            try:
                from overwatch_patches import fetch_all_patch_summaries

                await fetch_all_patch_summaries(limit=20, max_months=3)
            except Exception:
                log.debug("Patch cache warm failed", exc_info=True)

        asyncio.create_task(_warm_patches())

        tip = (
            f"Joined **{channel.name}** — say **Dream, …** to start. "
            f"For ~90s after that you can keep talking without repeating Dream "
            f"(follow-ups like *and Tracer?* work). "
            f"Idle 2.5 min with no wake → I leave."
        )
        # One public tip is enough — stay quiet while people talk without Dream
        if isinstance(interaction.channel, discord.TextChannel):
            try:
                await interaction.channel.send(tip, delete_after=90)
            except (discord.Forbidden, discord.HTTPException):
                pass
        await interaction.followup.send(
            "Listening. Use `/disconnect` anytime.",
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
        task = self._idle_timers.pop(guild_id, None)
        current = asyncio.current_task()
        if task and not task.done() and task is not current:
            task.cancel()

    def _bump_idle_timer(self, guild_id: int) -> None:
        """Reset the 2.5‑min no-wake leave clock (join or successful Dream)."""
        self._cancel_idle_timer(guild_id)
        self._idle_timers[guild_id] = asyncio.create_task(
            self._idle_leave_after(guild_id, IDLE_LEAVE_SECONDS)
        )

    async def _idle_leave_after(self, guild_id: int, delay: float) -> None:
        try:
            await asyncio.sleep(delay)
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
                delay,
            )
            await self._announce_status(
                guild_id,
                "Left voice — nobody said **Dream** for 2.5 minutes.",
            )
            await self._leave_voice(guild_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Idle-leave failed in guild %s", guild_id)
        finally:
            self._idle_timers.pop(guild_id, None)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(VoiceAICog(bot))
