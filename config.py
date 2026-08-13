import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
WELCOME_CHANNEL_ID = os.getenv("WELCOME_CHANNEL_ID", "").strip()
DATABASE_PATH = DATA_DIR / "dream_team.db"

# Discord nickname limit
MAX_NICK_LENGTH = 32

# How often to refresh Discord display names in server nicknames
NICKNAME_SYNC_HOURS = 24

# Rotate idle Watching title every N seconds while nothing is playing
IDLE_ROTATE_SECONDS = 90
# Seconds after music stops before restoring full idle presence
IDLE_AFTER_SECONDS = 120

# Timeout waiting for a new member to type their real name (minutes)
NAME_PROMPT_TIMEOUT_MINUTES = 30

# Birthday announcements (local wall-clock for the server community)
BIRTHDAY_TIMEZONE = os.getenv("BIRTHDAY_TIMEZONE", "Europe/Kyiv").strip() or "Europe/Kyiv"
# Check every hour; announce once when local hour reaches this value
BIRTHDAY_ANNOUNCE_HOUR = int(os.getenv("BIRTHDAY_ANNOUNCE_HOUR", "10"))

# Overwatch patch notes (official Battle.net page, checked once per day)
OW_PATCH_URL = "https://overwatch.blizzard.com/en-us/news/patch-notes/"
OW_PATCH_CHECK_HOURS = max(1, int(os.getenv("OW_PATCH_CHECK_HOURS", "24")))

# Counterwatch tier list (announce about once every 2 weeks)
OW_TIER_URL = "https://www.counterwatch.gg/stats/overwatch/tier-list"
OW_TIER_INTERVAL_DAYS = max(1, int(os.getenv("OW_TIER_INTERVAL_DAYS", "14")))
OW_TIER_CHECK_HOURS = max(1, int(os.getenv("OW_TIER_CHECK_HOURS", "24")))

# Counterwatch best one-tricks / META (same cadence as tier list by default)
OW_META_URL = "https://www.counterwatch.gg/stats/overwatch/best-onetricks"
OW_META_INTERVAL_DAYS = max(1, int(os.getenv("OW_META_INTERVAL_DAYS", "14")))
OW_META_CHECK_HOURS = max(1, int(os.getenv("OW_META_CHECK_HOURS", "24")))

# Bluesky Overwatch news (filtered) — hourly checks
OW_NEWS_BSKY_ACTOR = (
    os.getenv("OW_NEWS_BSKY_ACTOR", "owcavalry.com").strip() or "owcavalry.com"
)
OW_NEWS_CHECK_HOURS = max(1, int(os.getenv("OW_NEWS_CHECK_HOURS", "1")))
# First-run seed: ~1 day old posts only (not older archive dumps)
OW_NEWS_SEED_MIN_HOURS = max(1, int(os.getenv("OW_NEWS_SEED_MIN_HOURS", "12")))
OW_NEWS_SEED_MAX_HOURS = max(
    OW_NEWS_SEED_MIN_HOURS + 1,
    int(os.getenv("OW_NEWS_SEED_MAX_HOURS", "36")),
)
OW_NEWS_SEED_MAX_POSTS = max(1, int(os.getenv("OW_NEWS_SEED_MAX_POSTS", "10")))
# After seeding, only auto-post items this fresh (hourly catch-up)
OW_NEWS_FRESH_MAX_HOURS = max(1, int(os.getenv("OW_NEWS_FRESH_MAX_HOURS", "3")))
# Close (archive) news forum posts after this many hours so the feed stays clean
OW_NEWS_CLOSE_HOURS = max(1, int(os.getenv("OW_NEWS_CLOSE_HOURS", "6")))
OW_NEWS_CLOSE_CHECK_MINUTES = max(5, int(os.getenv("OW_NEWS_CLOSE_CHECK_MINUTES", "15")))

# Temporary video attach for news / custom posts (deleted after Discord upload)
OW_MEDIA_MAX_MB = max(1, min(25, int(os.getenv("OW_MEDIA_MAX_MB", "24"))))
OW_MEDIA_MAX_BYTES = OW_MEDIA_MAX_MB * 1024 * 1024
OW_MEDIA_MAX_VIDEOS = 1  # keep the bot light — one clip per post
OW_MEDIA_MAX_DURATION_SEC = max(
    0, int(os.getenv("OW_MEDIA_MAX_DURATION_SEC", "180"))
)  # 0 = no duration cap
OW_MEDIA_DOWNLOAD_TIMEOUT = max(
    20, int(os.getenv("OW_MEDIA_DOWNLOAD_TIMEOUT", "90"))
)

# Free Llama via Groq (no billing) — https://console.groq.com/keys
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
# 70B is still very fast on Groq LPUs and much smarter than 8B
GROQ_MODEL = (
    os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile").strip()
    or "llama-3.3-70b-versatile"
)
# Default turbo for lower voice latency; set GROQ_WHISPER_MODEL=whisper-large-v3 for max accuracy
GROQ_WHISPER_MODEL = (
    os.getenv("GROQ_WHISPER_MODEL", "whisper-large-v3-turbo").strip()
    or "whisper-large-v3-turbo"
)
# Optional: force Whisper language (ru / en / uk). Empty = auto-detect.
GROQ_WHISPER_LANGUAGE = os.getenv("GROQ_WHISPER_LANGUAGE", "").strip().lower()

# Free edge-tts — masculine defaults per language
TTS_VOICE = os.getenv("TTS_VOICE", "en-US-GuyNeural").strip() or "en-US-GuyNeural"
TTS_VOICE_RU = (
    os.getenv("TTS_VOICE_RU", "ru-RU-DmitryNeural").strip() or "ru-RU-DmitryNeural"
)
TTS_VOICE_UK = (
    os.getenv("TTS_VOICE_UK", "uk-UA-OstapNeural").strip() or "uk-UA-OstapNeural"
)
# Slightly more expressive delivery
TTS_RATE = os.getenv("TTS_RATE", "+8%").strip() or "+8%"
TTS_PITCH = os.getenv("TTS_PITCH", "+4Hz").strip() or "+4Hz"

# Play together — recency decay and background loops (guild panel can override windows)
# Presence is a privileged intent: leave this off until it is enabled in the
# Developer Portal, or Discord refuses the gateway and the bot never starts.
PLAY_PRESENCE_INTENT = os.getenv("PLAY_PRESENCE_INTENT", "").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)
PLAY_HALF_LIFE_DAYS = max(1.0, float(os.getenv("PLAY_HALF_LIFE_DAYS", "7")))
PLAY_DETECT_INTERVAL_HOURS = max(1, int(os.getenv("PLAY_DETECT_INTERVAL_HOURS", "6")))
PLAY_VOICE_SAMPLE_MINUTES = max(1, int(os.getenv("PLAY_VOICE_SAMPLE_MINUTES", "5")))
PLAY_REMIND_MINUTES = max(5, int(os.getenv("PLAY_REMIND_MINUTES", "60")))
PLAY_EXPAND_MAX = max(1, int(os.getenv("PLAY_EXPAND_MAX", "5")))
PLAY_EXPAND_MIN_SCORE = max(0.1, float(os.getenv("PLAY_EXPAND_MIN_SCORE", "1.5")))
