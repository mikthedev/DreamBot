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
    fix_ow_asr,
    lookup_hero_patch,
    patch_details_from_facts,
    patch_teaser,
)

log = logging.getLogger("dream_team.ai")

ACCENT = discord.Color.from_rgb(46, 230, 166)
DISCORD_MSG_LIMIT = 2000
COOLDOWN_SECONDS = 8
PENDING_SECONDS = 90.0
# After "Dream, …", keep listening to the same user without wake word
CONVO_SECONDS = 90.0
HISTORY_TURNS = 6

SYSTEM_INSTRUCTION = (
    "You are Dream, a sharp but chill teammate on the Dream Team Discord.\n"
    "Talk naturally — short, warm, conversational. Usually 1–2 sentences in voice.\n"
    "No markdown, no bullet lists, no headers, no robotic phrasing.\n"
    "You understand English, Russian, and Ukrainian.\n"
    "CRITICAL LANGUAGE RULE: Reply ONLY in the language marked REPLY LANGUAGE. "
    "If REPLY LANGUAGE is English, every sentence must be English — never Russian "
    "or Ukrainian. Same for Russian-only and Ukrainian-only.\n"
    "Be lightly witty only when it fits — never force jokes. "
    "Accuracy first: if unsure, say you don't know. "
    "Never invent facts, numbers, dates, patch details, quotes, or biographies. "
    "For real people (actors, celebrities, streamers): give the correct real-world "
    "role (e.g. Millie Bobby Brown is a live-action actress). Do not invent animated "
    "series or fake credits.\n"
    "Use recent conversation context for pronouns and short follow-ups "
    "(LAST HERO / history).\n"
    "For Overwatch hero balance: ONLY use PATCH FACTS when provided. "
    "Refer to patches by calendar DATE only. Never invent nerfs/buffs.\n"
    "For Discord bot features: briefly point to slash commands."
)

GROQ_CHAT_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_TRANSCRIBE_URL = "https://api.groq.com/openai/v1/audio/transcriptions"

_WAKE_RE = re.compile(
    r"(?:^|[\s,.\!?])(?:hey\s+|эй\s+|эй,\s*|ok\s+)?"
    r"(?:dream(?:\s+team)?|дрим(?:\s+тим)?|дрім(?:\s+тім)?|"
    r"dre+m+|dr[ei]m+|drum|grim|cream|drin|дримм?|"
    r"дрень|дрейм|джейм|джеймс|дреам|дримы|дримс|грим|"
    r"dmv|d\.m\.v)"
    r"[\s,.\!?:\-]*",
    re.IGNORECASE,
)

# Exact / near-exact first tokens Whisper invents for "Dream" (RU/UA accents)
_WAKE_FIRST_TOKENS = frozenset(
    {
        "dream",
        "dreams",
        "dreamteam",
        "дрим",
        "дрім",
        "дрима",
        "дримм",
        "дримы",
        "дримс",
        "дрейм",
        "дрень",
        "дреам",
        "дримт",
        "джейм",
        "джеймс",
        "джейми",
        "дримтим",
        "дрімтим",
        "грим",
        "грем",
        "дрин",
        "дримм",
        "dreem",
        "drem",
        "drim",
        "drin",
        "drum",
        "grim",
        "cream",
        "drean",
        "drain",
        "dmv",
        "дмв",
    }
)

_WAKE_FUZZY_TARGETS = (
    "dream",
    "dreams",
    "дрим",
    "дрім",
    "дримм",
    "дрейм",
    "дрень",
    "джейм",
)


def _levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(
                min(
                    prev[j] + 1,
                    cur[j - 1] + 1,
                    prev[j - 1] + (0 if ca == cb else 1),
                )
            )
        prev = cur
    return prev[-1]


def _token_looks_like_dream(token: str) -> bool:
    t = re.sub(r"[^\wа-яё]", "", (token or "").lower(), flags=re.UNICODE)
    if not t:
        return False
    # D.M.V. / D M V style
    compact = re.sub(r"[\s.]+", "", (token or "").lower())
    if compact in {"dmv", "d.m.v", "дмв"}:
        return True
    if t in _WAKE_FIRST_TOKENS:
        return True
    for target in _WAKE_FUZZY_TARGETS:
        if abs(len(t) - len(target)) > 2:
            continue
        # Allow 2 edits for short RU mangling (дрень≈дрим)
        if _levenshtein(t, target) <= 2:
            return True
    return False


