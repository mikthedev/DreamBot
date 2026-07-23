# Dream Team Bot

Python Discord bot for the Dream Team server: welcome new members, set nicknames like `MikeGTC (Миша)`, sync Discord display names once per day, and auto-assign a role.

## Setup (Discord)

1. Open [Discord Developer Portal](https://discord.com/developers/applications) → **New Application**.
2. **Bot** → Add Bot → copy the token.
3. Enable these **Privileged Gateway Intents**:
   - Server Members Intent
   - Message Content Intent
4. **OAuth2 → URL Generator**:
   - Scopes: `bot`, `applications.commands`
   - Permissions: Manage Nicknames, Manage Roles, Send Messages, Read Message History, View Channels
5. Open the invite URL and add the bot to your server.
6. In **Server Settings → Roles**, drag the bot’s role **above** members it should rename.

## Setup (local)

```bash
cd "/Users/user/Desktop/Development/Dream Team Bot"
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# paste DISCORD_TOKEN into .env
python bot.py
```

### Bot-hosting (Python egg)

YouTube needs a small **Deno** binary for yt-dlp’s JS challenges. The bot downloads it automatically into `.local/bin/` on first start (no Node required).

Optional: set `START_BASH_FILE=start.sh` to install Deno before Python boots.

Keep `cookies.txt` in the server root as before.

If YouTube still says “not a bot” with cookies (common on shared hosting IPs), set a residential proxy in `.env`:

```bash
YTDLP_PROXY=socks5://user:pass@host:port
```

SoundCloud URLs work without this.

## Admin commands

Only the **server owner** or members with **Administrator** / **Manage Server**:

| Command | What it does |
|---|---|
| `/panel` | **Control panel** — channels, names, birthdays, Overwatch, onboarding, anniversary |
| `/help` | Command guide (admins get a button into the panel) |

Setup that used to be separate slash commands (welcome channel, auto-role, names, birthday signup, anniversary, Overwatch) lives in **`/panel`**.

### Everyone

| Command | What it does |
|---|---|
| `/help` | How to use the bot |
| `/ask <question>` | Ask Gemini AI (also works if you @mention the bot) |
| `/listen` | Join VC; answer when you say **Dream**, … |
| `/unlisten` | Stop wake-word listening |
| `/setbirthday 15.03` | Save your birthday (`DD.MM` or `DD.MM.YYYY`) |
| `/mybirthday` | Show your saved birthday |
| `/clearbirthday` | Remove your birthday |

### Gemini AI (optional)

1. Create a free API key at [Google AI Studio](https://aistudio.google.com/apikey).
2. Add to `.env`:

```bash
GEMINI_API_KEY=your_key_here
# optional:
# GEMINI_MODEL=gemini-2.0-flash
# TTS_VOICE=en-US-JennyNeural
```

3. Restart the bot. Use `/ask`, mention the bot, or `/listen` in voice.

**Voice wake word:** join a VC → `/listen` → say *Dream, was Genji patched?*  
The bot only runs STT after you finish speaking (silence), and only answers if it hears **Dream**. Spoken replies use free **edge-tts** (skipped while music is playing; text reply still posts in the channel where you ran `/listen`).

If an admin sets someone’s birthday to **today**, the celebration posts immediately in the birthday channel (and still runs daily at the configured hour).

SQLite database file: `data/dream_team.db` (created automatically; on bot-hosting: `/home/container/data/dream_team.db`). Use `/panel` → **Bot status** to see counts.
### Music (YouTube / SoundCloud)

Join a voice channel, then:

| Command | What it does |
|---|---|
| `/play <url or search>` | Play or queue a track |
| `/skip` | Skip current track |
| `/pause` / `/resume` | Pause / resume |
| `/queue` | Show queue |
| `/nowplaying` | Current track |
| `/stop` or `/leave` | Stop and disconnect |

While a track plays, the bot uses a **Discord RPC-style Rich Presence** (`rich_presence.py`), equivalent to:

| C SDK field | Music bot value |
|---|---|
| `details` | Song title |
| `state` | Artist · Requested by … |
| `startTimestamp` / `endTimestamp` | Track progress bar |
| `largeImageKey` / `largeImageText` | `dreamteam` + song title |
| `smallImageKey` / `smallImageText` | `youtube` / `soundcloud` |
| `partyId` / `partySize` / `partyMax` | Queue size as party counter |
| `joinSecret` | Not available for bots (Game SDK only) |

### Enable presence icons (one-time)

1. Open [Discord Developer Portal](https://discord.com/developers/applications) → your app  
2. **Rich Presence → Art Assets**  
3. Upload these files from `assets/presence/` with **exact** names (no extension in the key):
   - `dreamteam` ← `dreamteam.png`
   - `youtube` ← `youtube.png`
   - `soundcloud` ← `soundcloud.png`
4. Wait a few minutes after upload, then restart the bot

Needs **FFmpeg** installed on the machine, plus bot permissions: Connect, Speak, Use Voice Activity.

If YouTube returns “Sign in to confirm you’re not a bot” (common on shared hosting), export cookies from a logged-in browser (Firefox + “Get cookies.txt LOCALLY”), save as `cookies.txt` next to the bot, and restart. Cookies expire periodically — re-export when playback breaks again.

## Nickname rules

- On join: bot asks for a real name → sets `DiscordName (RealName)`
- Then optionally asks for birthday (`DD.MM` or `skip`)
- Every **24 hours**: if someone changed their Discord display name, bot updates the left part and **keeps** the real name
- On birthdays (default 10:00 `Europe/Kyiv`): posts a celebration message once per year
- Data is stored in `data/dream_team.db`
