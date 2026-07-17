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
