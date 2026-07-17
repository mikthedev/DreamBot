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

## Admin commands

Only the **server owner** or members with **Administrator** / **Manage Server**:

| Command | What it does |
|---|---|
| `/setwelcome #channel` | Where join prompts are posted |
| `/setautorole @Role` | Role given to new members |
| `/setname @user Миша` | Set/fix someone’s real name + nickname |
| `/setbirthdaychannel #channel` | Where birthday messages are posted |
| `/syncnicks` | Force nickname sync now |

### Everyone

| Command | What it does |
|---|---|
| `/setbirthday 15.03` | Save your birthday (`DD.MM` or `DD.MM.YYYY`) |
| `/mybirthday` | Show your saved birthday |
| `/clearbirthday` | Remove your birthday |

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

## Nickname rules

- On join: bot asks for a real name → sets `DiscordName (RealName)`
- Then optionally asks for birthday (`DD.MM` or `skip`)
- Every **24 hours**: if someone changed their Discord display name, bot updates the left part and **keeps** the real name
- On birthdays (default 10:00 `Europe/Kyiv`): posts a celebration message once per year
- Data is stored in `data/dream_team.db`