_YES_RE = re.compile(
    r"(?i)^\s*(?:(?:dream|дрим|дрім|дрень|джейм|дрейм)[\s,.\!?:\-]*)?"
    r"(?:yes|yeah|yep|sure|ok|okay|"
    r"да|ага|угу|конечно|давай|расскажи|tell me|go ahead|"
    r"так|авжеж|розкажи|добре)\b",
)


class AIError(Exception):
    """Raised when the free AI provider returns an error or empty reply."""


GeminiError = AIError


@dataclass
class PendingOffer:
    hero: str
    facts: str
    expires_at: float


@dataclass
class ChatTurn:
    role: str  # user | assistant
    content: str


@dataclass
class UserSession:
    expires_at: float
    last_hero: str | None = None
    last_lang: str | None = None  # en | ru | uk
    history: list[ChatTurn] | None = None

    def __post_init__(self) -> None:
        if self.history is None:
            self.history = []


# (guild_id, user_id) -> pending "want the details?" offer
_pending_offers: dict[tuple[int, int], PendingOffer] = {}
# (guild_id, user_id) -> active voice/text conversation
_sessions: dict[tuple[int, int], UserSession] = {}


def ai_configured() -> bool:
    return bool(config.GROQ_API_KEY)


def looks_cyrillic(text: str) -> bool:
    return bool(re.search(r"[а-яА-ЯёЁіІїЇєЄґҐ]", text or ""))


def detect_user_language(text: str, *, fallback: str | None = None) -> str:
    """
    Detect reply language from the user's message.
    Returns 'en', 'ru', or 'uk'.
    Latin-heavy text is ALWAYS English (never fall back to RU/UK).
    """
    t = text or ""
    latin = len(re.findall(r"[A-Za-z]", t))
    cyr = len(re.findall(r"[а-яА-ЯёЁіІїЇєЄґҐ]", t))
    # Clear English: mostly Latin letters — lock EN even mid-RU session
    if latin >= 3 and latin >= cyr * 2:
        return "en"
    if re.search(r"[іїєґІЇЄҐ]", t):
        return "uk"
    if re.search(
        r"(?i)\b(що|як|де|мені|тобі|дякую|будь\s*ласка|привіт|капібар|"
        r"розкажи|авжеж|добре|живуть|вийди|дякую)\b",
        t,
    ):
        return "uk"
    if re.search(r"[ыэъёЫЭЪЁ]", t):
        return "ru"
    if re.search(
        r"(?i)\b(что|как|где|мне|тебе|спасибо|пожалуйста|привет|"
        r"расскажи|конечно|живут|выйди|уходи)\b",
        t,
    ):
        return "ru"
    if cyr >= 3:
        if fallback in {"ru", "uk"}:
            return fallback
        return "ru"
    if latin >= 1:
        return "en"
    return fallback if fallback in {"en", "ru", "uk"} else "en"


def language_instruction(lang: str) -> str:
    if lang == "uk":
        return (
            "REPLY LANGUAGE: Ukrainian only. "
            "Пиши українською. Do not use English or Russian."
        )
    if lang == "ru":
        return (
            "REPLY LANGUAGE: Russian only. "
            "Отвечай только по-русски. Do not use English or Ukrainian."
        )
    return (
        "REPLY LANGUAGE: English only. "
        "Answer only in English. Do not use Russian or Ukrainian."
    )


def empty_prompt_reply(lang: str) -> str:
    if lang == "uk":
        return "Ага? Що треба?"
    if lang == "ru":
        return "Ага? Чё надо?"
    return "Yeah? What do you need?"


def _cyrillic_ratio(text: str) -> float:
    letters = re.findall(r"[A-Za-zа-яА-ЯёЁіІїЇєЄґҐ]", text or "")
    if not letters:
        return 0.0
    cyr = sum(1 for ch in letters if re.match(r"[а-яА-ЯёЁіІїЇєЄґҐ]", ch))
    return cyr / len(letters)


def reply_matches_language(reply: str, lang: str) -> bool:
    if not (reply or "").strip():
        return True
    ratio = _cyrillic_ratio(reply)
    if lang == "en":
        return ratio < 0.25
    return ratio > 0.45


