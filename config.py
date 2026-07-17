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
YTDLP_COOKIES = Path(_cookies_env) if _cookies_env else BASE_DIR / "cookies.txt"

# Discord nickname limit
MAX_NICK_LENGTH = 32

# How often to refresh Discord display names in server nicknames
NICKNAME_SYNC_HOURS = 24

# Rotate idle title every N seconds while nothing is playing
IDLE_ROTATE_SECONDS = 90

# Timeout waiting for a new member to type their real name (minutes)
NAME_PROMPT_TIMEOUT_MINUTES = 30

# Birthday announcements (local wall-clock for the server community)
BIRTHDAY_TIMEZONE = os.getenv("BIRTHDAY_TIMEZONE", "Europe/Kyiv").strip() or "Europe/Kyiv"
# Check every hour; announce once when local hour reaches this value
BIRTHDAY_ANNOUNCE_HOUR = int(os.getenv("BIRTHDAY_ANNOUNCE_HOUR", "10"))
