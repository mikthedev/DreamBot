"""Free Llama chat via Groq — /ask, @mention, and Dream voice."""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from typing import Any

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

import config
from ai_ow import (
    extract_hero_query,
    facts_block,
    lookup_hero_patch,
)

log = logging.getLogger("dream_team.ai")

ACCENT = discord.Color.from_rgb(46, 230, 166)
DISCORD_MSG_LIMIT = 2000
COOLDOWN_SECONDS = 8
PENDING_SECONDS = 90.0

SYSTEM_INSTRUCTION = (
    "You are Dream, the casual buddy AI for the Dream Team Discord server.\n"
    "Style: short, natural, spoken — usually 1–3 sentences. No markdown, "
    "no bullet lists, no headers.\n"
    "Languages: understand Russian and English. Always reply in the same "
    "language the user just used.\n"
    "The wake word is always the English word Dream (even in Russian chat).\n"
    "Do not invent Overwatch patch notes. If PATCH FACTS are provided, use "
    "only those facts. If none are provided, say you don't have patch data.\n"
    "For bot features (music, nicknames, panel): point people to slash commands "
    "briefly — don't pretend you can change server settings yourself."
)

GROQ_CHAT_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_TRANSCRIBE_URL = "https://api.groq.com/openai/v1/audio/transcriptions"

_WAKE_RE = re.compile(
    r"(?:^|[\s,.\!?])(?:hey\s+|эй\s+|эй,\s*)?dream(?:\s+team)?[\s,.\!?:\-]*",
    re.IGNORECASE,
)

_YES_RE = re.compile(
    r"(?i)^\s*(?:dream[\s,.\!?:\-]*)?(?:yes|yeah|yep|sure|ok|okay|"
    r"да|ага|угу|конечно|давай|расскажи|tell me|go ahead)\b",
)


class AIError(Exception):
    """Raised when the free AI provider returns an error or empty reply."""


GeminiError = AIError


@dataclass
class PendingOffer:
    hero: str
    facts: str
    expires_at: float


# (guild_id, user_id) -> pending "want the details?" offer
_pending_offers: dict[tuple[int, int], PendingOffer] = {}


def ai_configured() -> bool:
    return bool(config.GROQ_API_KEY)


def looks_cyrillic(text: str) -> bool:
    return bool(re.search(r"[а-яА-ЯёЁ]", text or ""))


def is_affirmative(text: str) -> bool:
    return bool(_YES_RE.match((text or "").strip()))


def set_pending_offer(
    guild_id: int, user_id: int, *, hero: str, facts: str
) -> None:
    _pending_offers[(guild_id, user_id)] = PendingOffer(
        hero=hero,
        facts=facts,
        expires_at=time.monotonic() + PENDING_SECONDS,
    )


def pop_pending_offer(guild_id: int, user_id: int) -> PendingOffer | None:
    key = (guild_id, user_id)
    offer = _pending_offers.get(key)
    if offer is None:
        return None
    if time.monotonic() > offer.expires_at:
        _pending_offers.pop(key, None)
        return None
    return _pending_offers.pop(key, None)


def peek_pending_offer(guild_id: int, user_id: int) -> PendingOffer | None:
    key = (guild_id, user_id)
    offer = _pending_offers.get(key)
    if offer is None:
        return None
    if time.monotonic() > offer.expires_at:
        _pending_offers.pop(key, None)
        return None
    return offer


