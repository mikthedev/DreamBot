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

YouTube and SoundCloud in voice: `/play`, `/skip`, `/pause`, `/queue`, `/nowplaying`, `/stop`.

While a track plays, the bot shows rich presence (title, artist, progress, queue). A live now-playing card can sit in a channel of your choice.

If the channel empties, the bot leaves after **10 seconds**.

---

## Dream AI

**100% free** — Llama on [Groq](https://console.groq.com/keys) (no Google billing). Whisper for voice, edge-tts for speech.

| How | What happens |
|---|---|
| `/ask …` | Text chat in an embed |
| @mention the bot | Same, in the channel |
| `/join` then say **Dream, …** | Hears you in VC, answers out loud (and posts text) |

Example: *Dream, was Genji patched?*

`/disconnect` (or `/leave`) stops voice AI and music. Wake word only — casual talk without **Dream** is ignored.

---

## Overwatch

Automatic patch-note posts from Blizzard and periodic Counterwatch tier lists, with hero icons and rates. Channel and preview/post controls live under **`/panel` → Overwatch**.

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
| `/play` · `/pause` · `/skip` · `/queue` · `/stop` · `/leave` | Music |

**Admins**

| | |
|---|---|
| `/panel` | Channels, roles, names, birthdays, Overwatch, onboarding, anniversary, status |

One hub, fewer slash commands — setup that used to be scattered now lives in the panel.