def leave_goodbye(lang: str, *, thanks: bool) -> str:
    if thanks:
        if lang == "uk":
            return "Немає проблем, бувай!"
        if lang == "ru":
            return "Без проблем, пока-пока!"
        return "No problem, bye bye!"
    if lang == "uk":
        return "Ок, виходжу."
    if lang == "ru":
        return "Ок, выхожу."
    return "Alright, leaving."


_LEAVE_CMD_RE = re.compile(
    r"(?i)^\s*(?:please\s+)?(?:"
    r"leave|disconnect|go\s+away|get\s+out|"
    r"выйди|выйти|уходи|отключись|покинь|"
    r"вийди|піди\s+геть|виходь|залиши"
    r")(?:\s+please)?\s*[.!]?\s*$"
)

_THANKS_LEAVE_RE = re.compile(
    r"(?i)^\s*(?:(?:hey\s+|эй\s+)?)?(?:"
    r"thanks?(?:\s+you)?(?:\s+so\s+much)?\s+(?:dream|дрим|дрім)|"
    r"thank\s+(?:you\s+)?(?:dream|дрим|дрім)|ty\s+(?:dream|дрим|дрім)|"
    r"спасибо(?:\s+тебе)?\s+(?:дрим|dream|дрім)|"
    r"благодарю\s+(?:дрим|dream|дрім)|"
    r"дякую(?:\s+тобі)?\s+(?:дрім|dream|дрим)"
    r")\s*[.!]?\s*$"
)

# After wake word stripped: "thanks" / "спасибо" alone still counts
_THANKS_AFTER_WAKE_RE = re.compile(
    r"(?i)^\s*(?:"
    r"thanks?(?:\s+you)?(?:\s+so\s+much)?|"
    r"thank\s+you|ty|"
    r"спасибо(?:\s+тебе)?|благодарю|дякую(?:\s+тобі)?"
    r")\s*[.!]?\s*$"
)


def is_leave_command(text: str) -> bool:
    return bool(_LEAVE_CMD_RE.match((text or "").strip()))


def is_thanks_leave_command(text: str, *, after_wake: bool = False) -> bool:
    t = (text or "").strip()
    if after_wake and _THANKS_AFTER_WAKE_RE.match(t):
        return True
    return bool(_THANKS_LEAVE_RE.match(t))


@dataclass
class VoiceTurnResult:
    """Spoken reply and/or leave the voice channel."""

    reply: str | None = None
    leave: bool = False


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


def touch_session(
    guild_id: int,
    user_id: int,
    *,
    last_hero: str | None = None,
    last_lang: str | None = None,
) -> UserSession:
    key = (guild_id, user_id)
    sess = _sessions.get(key)
    now = time.monotonic()
    if sess is None or now > sess.expires_at:
        sess = UserSession(expires_at=now + CONVO_SECONDS)
        _sessions[key] = sess
    else:
        sess.expires_at = now + CONVO_SECONDS
    if last_hero:
        sess.last_hero = last_hero
    if last_lang in {"en", "ru", "uk"}:
        sess.last_lang = last_lang
    return sess


def peek_session(guild_id: int, user_id: int) -> UserSession | None:
    key = (guild_id, user_id)
    sess = _sessions.get(key)
    if sess is None:
        return None
    if time.monotonic() > sess.expires_at:
        _sessions.pop(key, None)
        return None
    return sess


def remember_turn(
    guild_id: int,
    user_id: int,
    *,
    user: str,
    assistant: str,
    hero: str | None = None,
    lang: str | None = None,
) -> None:
    sess = touch_session(guild_id, user_id, last_hero=hero, last_lang=lang)
    assert sess.history is not None
    sess.history.append(ChatTurn("user", user[:500]))
    sess.history.append(ChatTurn("assistant", assistant[:500]))
    if len(sess.history) > HISTORY_TURNS * 2:
        sess.history = sess.history[-(HISTORY_TURNS * 2) :]


_PRONOUN_RE = re.compile(
    r"(?i)\b(?:he|she|him|her|they|them|his|hers|their|"
    r"он|она|его|её|ее|ему|ей|них|их|него|неё)\b"
)


def resolve_question(text: str, sess: UserSession | None) -> str:
    """Expand short/pronoun follow-ups using the last hero in session."""
    q = (text or "").strip()
    if not sess or not sess.last_hero:
        return q
    hero = sess.last_hero
    if extract_hero_query(q):
        return q
    if _PRONOUN_RE.search(q):
        return f"{q} (referring to {hero})"
    # Bare follow-ups: "and nerfs?", "what about that?", "а трейсер?" handled
    # by hero extract when named; otherwise keep last hero in mind.
    if len(q.split()) <= 8 and (
        _patch_question(q)
        or re.search(
            r"(?i)\b(?:and|also|what about|how about|а|і|и|ещё|еще|про|same)\b",
            q,
        )
    ):
        return f"{q} (about {hero})"
    return q


