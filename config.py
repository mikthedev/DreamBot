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

# Optional Netscape cookies file for YouTube (bot-hosting / datacenter IPs)
_cookies_env = os.getenv("YTDLP_COOKIES", "").strip()
YTDLP_COOKIES = (Path(_cookies_env) if _cookies_env else BASE_DIR / "cookies.txt").expanduser()
if not YTDLP_COOKIES.is_absolute():
    YTDLP_COOKIES = (BASE_DIR / YTDLP_COOKIES).resolve()

# Optional HTTP(S)/SOCKS proxy for yt-dlp (residential recommended on bot-hosting).
# Example: socks5://user:pass@host:port — must include a URL scheme.
# Do NOT put the panel/SFTP host (e.g. prem-eu5.bot-hosting.cloud:20790) here.
_proxy_raw = os.getenv("YTDLP_PROXY", "").strip()
YTDLP_PROXY_INVALID = bool(_proxy_raw) and "://" not in _proxy_raw
YTDLP_PROXY = "" if YTDLP_PROXY_INVALID else _proxy_raw

# Discord nickname limit
MAX_NICK_LENGTH = 32

# How often to refresh Discord display names in server nicknames
NICKNAME_SYNC_HOURS = 24

# Rotate idle title every N seconds while nothing is playing
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

# Free Llama via Groq (no billing) — https://console.groq.com/keys
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
# 70B is still very fast on Groq LPUs and much smarter than 8B
GROQ_MODEL = (
    os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile").strip()
    or "llama-3.3-70b-versatile"
)
GROQ_WHISPER_MODEL = (
    os.getenv("GROQ_WHISPER_MODEL", "whisper-large-v3").strip()
    or "whisper-large-v3"
)
# Optional: force Whisper language (ru / en / uk). Empty = auto-detect.
GROQ_WHISPER_LANGUAGE = os.getenv("GROQ_WHISPER_LANGUAGE", "").strip().lower()

# Free edge-tts — masculine default; Russian replies use TTS_VOICE_RU
TTS_VOICE = os.getenv("TTS_VOICE", "en-US-GuyNeural").strip() or "en-US-GuyNeural"
TTS_VOICE_RU = (
    os.getenv("TTS_VOICE_RU", "ru-RU-DmitryNeural").strip() or "ru-RU-DmitryNeural"
)
# Slightly more expressive delivery
TTS_RATE = os.getenv("TTS_RATE", "+8%").strip() or "+8%"
TTS_PITCH = os.getenv("TTS_PITCH", "+4Hz").strip() or "+4Hz"