async def generate_reply(
    prompt: str,
    *,
    session: aiohttp.ClientSession,
    system: str | None = None,
    max_tokens: int = 220,
    temperature: float = 0.7,
) -> str:
    if not config.GROQ_API_KEY:
        raise AIError(
            "AI is not configured. Add a free `GROQ_API_KEY` to `.env` "
            "(https://console.groq.com/keys — no billing required)."
        )

    payload: dict[str, Any] = {
        "model": config.GROQ_MODEL,
        "messages": [
            {"role": "system", "content": system or SYSTEM_INSTRUCTION},
            {"role": "user", "content": prompt},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
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
    return _strip_markdown(text.strip())


async def transcribe_audio(
    wav_bytes: bytes,
    *,
    session: aiohttp.ClientSession,
    filename: str = "clip.wav",
) -> str:
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


def extract_wake_question(transcript: str) -> str | None:
    """If transcript addresses Dream, return the question after the wake word."""
    text = (transcript or "").strip()
    if not text:
        return None
    match = _WAKE_RE.search(text)
    if not match:
        if not re.match(r"(?i)^\s*(?:hey\s+|эй\s+)?dream\b", text):
            return None
        match = re.match(
            r"(?i)^\s*(?:hey\s+|эй\s+)?dream(?:\s+team)?[\s,.\!?:\-]*", text
        )
        if not match:
            return None
    question = text[match.end() :].strip(" \t\n\r,.-")
    return question or None


def _strip_markdown(text: str) -> str:
    text = re.sub(r"[*`_#]", "", text)
    text = re.sub(r"\n{2,}", " ", text)
    text = re.sub(r"\s{2,}", " ", text)
    return text.strip()


def _patch_question(text: str) -> bool:
    low = (text or "").lower()
    keys = (
        "nerf",
        "buff",
        "patch",
        "нерф",
        "бафф",
        "патч",
        "changed",
        "update",
        "updated",
        "изменени",
        "понерф",
        "побафф",
    )
    return any(k in low for k in keys)


async def handle_user_turn(
    question: str,
    *,
    session: aiohttp.ClientSession,
    bot,
    guild_id: int,
    user_id: int,
    voice: bool = True,
) -> str:
    """Answer a user turn (after wake word, or a pending yes)."""
    q = (question or "").strip()
    if not q:
        return "Yeah? What do you need?" if not looks_cyrillic(q) else "Ага? Чё надо?"

    # Follow-up: user said yes to hearing last patch details
    if is_affirmative(q) and peek_pending_offer(guild_id, user_id):
        offer = pop_pending_offer(guild_id, user_id)
        assert offer is not None
        prompt = (
            "The user said yes — tell them the last patch highlights for this hero.\n"
            "Use ONLY these PATCH FACTS. Pick the 1–2 most important changes. "
            "Keep it casual and short (2 sentences max). Mention roughly when "
            "(from PATCH_DATE). No lists.\n\n"
            f"PATCH FACTS:\n{offer.facts}\n\n"
            f"User said: {q}"
        )
        return await generate_reply(prompt, session=session, max_tokens=180)

    # Overwatch balance question
    hero = extract_hero_query(q) if _patch_question(q) else None
    if hero:
        hit, latest_date = await lookup_hero_patch(bot, guild_id, hero)
        if hit is None:
            prompt = (
                "User asked about an Overwatch hero patch, but we have no saved "
                f"notes for '{hero}'. Say casually you don't see them in the "
                "recent notes we track. One short sentence.\n\n"
                f"User: {q}"
            )
            return await generate_reply(prompt, session=session, max_tokens=120)

        facts = facts_block(hit)
        set_pending_offer(guild_id, user_id, hero=hit.hero_name, facts=facts)

        if hit.in_latest:
            prompt = (
                "User asked if this hero was nerfed/buffed. They ARE in the "
                "latest patch. Answer in 1–2 casual sentences: say they got a "
                "buff, nerf, or mix (from CHANGE_KIND), based on the latest "
                "notes (LATEST_PATCH_DATE). Then ask if they want the highlights. "
                "Do NOT list the changes yet.\n\n"
                f"PATCH FACTS:\n{facts}\n\nUser: {q}"
            )
        else:
            prompt = (
                "User asked if this hero was nerfed/buffed. They are NOT in the "
                "newest patch (LATEST_PATCH_DATE). Say casually that based on the "
                "latest updates they didn't get changes this time. Mention the "
                "last time they were patched (PATCH_DATE) only briefly, then ask "
                "if the user wants to hear what changed then. Do NOT list changes "
                "yet.\n\n"
                f"PATCH FACTS:\n{facts}\n\nUser: {q}"
            )
        return await generate_reply(prompt, session=session, max_tokens=160)

    # General chat
    style = (
        "Answer in 1–3 short spoken sentences, plain text, no markdown. "
        "Match the user's language (Russian or English)."
        if voice
        else "Keep it concise and casual. Match the user's language."
    )
    return await generate_reply(f"{style}\n\nUser: {q}", session=session)


async def voice_reply_from_wav(
    wav_bytes: bytes,
    *,
    session: aiohttp.ClientSession,
    bot,
    guild_id: int,
    user_id: int,
) -> str | None:
    """
    Transcribe → wake / pending-yes → answer.
    Returns None if this utterance should be ignored (no Dream, no pending yes).
    """
    transcript = await transcribe_audio(wav_bytes, session=session)
    log.info("Voice transcript: %r", transcript[:200])

    pending = peek_pending_offer(guild_id, user_id)
    wake_q = extract_wake_question(transcript)

    if wake_q is not None:
        question = wake_q
    elif pending and is_affirmative(transcript):
        # Allow plain "yes" / "да" without Dream after we offered details
        question = transcript.strip()
    else:
        return None

    return await handle_user_turn(
        question,
        session=session,
        bot=bot,
        guild_id=guild_id,
        user_id=user_id,
        voice=True,
    )


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
        description="Ask Dream (free Llama via Groq)",
    )
    @app_commands.describe(prompt="Your question or message")
    async def ask(self, interaction: discord.Interaction, prompt: str) -> None:
        prompt = prompt.strip()
        if not prompt:
            await interaction.response.send_message(
                "Ask something — e.g. `/ask Was Genji nerfed?`",
                ephemeral=True,
            )
            return
        if len(prompt) > 1500:
            await interaction.response.send_message(
                "Keep your question under 1500 characters.",
                ephemeral=True,
            )
            return
        if interaction.guild is None:
            await interaction.response.send_message(
                "Use this inside the server.", ephemeral=True
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
            reply = await handle_user_turn(
                prompt,
                session=self._session_or_raise(),
                bot=self.bot,
                guild_id=interaction.guild.id,
                user_id=interaction.user.id,
                voice=False,
            )
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
            title="Dream",
            description=reply[:4096],
            color=ACCENT,
        )
        embed.set_footer(text=f"Llama · asked by {interaction.user.display_name}")
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
                reply = await handle_user_turn(
                    prompt,
                    session=self._session_or_raise(),
                    bot=self.bot,
                    guild_id=message.guild.id,
                    user_id=message.author.id,
                    voice=False,
                )
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