async def generate_reply(
    prompt: str,
    *,
    session: aiohttp.ClientSession,
    system: str | None = None,
    max_tokens: int = 160,
    temperature: float = 0.45,
    history: list[ChatTurn] | None = None,
    lang: str | None = None,
) -> str:
    if not config.GROQ_API_KEY:
        raise AIError(
            "AI is not configured. Add a free `GROQ_API_KEY` to `.env` "
            "(https://console.groq.com/keys — no billing required)."
        )

    messages: list[dict[str, str]] = [
        {"role": "system", "content": system or SYSTEM_INSTRUCTION},
    ]
    if history:
        for turn in history[-HISTORY_TURNS * 2 :]:
            messages.append({"role": turn.role, "content": turn.content})
    messages.append({"role": "user", "content": prompt})

    payload: dict[str, Any] = {
        "model": config.GROQ_MODEL,
        "messages": messages,
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
            timeout=aiohttp.ClientTimeout(total=25),
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
    reply = _strip_markdown(text.strip())

    # One retry if the model ignored REPLY LANGUAGE (common EN→UA bleed)
    if lang and not reply_matches_language(reply, lang):
        log.info("Language mismatch (want=%s) — rewriting", lang)
        fix_prompt = (
            f"{language_instruction(lang)}\n"
            "Rewrite the following answer in the required language only. "
            "Keep the same meaning. Do not add new facts.\n\n"
            f"Answer to rewrite:\n{reply}"
        )
        payload2 = {
            "model": config.GROQ_MODEL,
            "messages": [
                {"role": "system", "content": SYSTEM_INSTRUCTION},
                {"role": "user", "content": fix_prompt},
            ],
            "temperature": 0.2,
            "max_tokens": max_tokens,
        }
        try:
            async with session.post(
                GROQ_CHAT_URL,
                json=payload2,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=20),
            ) as resp2:
                data2 = await resp2.json(content_type=None)
                if resp2.status < 400:
                    fixed = _strip_markdown(_chat_text(data2).strip())
                    if fixed and reply_matches_language(fixed, lang):
                        return fixed
        except Exception:
            log.debug("Language rewrite failed", exc_info=True)
    return reply


WHISPER_PROMPT = "Dream."


async def _whisper_once(
    wav_bytes: bytes,
    *,
    session: aiohttp.ClientSession,
    filename: str,
    language: str | None,
) -> tuple[str, float]:
    """
    Returns (text, no_speech_prob).
    no_speech_prob is 0..1 from verbose_json segments (1 = likely silence).
    """
    form = aiohttp.FormData()
    form.add_field(
        "file",
        wav_bytes,
        filename=filename,
        content_type="audio/wav",
    )
    form.add_field("model", config.GROQ_WHISPER_MODEL)
    form.add_field("response_format", "verbose_json")
    form.add_field("temperature", "0")
    form.add_field("prompt", WHISPER_PROMPT)
    if language:
        form.add_field("language", language)
    headers = {"Authorization": f"Bearer {config.GROQ_API_KEY}"}

    async with session.post(
        GROQ_TRANSCRIBE_URL,
        data=form,
        headers=headers,
        timeout=aiohttp.ClientTimeout(total=45),
    ) as resp:
        data = await resp.json(content_type=None)
        if resp.status >= 400:
            message = _api_error_message(data) or f"HTTP {resp.status}"
            log.warning("Groq Whisper error %s: %s", resp.status, message)
            raise AIError(f"Speech recognition failed: {message}")

    text_out = ""
    no_speech = 0.0
    if isinstance(data, dict):
        text_out = str(data.get("text") or "").strip()
        segs = data.get("segments") or []
        if isinstance(segs, list) and segs:
            probs = [
                float(s.get("no_speech_prob") or 0.0)
                for s in segs
                if isinstance(s, dict)
            ]
            if probs:
                no_speech = sum(probs) / len(probs)
    return _fix_common_asr(fix_ow_asr(text_out)), no_speech


