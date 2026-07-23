"""Voice AI — join VC, wake on \"Dream\", answer with Gemini + TTS."""

from __future__ import annotations

import asyncio
import audioop
import base64
import io
import logging
import re
import tempfile
import time
import wave
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

import config
from ai import GeminiError, SYSTEM_INSTRUCTION, extract_text

log = logging.getLogger("dream_team.voice_ai")

SAMPLE_RATE = 48000
CHANNELS = 2
SAMPLE_WIDTH = 2
SILENCE_SECONDS = 0.85
MIN_UTTERANCE_SECONDS = 0.7
MAX_UTTERANCE_SECONDS = 12.0
RMS_THRESHOLD = 400
WAKE_COOLDOWN_SECONDS = 4.0
NO_WAKE = "NO_WAKE"

WAKE_PROMPT = (
    "You are listening to a Discord voice clip from the Dream Team server.\n"
    "The wake word for the bot is \"Dream\" (also accept close forms like "
    "\"Hey Dream\", \"Dream?\").\n\n"
    "If the speaker is addressing the bot with that wake word, answer their "
    "question helpfully in 1–3 short spoken sentences (plain text, no markdown, "
    "no bullet lists).\n"
    "If they are NOT addressing the bot, reply with exactly: NO_WAKE"
)


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
        self.started_at = 0.0
        self.bytes_total = 0

    def add(self, pcm: bytes, now: float) -> None:
        if not self.chunks:
            self.started_at = now
        self.chunks.append(pcm)
        self.bytes_total += len(pcm)
        self.last_voice_at = now

    def duration(self, now: float | None = None) -> float:
        if not self.chunks:
            return 0.0
        end = now if now is not None else self.last_voice_at
        return max(0.0, end - self.started_at)

    def flush(self) -> bytes:
        data = b"".join(self.chunks)
        self.chunks.clear()
        self.bytes_total = 0
        self.started_at = 0.0
        self.last_voice_at = 0.0
        return data


def _build_sink_class():
    from discord.ext import voice_recv

    class WakeAudioSink(voice_recv.AudioSink):
        def __init__(self, cog: VoiceAICog, guild_id: int) -> None:
            super().__init__()
            self.cog = cog
            self.guild_id = guild_id
            self._buffers: dict[int, UtteranceBuffer] = defaultdict(UtteranceBuffer)

        def wants_opus(self) -> bool:
            return False

        def write(self, user: discord.User | discord.Member | None, data) -> None:
            if user is None or getattr(user, "bot", False):
                return
            if self.cog.is_paused(self.guild_id):
                return
            pcm = getattr(data, "pcm", None)
            if not pcm:
                return
            try:
                rms = audioop.rms(pcm, SAMPLE_WIDTH)
            except Exception:
                return
            if rms < RMS_THRESHOLD:
                return

            now = time.monotonic()
            buf = self._buffers[user.id]
            buf.add(pcm, now)

            max_bytes = int(
                SAMPLE_RATE * CHANNELS * SAMPLE_WIDTH * MAX_UTTERANCE_SECONDS
            )
            if buf.bytes_total > max_bytes:
                # Force readiness on next poll
                buf.last_voice_at = now - SILENCE_SECONDS - 0.01

        def cleanup(self) -> None:
            self._buffers.clear()

        def pop_ready(self, user_id: int, now: float) -> bytes | None:
            buf = self._buffers.get(user_id)
            if buf is None or not buf.chunks:
                return None
            if buf.duration(now) < MIN_UTTERANCE_SECONDS:
                return None
            if now - buf.last_voice_at < SILENCE_SECONDS:
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
                    now - buf.last_voice_at >= SILENCE_SECONDS
                    or buf.duration(now) >= MAX_UTTERANCE_SECONDS
                ):
                    ready.append(uid)
            return ready

    return WakeAudioSink


async def gemini_voice_reply(
    wav_bytes: bytes, *, session: aiohttp.ClientSession
) -> str | None:
    """Return spoken answer text, or None if wake word was not used."""
    if not config.GEMINI_API_KEY:
        raise GeminiError("GEMINI_API_KEY not set.")

    model = config.GEMINI_MODEL
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent"
    )
    b64 = base64.b64encode(wav_bytes).decode("ascii")
    payload: dict[str, Any] = {
        "system_instruction": {"parts": [{"text": SYSTEM_INSTRUCTION}]},
        "contents": [
            {
                "role": "user",
                "parts": [
                    {"text": WAKE_PROMPT},
                    {"inline_data": {"mime_type": "audio/wav", "data": b64}},
                ],
            }
        ],
        "generationConfig": {
            "temperature": 0.6,
            "maxOutputTokens": 512,
        },
    }
    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": config.GEMINI_API_KEY,
    }

    async with session.post(
        url, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=60)
    ) as resp:
        data = await resp.json(content_type=None)
        if resp.status >= 400:
            err = data.get("error", {}) if isinstance(data, dict) else {}
            msg = err.get("message") if isinstance(err, dict) else str(data)
            raise GeminiError(f"Gemini voice failed: {msg}")

    text = (extract_text(data) or "").strip()
    if not text:
        return None
    if text.upper().startswith(NO_WAKE) or text.strip() == NO_WAKE:
        return None
    cleaned = re.sub(r"(?i)^\s*NO_WAKE\s*", "", text).strip()
    return cleaned or None


