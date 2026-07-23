"""Google Gemini chat — /ask and @mention replies."""

from __future__ import annotations

import logging
import time
from typing import Any

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

import config

log = logging.getLogger("dream_team.ai")

ACCENT = discord.Color.from_rgb(46, 230, 166)
BRAND = discord.Color.from_rgb(14, 28, 48)

DISCORD_MSG_LIMIT = 2000
COOLDOWN_SECONDS = 8

SYSTEM_INSTRUCTION = (
    "You are the helpful AI assistant for the Dream Team Discord server. "
    "Be friendly, concise, and useful. Keep answers under ~400 words unless "
    "the user asks for detail. Do not claim you can manage Discord settings, "
    "music, or nicknames — point people to the bot's slash commands for those."
)


class GeminiError(Exception):
    """Raised when Gemini returns an error or empty reply."""


async def generate_reply(prompt: str, *, session: aiohttp.ClientSession) -> str:
    if not config.GEMINI_API_KEY:
        raise GeminiError(
            "Gemini is not configured. Add `GEMINI_API_KEY` to the bot `.env`."
        )

    model = config.GEMINI_MODEL
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent"
    )
    payload: dict[str, Any] = {
        "system_instruction": {"parts": [{"text": SYSTEM_INSTRUCTION}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.8,
            "maxOutputTokens": 1024,
        },
    }
    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": config.GEMINI_API_KEY,
    }

    try:
        async with session.post(
            url, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=45)
        ) as resp:
            data = await resp.json(content_type=None)
            if resp.status >= 400:
                message = _api_error_message(data) or f"HTTP {resp.status}"
                log.warning("Gemini error %s: %s", resp.status, message)
                raise GeminiError(f"Gemini request failed: {message}")
    except aiohttp.ClientError as exc:
        log.warning("Gemini network error: %s", exc)
        raise GeminiError("Could not reach Gemini. Try again in a moment.") from exc

    text = _extract_text(data)
    if not text:
        raise GeminiError("Gemini returned an empty reply. Try rephrasing.")
    return text.strip()


def _api_error_message(data: Any) -> str:
    if not isinstance(data, dict):
        return ""
    err = data.get("error")
    if isinstance(err, dict):
        return str(err.get("message") or err.get("status") or "").strip()
    return ""


def extract_text(data: Any) -> str:
    if not isinstance(data, dict):
        return ""
    candidates = data.get("candidates") or []
    if not candidates:
        return ""
    content = candidates[0].get("content") or {}
    parts = content.get("parts") or []
    chunks: list[str] = []
    for part in parts:
        if isinstance(part, dict) and part.get("text"):
            chunks.append(str(part["text"]))
    return "\n".join(chunks).strip()


def _extract_text(data: Any) -> str:
    return extract_text(data)


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
            raise GeminiError("AI session is not ready yet. Try again.")
        return self._session

    def _check_cooldown(self, user_id: int) -> float | None:
        now = time.monotonic()
        last = self._last_ask.get(user_id, 0.0)
        wait = COOLDOWN_SECONDS - (now - last)
        if wait > 0:
            return wait
        self._last_ask[user_id] = now
        return None

    @app_commands.command(name="ask", description="Ask the Dream Team AI (Google Gemini)")
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
        except GeminiError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return
        except Exception as exc:
            log.exception("Unexpected /ask failure: %s", exc)
            await interaction.followup.send(
                "Something went wrong talking to Gemini.",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title="Dream Team AI",
            description=reply[:4096],
            color=ACCENT,
        )
        embed.set_footer(text=f"Gemini · asked by {interaction.user.display_name}")
        await interaction.followup.send(embed=embed)
        for extra in _chunk_text(reply[4096:], DISCORD_MSG_LIMIT) if len(reply) > 4096 else []:
            await interaction.followup.send(extra)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or not self.bot.user:
            return
        if not message.guild:
            return
        if not config.GEMINI_API_KEY:
            return

        mentioned = self.bot.user in message.mentions
        if not mentioned:
            return

        # Ignore pure mention with no question
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
            except GeminiError as exc:
                await message.reply(str(exc), mention_author=False)
                return
            except Exception as exc:
                log.exception("Unexpected mention-AI failure: %s", exc)
                await message.reply(
                    "Something went wrong talking to Gemini.",
                    mention_author=False,
                )
                return

        chunks = _chunk_text(reply)
        await message.reply(chunks[0], mention_author=False)
        for chunk in chunks[1:]:
            await message.channel.send(chunk)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AICog(bot))