def _fix_common_asr(text: str) -> str:
    """Light cleanup for frequent Whisper mistakes (not just OW heroes)."""
    out = text or ""
    fixes = (
        (r"(?i)\b(?:эпибары|бибары|капибаррский)\b", "капибары"),
        (r"(?i)\bкапибар+ы?\b", "капибары"),
        (r"(?i)\bкапібар+и?\b", "капібари"),
        (r"(?i)\bcapybaras?\b", "capybaras"),
    )
    for pat, repl in fixes:
        out = re.sub(pat, repl, out)
    return out


def _looks_like_hallucination(text: str) -> bool:
    """Catch Whisper inventing words from silence / prompt bleed."""
    t = (text or "").strip()
    if not t:
        return True
    letters = re.sub(r"[^\wа-яёіїєґА-ЯЁІЇЄҐ]", "", t, flags=re.UNICODE)
    if len(letters) < 3:
        return True
    low = t.lower()
    if re.fullmatch(
        r"(?i)[\s.]*"
        r"(thanks?\s*for\s*watching|subscribe|thank\s*you|"
        r"amara\.org|www\.|http|music|\[?\s*music\s*\]?|"
        r"you|the end|\.\.\.)[\s.]*",
        t,
    ):
        return True
    if "капибаррский day" in low:
        return True
    return False


async def transcribe_audio(
    wav_bytes: bytes,
    *,
    session: aiohttp.ClientSession,
    filename: str = "clip.wav",
) -> str:
    """
    Transcribe with Whisper. Drop high no-speech / hallucinated clips.
    Retry uk/ru only when first pass has real speech but missed Dream.
    """
    if not config.GROQ_API_KEY:
        raise AIError("GROQ_API_KEY not set.")

    forced = config.GROQ_WHISPER_LANGUAGE or None
    try:
        text, no_speech = await _whisper_once(
            wav_bytes, session=session, filename=filename, language=forced
        )
    except aiohttp.ClientError as exc:
        raise AIError("Could not reach Groq Whisper.") from exc

    if no_speech >= 0.55 or _looks_like_hallucination(text):
        log.info(
            "Ignoring likely non-speech (no_speech=%.2f text=%r)",
            no_speech,
            text[:80],
        )
        return ""

    if forced or extract_wake_question(text) is not None:
        return text

    for lang_code in ("uk", "ru"):
        try:
            alt, alt_ns = await _whisper_once(
                wav_bytes,
                session=session,
                filename=filename,
                language=lang_code,
            )
        except (AIError, aiohttp.ClientError):
            continue
        if alt_ns >= 0.55 or _looks_like_hallucination(alt):
            continue
        if extract_wake_question(alt) is not None:
            log.info(
                "Whisper %s retry caught wake: %r → %r",
                lang_code,
                text[:80],
                alt[:80],
            )
            return alt
        if looks_cyrillic(alt) and not looks_cyrillic(text):
            text = alt
    return text



def _normalize_wake_transcript(transcript: str) -> str:
    """Rewrite Whisper mangling of the Dream wake word at the start."""
    text = (transcript or "").strip()
    if not text:
        return text

    text = re.sub(
        r"(?i)^\s*(?:d\s*\.?\s*m\s*\.?\s*v|д\s*\.?\s*м\s*\.?\s*в)\b[\s,.\!?:\-]*",
        "Dream, ",
        text,
        count=1,
    )

    m = re.match(
        r"(?i)^\s*(hey\s+|эй\s+|эй,\s*|ok\s+)?"
        r"([a-zа-яё0-9.]+)"
        r"([\s,.\!?:\-]+)(.*)$",
        text,
        flags=re.DOTALL,
    )
    if m:
        prefix, word, sep, rest = (
            m.group(1) or "",
            m.group(2),
            m.group(3),
            m.group(4) or "",
        )
        if _token_looks_like_dream(word):
            return f"{prefix}Dream{sep}{rest}"
        return text

    m = re.match(
        r"(?i)^\s*(hey\s+|эй\s+|эй,\s*)?([a-zа-яё0-9.]+)(.*)$",
        text,
        flags=re.DOTALL,
    )
    if m:
        prefix, word, rest = m.group(1) or "", m.group(2), m.group(3) or ""
        if _token_looks_like_dream(word):
            return f"{prefix}Dream{rest}"
    return text