async def synthesize_speech(text: str, out_path: Path) -> Path:
    import edge_tts

    communicate = edge_tts.Communicate(text, config.TTS_VOICE)
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

    async def cog_load(self) -> None:
        self._session = aiohttp.ClientSession()
        self._poll_task = asyncio.create_task(self._poll_loop())

    async def cog_unload(self) -> None:
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
        for guild_id in list(self._listening):
            await self._stop_listening(guild_id, leave=False)
        if self._session and not self._session.closed:
            await self._session.close()

    def is_paused(self, guild_id: int) -> bool:
        return time.monotonic() < self._paused_until.get(guild_id, 0.0)

    def pause_listening(self, guild_id: int, seconds: float = 6.0) -> None:
        self._paused_until[guild_id] = time.monotonic() + seconds

    def _session_or_raise(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            raise GeminiError("AI session not ready.")
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
        self.pause_listening(guild_id, 2.0)
        try:
            wav = _pcm_to_wav_bytes(pcm)
            if len(wav) > 4_500_000:
                log.info("Voice clip too large in guild %s — skipped", guild_id)
                return

            reply = await gemini_voice_reply(wav, session=self._session_or_raise())
            if not reply:
                return

            guild = self.bot.get_guild(guild_id)
            if guild is None:
                return
            member = guild.get_member(user_id)
            who = member.display_name if member else f"user {user_id}"
            log.info("Voice wake from %s in %s", who, guild.name)

            await self._speak(guild, reply)
            await self._announce_text(guild_id, who, reply)
        except GeminiError as exc:
            log.warning("Voice AI: %s", exc)
        except Exception:
            log.exception("Voice AI utterance failed")
        finally:
            self._busy.discard(guild_id)
            self.pause_listening(guild_id, WAKE_COOLDOWN_SECONDS)

    async def _announce_text(self, guild_id: int, who: str, reply: str) -> None:
        channel_id = self._text_channel.get(guild_id)
        channel = self.bot.get_channel(channel_id) if channel_id else None
        if not isinstance(channel, discord.TextChannel):
            return
        embed = discord.Embed(
            title="Dream (voice)",
            description=reply[:4096],
            color=discord.Color.from_rgb(46, 230, 166),
        )
        embed.set_footer(text=f"Heard {who}")
        try:
            await channel.send(embed=embed)
        except (discord.Forbidden, discord.HTTPException):
            pass

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
        name="listen",
        description="Join your VC and answer when you say Dream …",
    )
    async def listen_cmd(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                "Use this in a server.", ephemeral=True
            )
            return
        if not config.GEMINI_API_KEY:
            await interaction.response.send_message(
                "Set `GEMINI_API_KEY` in `.env` first.", ephemeral=True
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

        listen_fn(sink)
        self._listening[interaction.guild.id] = sink
        if isinstance(interaction.channel, discord.TextChannel):
            self._text_channel[interaction.guild.id] = interaction.channel.id

        music = self.bot.get_cog("MusicCog")
        if music is not None and hasattr(music, "get_player"):
            player = music.get_player(interaction.guild.id)
            if player is not None:
                player.voice = vc

        await interaction.followup.send(
            f"Listening in **{channel.name}**.\n"
            "Say **Dream**, then your question — e.g. "
            "*Dream, was Genji patched?*\n"
            "Use `/unlisten` to stop. Music and listen share the same VC.",
            ephemeral=True,
        )

    @app_commands.command(
        name="unlisten",
        description="Stop wake-word listening (stays in VC if music is playing)",
    )
    async def unlisten_cmd(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                "Use this in a server.", ephemeral=True
            )
            return

        guild_id = interaction.guild.id
        was = guild_id in self._listening
        music = self.bot.get_cog("MusicCog")
        playing = False
        if music is not None and hasattr(music, "get_player"):
            player = music.get_player(guild_id)
            playing = bool(player and player.current)

        await self._stop_listening(guild_id, leave=not playing)

        vc = interaction.guild.voice_client
        if vc and vc.is_connected() and playing:
            try:
                await interaction.guild.change_voice_state(
                    channel=vc.channel, self_deaf=True, self_mute=False
                )
            except Exception:
                pass

        if was:
            await interaction.response.send_message(
                "Stopped listening."
                + (" Still in VC for music." if playing else ""),
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                "I wasn't listening.", ephemeral=True
            )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(VoiceAICog(bot))
