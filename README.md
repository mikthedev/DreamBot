# Dream Team Bot

The Discord companion for **Dream Team** — nicknames, birthdays, music, Overwatch news, and a voice AI you can call by name.

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

Automatic **forum posts** for Blizzard patch notes, Counterwatch [tier lists](https://www.counterwatch.gg/stats/overwatch/tier-list), [best one-tricks](https://www.counterwatch.gg/stats/overwatch/best-onetricks), and filtered general **News** from Bluesky (map/mode/lore — no shop, skins, OWCS, or patch dumps). Patch / tier / META keep **one locked post** edited in place. News creates a locked thread per story (tag **News**), with optional **Custom post** from `/panel`. Video posts share a Bluesky link (no download/transcode); image posts still attach files. Configure under **`/panel` → Overwatch**.

Patch cards include **Hero Updates** as well as classic Tank / Damage / Support sections (Blizzard’s layout varies by drop). Live patch posts refresh if the notes gain balance lines after the first publish.

**Hero history** — `/hero` (autocomplete), the **Hero history** button under live patch posts, or **Hero history** in `/panel` → Overwatch (creates a locked **Patch Notes** hub titled **Search Hero Changes**). Browse recent buffs/nerfs for one character. Data comes from live Blizzard notes plus the guild’s saved patch archive.

**Hero icons** come from the official [Blizzard roster](https://overwatch.blizzard.com/en-us/heroes/) (Counterwatch as fallback). Application emojis are created and updated when a new hero ships or a CDN portrait changes — daily sync, plus an extra refresh after a new patch posts — so tier, META, and patch posts stay current.

---

## Onboarding

A persistent Ukrainian welcome panel with buttons — edit and republish without leaving the admin hub.

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
| `/panel` | Channels, roles, names, birthdays, Overwatch, onboarding, anniversary, status |

One hub, fewer slash commands — setup that used to be scattered now lives in the panel.