def extract_wake_question(transcript: str) -> str | None:
    """If transcript addresses Dream, return the question after the wake word."""
    text = _normalize_wake_transcript(transcript)
    if not text:
        return None
    match = _WAKE_RE.search(text)
    if not match:
        match = re.match(
            r"(?i)^\s*(?:hey\s+|эй\s+)?dream(?:\s+team)?[\s,.\!?:\-]*",
            text,
        )
        if not match:
            return None
    after = text[match.end() :].strip(" \t\n\r,.-")
    before = text[: match.start()].strip(" \t\n\r,.-")
    # "Thank Dream" / "Leave Dream" — wake word at the end
    if not after and before:
        return before
    return after or None


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
        "meta",
        "метан",
        "strong",
        "weak",
        "играбел",
        "баланс",
        "how is",
        "how's",
        "what about",
        "как там",
        "что с",
        "что по",
    )
    return any(k in low for k in keys)


def _wants_hero_lookup(text: str) -> str | None:
    """If the user is asking about an OW hero's balance, return the hero query."""
    text = fix_ow_asr(text or "")
    hero = extract_hero_query(text)
    if not hero:
        return None
    if _patch_question(text):
        return hero
    # Balance vibes — not jokes / stories that merely mention a hero
    if re.search(
        r"(?i)\b(how'?s|how is|what about|was|were|did|has|"
        r"как там|что с|что по|як там|що з|що по|нерф|бафф|патч)\b",
        text,
    ):
        return hero
    # Short "Dream, Doomfist?" / "Tracer?"
    if len(text.split()) <= 5:
        return hero
    return None


async def handle_user_turn(
    question: str,
    *,
    session: aiohttp.ClientSession,
    bot,
    guild_id: int,
    user_id: int,
    voice: bool = True,
) -> str:
    """Answer a user turn (after wake word, convo follow-up, or pending yes)."""
    raw = fix_ow_asr((question or "").strip())
    sess = peek_session(guild_id, user_id)
    # Affirmatives / tiny follow-ups keep session language; long Latin = EN
    lang = detect_user_language(
        raw, fallback=(sess.last_lang if sess else None)
    )
    if not raw:
        return empty_prompt_reply(lang)

    q = resolve_question(raw, sess)
    if not is_affirmative(raw) and len(raw.split()) >= 3:
        lang = detect_user_language(raw, fallback=None)
    history = list(sess.history) if sess and sess.history else None
    context_line = ""
    if sess and sess.last_hero:
        context_line = f"LAST HERO in this chat: {sess.last_hero}\n"
    lang_line = language_instruction(lang) + "\n"

    # Follow-up: user said yes to hearing last patch details
    if is_affirmative(q) and peek_pending_offer(guild_id, user_id):
        offer = pop_pending_offer(guild_id, user_id)
        assert offer is not None
        reply = patch_details_from_facts(
            offer.facts, hero=offer.hero, lang=lang
        )
        remember_turn(
            guild_id,
            user_id,
            user=raw,
            assistant=reply,
            hero=offer.hero,
            lang=lang,
        )
        return reply

    # Overwatch hero → Blizzard patch notes (uses cache; stop when found)
    hero = _wants_hero_lookup(q)
    if not hero and sess and sess.last_hero and _patch_question(q):
        # "was he nerfed?" / "а нерфы?" with no new name → last hero
        hero = sess.last_hero.lower()
    if hero:
        hit, latest_date = await lookup_hero_patch(bot, guild_id, hero)
        if hit is None:
            prompt = (
                f"{lang_line}{context_line}"
                "User asked about an Overwatch hero. We checked Blizzard patch "
                "notes and didn't find recent retail hero updates for them "
                f"(latest listed date: {latest_date or 'unknown'}). "
                "Say that casually in one short sentence.\n\n"
                f"User: {q}"
            )
            reply = await generate_reply(
                prompt,
                session=session,
                max_tokens=80,
                temperature=0.25,
                history=history,
                lang=lang,
            )
            remember_turn(
                guild_id, user_id, user=raw, assistant=reply, hero=hero, lang=lang
            )
            return reply

        facts = facts_block(hit)
        set_pending_offer(guild_id, user_id, hero=hit.hero_name, facts=facts)
        # Template teaser — buff/nerf/date come only from Blizzard facts
        reply = patch_teaser(hit, lang)
        remember_turn(
            guild_id,
            user_id,
            user=raw,
            assistant=reply,
            hero=hit.hero_name,
            lang=lang,
        )
        return reply

    # General chat — any topic, with memory so references make sense
    style = (
        "Reply like a real friend in voice chat: natural, warm, 1–2 short "
        "sentences. Use context if they refer to something earlier. "
        "Answer directly. If unsure about a real person or fact, say you "
        "don't know — never invent. No markdown, no filler."
        if voice
        else (
            "Reply like a helpful friend: natural and concise. Use chat context. "
            "If unsure about a real person or fact, say you don't know."
        )
    )
    reply = await generate_reply(
        f"{lang_line}{context_line}{style}\n\nUser: {q}",
        session=session,
        max_tokens=120 if voice else 200,
        temperature=0.35,
        history=history,
        lang=lang,
    )
    maybe_hero = extract_hero_query(q)
    remember_turn(
        guild_id,
        user_id,
        user=raw,
        assistant=reply,
        hero=maybe_hero,
        lang=lang,
    )
    return reply


