"""Free Llama chat via Groq — /ask and @mention replies (no Google billing)."""

from __future__ import annotations

import logging
import re
import time
from typing import Any

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

import config

log = logging.getLogger("dream_team.ai")

ACCENT = discord.Color.from_rgb(46, 230, 166)
DISCORD_MSG_LIMIT = 2000
COOLDOWN_SECONDS = 8

SYSTEM_INSTRUCTION = (
    "You are the helpful AI assistant for the Dream Team Discord server. "
    "Be friendly, concise, and useful. Keep answers under ~400 words unless "
    "the user asks for detail. Do not claim you can manage Discord settings, "
    "music, or nicknames — point people to the bot's slash commands for those."
)

GROQ_CHAT_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_TRANSCRIBE_URL = "https://api.groq.com/openai/v1/audio/transcriptions"


class AIError(Exception):
    """Raised when the free AI provider returns an error or empty reply."""


# Back-compat alias used by voice_ai
GeminiError = AIError


def ai_configured() -> bool:
    return bool(config.GROQ_API_KEY)


async def generate_reply(prompt: str, *, session: aiohttp.ClientSession) -> str:
    if not config.GROQ_API_KEY:
        raise AIError(
            "AI is not configured. Add a free `GROQ_API_KEY` to `.env` "
            "(https://console.groq.com/keys — no billing required)."
        )

    payload: dict[str, Any] = {
        "model": config.GROQ_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_INSTRUCTION},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.8,
        "max_tokens": 1024,
    }
    headers = {
        "Authorization": f"Bearer {config.GROQ_API_KEY}",
        "Content-Type": "application/json",
    }

    try:
        async with session.post(
            GROQ_CHAT_URL,
            json=payload,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=45),
        ) as resp:
            data = await resp.json(content_type=None)
            if resp.status >= 400:
                message = _api_error_message(data) or f"HTTP {resp.status}"
                log.warning("Groq error %s: %s", resp.status, message)
                if resp.status == 429:
                    raise AIError(
                        "Free Groq rate limit hit — wait a bit and try again."
                    )
                raise AIError(f"AI request failed: {message}")
    except aiohttp.ClientError as exc:
        log.warning("Groq network error: %s", exc)
        raise AIError("Could not reach Groq. Try again in a moment.") from exc

    text = _chat_text(data)
    if not text:
        raise AIError("AI returned an empty reply. Try rephrasing.")
    return text.strip()


async def transcribe_audio(
    wav_bytes: bytes,
    *,
    session: aiohttp.ClientSession,
    filename: str = "clip.wav",
) -> str:
    """Speech-to-text via Groq Whisper (free tier)."""
    if not config.GROQ_API_KEY:
        raise AIError("GROQ_API_KEY not set.")

    form = aiohttp.FormData()
    form.add_field(
        "file",
        wav_bytes,
        filename=filename,
        content_type="audio/wav",
    )
    form.add_field("model", config.GROQ_WHISPER_MODEL)
    form.add_field("response_format", "json")
    headers = {"Authorization": f"Bearer {config.GROQ_API_KEY}"}

    try:
        async with session.post(
            GROQ_TRANSCRIBE_URL,
            data=form,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=60),
        ) as resp:
            data = await resp.json(content_type=None)
            if resp.status >= 400:
                message = _api_error_message(data) or f"HTTP {resp.status}"
                log.warning("Groq Whisper error %s: %s", resp.status, message)
                raise AIError(f"Speech recognition failed: {message}")
    except aiohttp.ClientError as exc:
        raise AIError("Could not reach Groq Whisper.") from exc

    text = ""
    if isinstance(data, dict):
        text = str(data.get("text") or "").strip()
    return text


_WAKE_RE = re.compile(
    r"(?:^|[\s,.\!?])(?:hey\s+)?dream(?:\s+team)?[\s,.\!?:\-]*",
    re.IGNORECASE,
)


def extract_wake_question(transcript: str) -> str | None:
    """If transcript addresses Dream, return the question after the wake word."""
    text = (transcript or "").strip()
    if not text:
        return None
    match = _WAKE_RE.search(text)
    if not match:
        # Also accept starting with Dream
        if not re.match(r"(?i)^\s*(?:hey\s+)?dream\b", text):
            return None
        match = re.match(r"(?i)^\s*(?:hey\s+)?dream(?:\s+team)?[\s,.\!?:\-]*", text)
        if not match:
            return None
    question = text[match.end() :].strip(" \t\n\r,.-")
    return question or None


