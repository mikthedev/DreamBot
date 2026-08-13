# Dream Team Bot

The Discord companion for **Dream Team** — nicknames, birthdays, music, Overwatch news, play-together nights, and a voice AI you can call by name.

---

## Welcome & names

New members get a welcome, optional auto-role, and a quick **Set my name** flow.

Nicknames land as `DiscordName (RealName)` — for example `MikeGTC (Миша)`. Display names sync daily so the real name stays put when someone changes their Discord name.

Admins manage everything from **`/panel`** → Names (including a one-tap nickname sync).

---

## Birthdays & anniversary

Members save their day with `/setbirthday`. The bot celebrates at the configured hour, and admins can preview, announce, and post signup panels from the control panel.

Once a year, Dream Team’s founding date (**28.06.2017**) gets its own anniversary post — also editable in `/panel`.

---

## Music

SoundCloud in voice: `/play`, `/skip`, `/pause`, `/queue`, `/nowplaying`, `/stop`. Paste a SoundCloud link or search by name. YouTube is not supported; Spotify is planned later.

While a track plays, the bot shows rich presence (title, artist, progress, queue). A live now-playing card can sit in a channel of your choice.

If the channel empties, the bot leaves after **10 seconds**.

---

## Dream AI

**100% free** — Llama on [Groq](https://console.groq.com/keys) (no Google billing). Whisper for voice, edge-tts for speech (masculine EN / RU voices).

| How | What happens |
|---|---|
| `/ask …` | Short casual chat |
| @mention the bot | Same, in the channel |
| `/join` then say **Dream, …** | Voice answers (understands Russian; wake word stays **Dream**). One tip on join — stays quiet until woken. Leaves after 2.5 min with no wake. |

Ask about any Overwatch hero and Dream scans the live [Blizzard patch notes](https://overwatch.blizzard.com/en-us/news/patch-notes/) (including older months on that site), then answers with the **patch date** — never the patch title. Example: *Dream, was Genji nerfed?* → short take, then offer details; say **yes** / **да** for highlights. Ask anything else and Dream still answers naturally like a teammate.

Voice transcript embeds (text copies of what Dream said) go to a channel you set in **`/panel` → Dream AI**, and the bot deletes them after **24 hours**.

---

## Overwatch

Automatic **forum posts** for Blizzard patch notes, Counterwatch [tier lists](https://www.counterwatch.gg/stats/overwatch/tier-list), [best one-tricks](https://www.counterwatch.gg/stats/overwatch/best-onetricks), and filtered general **News** from Bluesky (map/mode/lore — no shop, skins, OWCS, or patch dumps). Patch / tier / META keep **one post** edited in place. **Patch Notes** threads stay unlocked so buttons work; META / News stay locked (reactions only). News creates a thread per story (tag **News**), with optional **Custom post** from `/panel`. Video posts share a Bluesky link (no download/transcode); image posts still attach files. Configure under **`/panel` → Overwatch**.

Patch cards include **Hero Updates** as well as classic Tank / Damage / Support sections (Blizzard’s layout varies by drop). Live patch posts refresh if the notes gain balance lines after the first publish.

**Hero history** — `/hero` (autocomplete), the **Hero history** button under live patch posts, or **Hero history** in `/panel` → Overwatch (creates an unlocked **Patch Notes** hub titled **Search Hero Changes**). Pick a role, then a hero, to browse recent buffs/nerfs. **Notify me** sends a private DM when that hero is patched — nothing extra is posted in the hub, so forum followers aren’t pinged. Data comes from live Blizzard notes plus the guild’s saved patch archive.

**Hero icons** come from the official [Blizzard roster](https://overwatch.blizzard.com/en-us/heroes/) (Counterwatch as fallback). Application emojis are created and updated when a new hero ships or a CDN portrait changes — daily sync, plus an extra refresh after a new patch posts — so tier, META, and patch posts stay current.

---

## Onboarding

A persistent Ukrainian welcome panel with buttons — edit and republish without leaving the admin hub.

---

## Play together

The bot notices when several people on the server have recently played the same multiplayer game, then offers a session — without treating “played it” as a yes.

**Activity is a hint. I'm in is intent. An admin can confirm someone by hand. A Discord Event is the end result.** Those are never mixed.

1. **Activity history** — Discord rich presence (Playing …) is stored per person, with recency decay. Playing Minecraft two days ago is a much stronger signal than six months ago. Windows and weights are set in `/panel` → Play together → Settings.
2. **Detection** — finds overlapping interest (you do **not** have to be in voice at the same time). One person is ignored; two is a maybe; four or so is a real signal. Regular launches count extra.
3. **Allow-list** — nothing is proposed unless an admin **allows** that game. Blocked games are still recorded, never auto-suggested (Warframe can be popular and still stay off-limits). **Add game** searches Steam and Wikipedia so you pick a real title (Minecraft has no Steam page — Wikipedia is the match). Sessions are not created from made-up names.
4. **Suggestion** — a message in the configured channel: game, next Saturday evening (default 19:00, Kyiv time), I'm in / Nope. Playing the game does **not** put you on the list.
5. **RSVP** — the card shows `3/6` (or similar). Admins can add or remove people in `/panel` when plans happened in chat.
6. **Social invites** — once the minimum shows up, the bot may DM people who often sit in voice or play with that group: “Ilya, Sasha and Edik are getting together… Wanna join them?”
7. **Discord Event** — at the minimum (or when an admin taps Create event), a scheduled event is created on the **existing** voice channel you picked (e.g. General). No extra channels.

**Required for automatic game history:** in the [Discord Developer Portal](https://discord.com/developers/applications) → Bot → Privileged Gateway Intents, turn on **Presence Intent**, then set `PLAY_PRESENCE_INTENT=1` in the host env and restart. The bot starts without it (manual suggestions still work); requesting Presence before it is enabled in the portal crashes login. The bot also needs **Create Events** (and the usual send/embed permissions) on the suggestion and voice channels. Personal invites are DMs — members who block DMs are skipped.

Auto suggestions, auto events, and personal invites each have an on/off toggle. Detection never publishes if auto is off — Review still shows overlap so you can post by hand.

---

## Commands at a glance

**Everyone**

| | |
|---|---|
| `/help` | Quick guide |
| `/ask` | Chat with free Llama (Groq) |
| `/join` / `/disconnect` | Voice AI in / out |
| `/setbirthday` · `/mybirthday` · `/clearbirthday` | Birthday self-service |
| `/hero` | One hero’s balance changes across recent patches |
| `/play` · `/pause` · `/skip` · `/queue` · `/stop` · `/leave` | Music (SoundCloud) |

**Admins**

| | |
|---|---|
| `/panel` | Channels, roles, names, birthdays, Overwatch, play together, onboarding, anniversary, status |

One hub, fewer slash commands — setup that used to be scattered now lives in the panel.