def _looks_like_followup(text: str) -> bool:
    """During the convo window, ignore normal teammate chatter."""
    t = (text or "").strip()
    if len(t) < 2:
        return False
    if "?" in t or is_affirmative(t):
        return True
    if is_leave_command(t) or is_thanks_leave_command(t):
        return True
    if extract_hero_query(t) or _patch_question(t):
        return True
    if _PRONOUN_RE.search(t) and len(t.split()) <= 14:
        return True
    if re.search(
        r"(?i)\b(dream|дрим|tell me|what about|how about|"
        r"а що|а что|почему|why|when|who|joke|шутк)\b",
        t,
    ):
        return True
    # Very short only if it looks like a question-ish cue
    if len(t.split()) <= 4 and re.search(
        r"(?i)\b(yes|no|да|нет|ok|okay|sure|why|how|what)\b", t
    ):
        return True
    return False


async def voice_reply_from_wav(
    wav_bytes: bytes,
    *,
    session: aiohttp.ClientSession,
    bot,
    guild_id: int,
    user_id: int,
) -> VoiceTurnResult | None:
    """
    Transcribe → wake / active convo / pending-yes → answer.
    Returns None if this utterance should be ignored.
    """
    transcript = await transcribe_audio(wav_bytes, session=session)
    if not (transcript or "").strip():
        log.info("Empty / non-speech transcript ignored")
        return None
    log.info("Voice transcript: %r", transcript[:200])

    pending = peek_pending_offer(guild_id, user_id)
    sess = peek_session(guild_id, user_id)
    raw_t = transcript.strip()

    # "Thank Dream" / "дякую Dream" even if wake parsing is odd
    if is_thanks_leave_command(raw_t):
        lang = detect_user_language(
            raw_t, fallback=(sess.last_lang if sess else None)
        )
        touch_session(guild_id, user_id, last_lang=lang)
        return VoiceTurnResult(
            reply=leave_goodbye(lang, thanks=True), leave=True
        )

    wake_q = extract_wake_question(transcript)
    from_wake = wake_q is not None

    if wake_q is not None:
        question = wake_q
        touch_session(guild_id, user_id)
    elif pending and is_affirmative(transcript):
        question = transcript.strip()
        touch_session(guild_id, user_id)
    elif sess is not None and _looks_like_followup(transcript):
        # Same user, still in the post-wake conversation window
        question = transcript.strip()
        touch_session(guild_id, user_id)
        log.info("Convo follow-up (no wake) guild=%s user=%s", guild_id, user_id)
    else:
        return None

    # Voice leave / thanks (all languages) — before LLM
    sess2 = peek_session(guild_id, user_id)
    lang = detect_user_language(
        question, fallback=(sess2.last_lang if sess2 else None)
    )
    thanks = is_thanks_leave_command(question, after_wake=from_wake)
    if thanks or is_leave_command(question):
        # Leave needs wake (or explicit "thank Dream"); bare "leave" in convo OK
        if thanks and not from_wake and not is_thanks_leave_command(raw_t):
            pass  # fall through to normal chat
        else:
            bye = leave_goodbye(lang, thanks=bool(thanks))
            touch_session(guild_id, user_id, last_lang=lang)
            return VoiceTurnResult(reply=bye, leave=True)

    reply = await handle_user_turn(
        question,
        session=session,
        bot=bot,
        guild_id=guild_id,
        user_id=user_id,
        voice=True,
    )
    return VoiceTurnResult(reply=reply, leave=False)


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
        description="Ask Dream (smart Llama via Groq)",
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
        embed.set_footer(text=f"Dream · {interaction.user.display_name}")
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