async def voice_reply_from_wav(
    wav_bytes: bytes, *, session: aiohttp.ClientSession
) -> str | None:
    """Transcribe → wake check → Llama answer. None if no wake word."""
    transcript = await transcribe_audio(wav_bytes, session=session)
    log.info("Voice transcript: %r", transcript[:200])
    question = extract_wake_question(transcript)
    if question is None:
        return None
    if not question:
        return "Yeah? What do you need?"
    spoken = (
        "Answer in 1–3 short spoken sentences, plain text, no markdown: "
        + question
    )
    return await generate_reply(spoken, session=session)


def _api_error_message(data: Any) -> str:
    if not isinstance(data, dict):
        return ""
    err = data.get("error")
    if isinstance(err, dict):
        return str(err.get("message") or err.get("code") or "").strip()
    if isinstance(err, str):
        return err.strip()
    return ""


def _chat_text(data: Any) -> str:
    if not isinstance(data, dict):
        return ""
    choices = data.get("choices") or []
    if not choices:
        return ""
    msg = choices[0].get("message") or {}
    return str(msg.get("content") or "").strip()


def _chunk_text(text: str, limit: int = DISCORD_MSG_LIMIT) -> list[str]:
    text = text.strip()
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    while text:
        if len(text) <= limit:
            chunks.append(text)
            break
        cut = text.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = text.rfind(" ", 0, limit)
        if cut < limit // 2:
            cut = limit
        chunks.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    return chunks


class AICog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._session: aiohttp.ClientSession | None = None
        self._last_ask: dict[int, float] = {}

    async def cog_load(self) -> None:
        self._session = aiohttp.ClientSession()

    async def cog_unload(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    def _session_or_raise(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            raise AIError("AI session is not ready yet. Try again.")
        return self._session

    def _check_cooldown(self, user_id: int) -> float | None:
        now = time.monotonic()
        last = self._last_ask.get(user_id, 0.0)
        wait = COOLDOWN_SECONDS - (now - last)
        if wait > 0:
            return wait
        self._last_ask[user_id] = now
        return None

    @app_commands.command(
        name="ask",
        description="Ask the Dream Team AI (free Llama via Groq)",
    )
    @app_commands.describe(prompt="Your question or message")
    async def ask(self, interaction: discord.Interaction, prompt: str) -> None:
        prompt = prompt.strip()
        if not prompt:
            await interaction.response.send_message(
                "Ask something — e.g. `/ask What is Overwatch 2?`",
                ephemeral=True,
            )
            return
        if len(prompt) > 1500:
            await interaction.response.send_message(
                "Keep your question under 1500 characters.",
                ephemeral=True,
            )
            return

        wait = self._check_cooldown(interaction.user.id)
        if wait is not None:
            await interaction.response.send_message(
                f"Easy — wait **{wait:.0f}s** before asking again.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(thinking=True)
        try:
            reply = await generate_reply(prompt, session=self._session_or_raise())
        except AIError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return
        except Exception as exc:
            log.exception("Unexpected /ask failure: %s", exc)
            await interaction.followup.send(
                "Something went wrong talking to the AI.",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title="Dream Team AI",
            description=reply[:4096],
            color=ACCENT,
        )
        embed.set_footer(
            text=f"Llama · Groq · asked by {interaction.user.display_name}"
        )
        await interaction.followup.send(embed=embed)
        for extra in (
            _chunk_text(reply[4096:], DISCORD_MSG_LIMIT) if len(reply) > 4096 else []
        ):
            await interaction.followup.send(extra)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or not self.bot.user:
            return
        if not message.guild:
            return
        if not ai_configured():
            return

        mentioned = self.bot.user in message.mentions
        if not mentioned:
            return

        prompt = message.content
        for mention in message.mentions:
            prompt = prompt.replace(f"<@{mention.id}>", "")
            prompt = prompt.replace(f"<@!{mention.id}>", "")
        prompt = prompt.strip()
        if not prompt:
            return

        wait = self._check_cooldown(message.author.id)
        if wait is not None:
            await message.reply(
                f"Easy — wait **{wait:.0f}s** before asking again.",
                mention_author=False,
            )
            return

        async with message.channel.typing():
            try:
                reply = await generate_reply(prompt, session=self._session_or_raise())
            except AIError as exc:
                await message.reply(str(exc), mention_author=False)
                return
            except Exception as exc:
                log.exception("Unexpected mention-AI failure: %s", exc)
                await message.reply(
                    "Something went wrong talking to the AI.",
                    mention_author=False,
                )
                return

        chunks = _chunk_text(reply)
        await message.reply(chunks[0], mention_author=False)
        for chunk in chunks[1:]:
            await message.channel.send(chunk)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AICog(bot))
