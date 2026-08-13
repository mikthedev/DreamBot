"""Play together — activity history, detection, suggestions, RSVP, events."""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import discord
from discord.ext import commands, tasks

import config
from nicknames import display_base
from game_search import (
    GameHit,
    hit_from_dict,
    search_catalog_games,
    store_link_markdown,
)

log = logging.getLogger("dream_team.play")

ACCENT = discord.Color.from_rgb(46, 230, 166)
MUTED = discord.Color.from_rgb(90, 110, 140)

RSVP_IN_ID = "play_together:in"
RSVP_NOPE_ID = "play_together:nope"
ACTIVE_STATUSES = ("published", "event")
SATURDAY = 5

_WHEN_FULL = (
    "%d.%m.%Y %H:%M",
    "%d.%m.%Y %H",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d %H",
)
_WHEN_YEARLESS = ("%d.%m %H:%M", "%d.%m %H")


def game_key(name: str) -> str:
    return " ".join((name or "").strip().lower().split())


def recency_weight(days: float, half_life_days: float = 7.0) -> float:
    if half_life_days <= 0:
        return 1.0 if days <= 0 else 0.0
    if days < 0:
        days = 0.0
    return math.exp(-math.log(2) * days / half_life_days)


def should_auto_suggest(people: int, weight_sum: float, min_people: int) -> bool:
    if people < min_people:
        return False
    return weight_sum >= min_people * 0.35


def next_saturday_evening(now: datetime, hour: int = 19) -> datetime:
    hour = max(0, min(23, int(hour)))
    days_ahead = (SATURDAY - now.weekday()) % 7
    candidate = now.replace(hour=hour, minute=0, second=0, microsecond=0) + timedelta(
        days=days_ahead
    )
    if candidate <= now:
        candidate += timedelta(days=7)
    return candidate


def parse_iso(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def parse_play_when(text: str, now: datetime, hour: int = 19) -> datetime | None:
    raw = (text or "").strip()
    if not raw:
        return next_saturday_evening(now, hour=hour)
    for fmt in _WHEN_FULL:
        try:
            dt = datetime.strptime(raw, fmt)
        except ValueError:
            continue
        return dt.replace(tzinfo=now.tzinfo)
    for fmt in _WHEN_YEARLESS:
        try:
            dt = datetime.strptime(raw, fmt)
        except ValueError:
            continue
        dt = dt.replace(year=now.year, tzinfo=now.tzinfo)
        if dt < now - timedelta(days=1):
            dt = dt.replace(year=now.year + 1)
        return dt
    return None


def parse_party_size(text: str, default_min: int, default_max: int) -> tuple[int, int]:
    raw = (text or "").strip()
    if not raw:
        return default_min, default_max
    match = re.match(r"^\s*(\d+)\s*[-/]\s*(\d+)\s*$", raw)
    if match:
        lo, hi = int(match.group(1)), int(match.group(2))
        if lo > hi:
            lo, hi = hi, lo
        return max(1, lo), max(1, hi)
    if raw.isdigit():
        n = max(1, int(raw))
        return n, max(n, default_max)
    return default_min, default_max


def format_play_when(dt: datetime) -> str:
    return (
        f"{dt.strftime('%A')}, {dt.day} {dt.strftime('%B')} · {dt.strftime('%H:%M')}"
    )


def format_days_ago(days: float) -> str:
    if days < 1:
        return "today"
    if days < 2:
        return "yesterday"
    n = int(days)
    return f"{n} days ago"


def join_names(names: list[str]) -> str:
    clean = [n for n in names if n]
    if not clean:
        return "people on the server"
    if len(clean) == 1:
        return clean[0]
    if len(clean) == 2:
        return f"{clean[0]} and {clean[1]}"
    return f"{', '.join(clean[:-1])} and {clean[-1]}"


def social_score(
    *,
    this_game_weight: float,
    voice_minutes: dict[int, int],
    shared_sessions: dict[int, int],
    confirmed_ids: list[int],
    shared_game_affinity: float = 0.0,
) -> float:
    voice_hours = sum(voice_minutes.get(i, 0) for i in confirmed_ids) / 60.0
    sessions = sum(shared_sessions.get(i, 0) for i in confirmed_ids)
    return (
        3.0 * this_game_weight
        + min(voice_hours, 20.0)
        + 2.0 * sessions
        + 0.5 * shared_game_affinity
    )


def iter_playing_names(member: discord.Member) -> list[tuple[str, int | None]]:
    out: list[tuple[str, int | None]] = []
    for act in member.activities or ():
        if getattr(act, "type", None) != discord.ActivityType.playing:
            continue
        name = (getattr(act, "name", None) or "").strip()
        if not name:
            continue
        app_id = getattr(act, "application_id", None)
        out.append((name, int(app_id) if app_id else None))
    return out


def _tz() -> ZoneInfo:
    try:
        return ZoneInfo(config.BIRTHDAY_TIMEZONE)
    except Exception:
        return ZoneInfo("Europe/Kyiv")


def _now_local() -> datetime:
    return datetime.now(_tz())


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def person_label(bot, guild: discord.Guild | None, user_id: int) -> str:
    real = bot.db.get_real_name(guild.id, user_id) if guild else None
    if real:
        return str(real)
    member = guild.get_member(user_id) if guild else None
    if member is not None:
        return display_base(member)
    return f"user {user_id}"


def _settings_int(settings: dict, key: str, default: int) -> int:
    value = settings.get(key)
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


@dataclass
class DetectedGroup:
    game_key: str
    game_name: str
    people: list[tuple[int, float, datetime]]
    weight_sum: float
    enabled: bool
    blocked: bool

    @property
    def allowed(self) -> bool:
        return self.enabled and not self.blocked

    @property
    def count(self) -> int:
        return len(self.people)


def detect_groups(db, guild_id: int, now: datetime | None = None) -> list[DetectedGroup]:
    now = now or _now_utc()
    settings = db.get_play_settings(guild_id)
    detect_days = max(1, _settings_int(settings, "play_detect_days", 14))
    half_life = float(getattr(config, "PLAY_HALF_LIFE_DAYS", 7))
    since = (now - timedelta(days=detect_days)).isoformat()
    rows = db.list_recent_play_games(guild_id, since, min_people=2)
    groups: list[DetectedGroup] = []
    for row in rows:
        key = str(row["game_key"])
        game = db.get_play_game(guild_id, key)
        players = db.list_play_activity_for_game(guild_id, key, since)
        people: list[tuple[int, float, datetime]] = []
        weight_sum = 0.0
        display = str(row["game_name"])
        for player in players:
            last = parse_iso(player["last_seen"])
            if last is None:
                continue
            days = max(0.0, (now - last.astimezone(now.tzinfo)).total_seconds() / 86400)
            weight = recency_weight(days, half_life)
            sessions = max(1, int(player["play_count"] or 1))
            weight *= min(1.0 + 0.15 * (sessions - 1), 1.6)
            people.append((int(player["user_id"]), weight, last))
            weight_sum += weight
            display = str(player["game_name"] or display)
        groups.append(
            DetectedGroup(
                game_key=key,
                game_name=(game["game_name"] if game else display) or display,
                people=people,
                weight_sum=weight_sum,
                enabled=bool(game["enabled"]) if game else False,
                blocked=bool(game["blocked"]) if game else False,
            )
        )
    groups.sort(key=lambda g: (g.weight_sum, g.count), reverse=True)
    return groups


def suggestion_embed(
    bot,
    guild: discord.Guild | None,
    row,
    *,
    confirmed_ids: list[int] | None = None,
) -> discord.Embed:
    confirmed_ids = confirmed_ids if confirmed_ids is not None else [
        int(r["user_id"]) for r in bot.db.list_play_rsvps(int(row["id"]), status="in")
    ]
    when = parse_iso(row["proposed_at"])
    when_local = when.astimezone(_tz()) if when else None
    when_line = format_play_when(when_local) if when_local else "time TBA"
    min_p = int(row["min_players"])
    max_p = int(row["max_players"])
    n = len(confirmed_ids)
    count_line = f"**{n}** people are in · aiming for **{min_p}–{max_p}**"
    if max_p > 0:
        count_line = f"**{n}/{max_p}** are in · need **{min_p}** to lock it in"

    steam = (row["steam_url"] or "").strip()
    note = (row["store_note"] or "").strip()
    names = [person_label(bot, guild, uid) for uid in confirmed_ids[:12]]
    in_line = join_names(names) if names else "_nobody yet — tap I'm in_"

    status = str(row["status"])
    footer = "I'm in = you want this session. Playing the game before is not a yes."
    if status == "event":
        footer = "Discord event is up · same voice channel as always"
    elif status == "cancelled":
        footer = "Cancelled"
    elif status == "completed":
        footer = "This one already happened"

    description = (
        f"A few people on the server already played **{row['game_name']}**. "
        f"Let's get together and play again.\n\n"
        f"**{when_line}**\n"
        f"{count_line}\n"
        f"In: {in_line}"
    )
    link = store_link_markdown(steam)
    if link:
        description += f"\n\n{link}"
    if note:
        description += f"\n{note}" if link else f"\n\n{note}"
    description += "\nEveryone's welcome."

    color = ACCENT if status in ACTIVE_STATUSES else MUTED
    embed = discord.Embed(
        title=str(row["game_name"]),
        description=description,
        color=color,
    )
    embed.set_author(name="Play together")
    embed.set_footer(text=footer)
    return embed


class PlayRsvpView(discord.ui.View):
    """Public I'm in / Nope — survives restarts."""

    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="I'm in",
        style=discord.ButtonStyle.success,
        custom_id=RSVP_IN_ID,
    )
    async def im_in(
        self, interaction: discord.Interaction, _button: discord.ui.Button
    ) -> None:
        await _handle_rsvp(interaction, "in")

    @discord.ui.button(
        label="Nope",
        style=discord.ButtonStyle.secondary,
        custom_id=RSVP_NOPE_ID,
    )
    async def nope(
        self, interaction: discord.Interaction, _button: discord.ui.Button
    ) -> None:
        await _handle_rsvp(interaction, "nope")


class PlayExpandInButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"play_exp:in:(?P<sid>[0-9]+)",
):
    def __init__(self, suggestion_id: int) -> None:
        super().__init__(
            discord.ui.Button(
                label="I'm in",
                style=discord.ButtonStyle.success,
                custom_id=f"play_exp:in:{suggestion_id}",
            )
        )
        self.suggestion_id = suggestion_id

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Button,
        match: re.Match[str],
        /,
    ):
        return cls(int(match["sid"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        await _handle_rsvp(interaction, "in", suggestion_id=self.suggestion_id)


class PlayExpandNopeButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"play_exp:nope:(?P<sid>[0-9]+)",
):
    def __init__(self, suggestion_id: int) -> None:
        super().__init__(
            discord.ui.Button(
                label="Nope",
                style=discord.ButtonStyle.secondary,
                custom_id=f"play_exp:nope:{suggestion_id}",
            )
        )
        self.suggestion_id = suggestion_id

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Button,
        match: re.Match[str],
        /,
    ):
        return cls(int(match["sid"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        await _handle_rsvp(interaction, "nope", suggestion_id=self.suggestion_id)


class PlayExpandView(discord.ui.View):
    def __init__(self, suggestion_id: int) -> None:
        super().__init__(timeout=None)
        self.add_item(PlayExpandInButton(suggestion_id))
        self.add_item(PlayExpandNopeButton(suggestion_id))


async def _handle_rsvp(
    interaction: discord.Interaction,
    status: str,
    *,
    suggestion_id: int | None = None,
) -> None:
    bot = interaction.client
    db = bot.db
    if suggestion_id is None:
        if interaction.message is None or interaction.channel is None:
            await interaction.response.send_message(
                "Couldn't find that session.", ephemeral=True
            )
            return
        row = db.get_play_suggestion_by_message(
            interaction.channel.id, interaction.message.id
        )
    else:
        row = db.get_play_suggestion(suggestion_id)
    if row is None:
        await interaction.response.send_message(
            "That session is gone.", ephemeral=True
        )
        return
    if str(row["status"]) not in ACTIVE_STATUSES:
        await interaction.response.send_message(
            "This session isn't open anymore.", ephemeral=True
        )
        return

    sid = int(row["id"])
    user_id = interaction.user.id
    confirmed = [int(r["user_id"]) for r in db.list_play_rsvps(sid, status="in")]
    if status == "in" and user_id not in confirmed:
        max_p = int(row["max_players"])
        if max_p > 0 and len(confirmed) >= max_p:
            await interaction.response.send_message(
                "This session is full.", ephemeral=True
            )
            return

    db.set_play_rsvp(
        sid, user_id, status, "self", _now_utc().isoformat()
    )
    cog = bot.get_cog("PlayTogetherCog")
    if cog is not None:
        await cog.after_rsvp_change(sid)

    if status == "in":
        msg = "You're in."
    else:
        msg = "Okay — I won't count you for this one."
    if interaction.response.is_done():
        await interaction.followup.send(msg, ephemeral=True)
    else:
        await interaction.response.send_message(msg, ephemeral=True)


def live_playing(guild: discord.Guild) -> list[tuple[discord.Member, str]]:
    out: list[tuple[discord.Member, str]] = []
    for member in guild.members:
        if member.bot:
            continue
        for name, _app in iter_playing_names(member):
            out.append((member, name))
    return out


def _lookback_since(guild_id: int, bot) -> tuple[int, str]:
    settings = bot.db.get_play_settings(guild_id)
    days = max(1, _settings_int(settings, "play_detect_days", 14))
    since = (_now_utc() - timedelta(days=days)).isoformat()
    return days, since


def snapshot_guild_games(bot, guild: discord.Guild) -> int:
    cog = bot.get_cog("PlayTogetherCog")
    if cog is None or not bot.intents.presences:
        return 0
    seen = 0
    for member in guild.members:
        games = iter_playing_names(member)
        if not games:
            continue
        cog.record_member_games(member)
        seen += 1
    return seen


def tracking_field(guild: discord.Guild, bot) -> tuple[str, str]:
    days, since = _lookback_since(guild.id, bot)
    records, people = bot.db.count_play_activity(guild.id, since)
    live = live_playing(guild)
    if not bot.intents.presences:
        return (
            "Watching games",
            "**No.** Presence Intent is off, so Discord does not tell the bot "
            "who is playing. Enable it in the Developer Portal, set "
            "`PLAY_PRESENCE_INTENT=1`, restart.\n"
            f"History ({days}d): **{people}** people · **{records}** records · "
            f"visible right now: **{len(live)}**",
        )
    if live:
        live_line = ", ".join(
            f"{person_label(bot, guild, m.id)} · {game}" for m, game in live[:8]
        )
        extra = f" · +{len(live) - 8} more" if len(live) > 8 else ""
        now_line = live_line + extra
    else:
        now_line = "_nobody in Playing … right now_"
    return (
        "Watching games",
        f"**Yes.** Right now: {now_line}\n"
        f"History ({days}d): **{people}** people · **{records}** records",
    )


def hub_play_embed(guild: discord.Guild, bot) -> discord.Embed:
    settings = bot.db.get_play_settings(guild.id)
    groups = detect_groups(bot.db, guild.id)
    allowed = [g for g in bot.db.list_known_play_games(guild.id) if g["enabled"] and not g["blocked"]]
    active = bot.db.list_play_suggestions(guild.id, statuses=ACTIVE_STATUSES, limit=8)
    auto = "on" if settings["play_auto_enabled"] else "off"
    events = "on" if settings["play_auto_event"] else "off"
    expand = "on" if settings["play_auto_expand"] else "off"

    def ch(cid) -> str:
        if not cid:
            return "_not set_"
        channel = guild.get_channel(int(cid))
        return channel.mention if channel else "_missing_"

    embed = discord.Embed(
        title="Play together",
        description=(
            "The bot watches **who recently played what**, finds overlapping "
            "interest, and can suggest a session. Activity is a hint — "
            "**I'm in** is the real yes. You stay in control of which games "
            "are allowed."
        ),
        color=ACCENT,
    )
    watch_name, watch_value = tracking_field(guild, bot)
    embed.add_field(name=watch_name, value=watch_value, inline=False)
    embed.add_field(
        name="Setup",
        value=(
            f"**Suggest channel** {ch(settings['play_suggest_channel_id'])}\n"
            f"**Event voice** {ch(settings['play_voice_channel_id'])}\n"
            f"Auto suggestions **{auto}** · Discord events **{events}** · "
            f"Personal invites **{expand}**"
        ),
        inline=False,
    )
    embed.add_field(
        name="Weights",
        value=(
            f"Activity stays relevant **{settings['play_decay_days']}d** · "
            f"look back **{settings['play_detect_days']}d**\n"
            f"Auto-suggest at **{settings['play_detect_min_people']}** people · "
            f"default party **{settings['play_default_min_players']}–"
            f"{settings['play_default_max_players']}** · Saturday "
            f"**{int(settings['play_default_hour']):02d}:00**"
        ),
        inline=False,
    )
    allowed_names = ", ".join(f"**{r['game_name']}**" for r in allowed[:8]) or "_none allowed yet_"
    embed.add_field(
        name=f"Allowed games ({len(allowed)})",
        value=allowed_names,
        inline=False,
    )

    detect_lines: list[str] = []
    for group in groups[:6]:
        flag = "allowed" if group.allowed else ("blocked" if group.blocked else "not allowed")
        detect_lines.append(
            f"**{group.game_name}** — {group.count} people ({flag})"
        )
    embed.add_field(
        name="Recent overlap",
        value="\n".join(detect_lines) if detect_lines else "_no shared games in the look-back window_",
        inline=False,
    )
    if active:
        lines = []
        for row in active:
            when = parse_iso(row["proposed_at"])
            stamp = format_play_when(when.astimezone(_tz())) if when else "?"
            n = len(bot.db.list_play_rsvps(int(row["id"]), status="in"))
            lines.append(f"**{row['game_name']}** · {stamp} · {n} in · `{row['status']}`")
        embed.add_field(name="Open sessions", value="\n".join(lines), inline=False)
    if not bot.intents.presences:
        embed.set_footer(text="Not watching games — Presence Intent is off")
    else:
        embed.set_footer(text="Open Activity to see everyone the bot has noticed")
    return embed


class PlaySettingsModal(discord.ui.Modal, title="Play together settings"):
    decay = discord.ui.TextInput(
        label="Activity relevant for (days)",
        max_length=3,
        required=True,
        placeholder="30",
    )
    detect = discord.ui.TextInput(
        label="Look back for overlap (days)",
        max_length=3,
        required=True,
        placeholder="14",
    )
    min_people = discord.ui.TextInput(
        label="Auto-suggest when this many people",
        max_length=2,
        required=True,
        placeholder="4",
    )
    hour = discord.ui.TextInput(
        label="Saturday session hour (0–23)",
        max_length=2,
        required=True,
        placeholder="19",
    )
    party = discord.ui.TextInput(
        label="Default party size (min-max)",
        max_length=7,
        required=True,
        placeholder="3-6",
    )

    def __init__(self, hub) -> None:
        super().__init__()
        self.hub = hub
        s = hub.bot.db.get_play_settings(hub.guild_id)
        self.decay.default = str(s["play_decay_days"])
        self.detect.default = str(s["play_detect_days"])
        self.min_people.default = str(s["play_detect_min_people"])
        self.hour.default = str(s["play_default_hour"])
        self.party.default = (
            f"{s['play_default_min_players']}-{s['play_default_max_players']}"
        )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        db = self.hub.bot.db
        gid = self.hub.guild_id

        def _n(raw: str, lo: int, hi: int, fallback: int) -> int:
            try:
                return max(lo, min(hi, int(str(raw).strip())))
            except (TypeError, ValueError):
                return fallback

        db.set_play_setting(gid, "play_decay_days", _n(str(self.decay), 3, 180, 30))
        db.set_play_setting(gid, "play_detect_days", _n(str(self.detect), 2, 90, 14))
        db.set_play_setting(
            gid, "play_detect_min_people", _n(str(self.min_people), 2, 20, 4)
        )
        db.set_play_setting(gid, "play_default_hour", _n(str(self.hour), 0, 23, 19))
        s = db.get_play_settings(gid)
        lo, hi = parse_party_size(
            str(self.party),
            int(s["play_default_min_players"] or 3),
            int(s["play_default_max_players"] or 6),
        )
        db.set_play_setting(gid, "play_default_min_players", lo)
        db.set_play_setting(gid, "play_default_max_players", hi)
        self.hub.page = "play"
        self.hub._rebuild()
        await interaction.response.edit_message(
            embed=hub_play_embed(interaction.guild, self.hub.bot),
            view=self.hub,
        )


class CreatePlayModal(discord.ui.Modal, title="Create a play suggestion"):
    game = discord.ui.TextInput(
        label="Game",
        max_length=80,
        required=True,
        placeholder="The Big Walk",
    )
    when = discord.ui.TextInput(
        label="When (empty = next Saturday 19:00)",
        max_length=22,
        required=False,
        placeholder="15.08.2026 19:00",
    )
    party = discord.ui.TextInput(
        label="Party size",
        max_length=7,
        required=False,
        placeholder="3-6",
    )
    steam = discord.ui.TextInput(
        label="Store / page URL (optional)",
        max_length=200,
        required=False,
        placeholder="Leave empty unless you already have a real page",
    )
    note = discord.ui.TextInput(
        label="Note / price (optional)",
        max_length=120,
        required=False,
        placeholder="Currently 300 UAH",
    )

    def __init__(self, hub, *, game_name: str = "") -> None:
        super().__init__()
        self.hub = hub
        if game_name:
            self.game.default = game_name[:80]
        s = hub.bot.db.get_play_settings(hub.guild_id)
        self.party.default = (
            f"{s['play_default_min_players']}-{s['play_default_max_players']}"
        )
        hour = int(s["play_default_hour"] or 19)
        guessed = next_saturday_evening(_now_local(), hour=hour)
        self.when.placeholder = guessed.strftime("%d.%m.%Y %H:%M")

    async def on_submit(self, interaction: discord.Interaction) -> None:
        cog = self.hub.bot.get_cog("PlayTogetherCog")
        if cog is None or interaction.guild is None:
            await interaction.response.send_message("Play together isn't loaded.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        s = self.hub.bot.db.get_play_settings(self.hub.guild_id)
        hour = int(s["play_default_hour"] or 19)
        when_dt = parse_play_when(
            str(self.when.value or ""), _now_local(), hour=hour
        )
        if when_dt is None:
            when_dt = next_saturday_evening(_now_local(), hour=hour)
        elif when_dt.tzinfo is None:
            when_dt = when_dt.replace(tzinfo=_tz())
        lo, hi = parse_party_size(
            str(self.party.value or ""),
            int(s["play_default_min_players"] or 3),
            int(s["play_default_max_players"] or 6),
        )
        name = str(self.game.value).strip()
        key = game_key(name)
        game = self.hub.bot.db.get_play_game(self.hub.guild_id, key)
        if game is None:
            hits = await search_catalog_games(name)
            exact = [h for h in hits if game_key(h.name) == key]
            if not exact:
                await interaction.followup.send(
                    f"**{name}** is not in the catalog, and search did not find "
                    f"that exact title. Use **Games → Add game**, pick the real "
                    f"result, then create the session.",
                    ephemeral=True,
                )
                return
            hit = exact[0]
            name = hit.name
            key = game_key(name)
            self.hub.bot.db.upsert_play_game(
                self.hub.guild_id,
                key,
                name,
                steam_url=hit.url,
                set_steam=True,
            )
            game = self.hub.bot.db.get_play_game(self.hub.guild_id, key)
        steam = str(self.steam.value or "").strip() or (
            game["steam_url"] if game else None
        )
        note = str(self.note.value or "").strip() or (
            game["store_note"] if game else None
        )
        if game:
            if game["min_players"] and not str(self.party.value or "").strip():
                lo = int(game["min_players"])
            if game["max_players"] and not str(self.party.value or "").strip():
                hi = int(game["max_players"])
            name = str(game["game_name"] or name)
        self.hub.bot.db.upsert_play_game(self.hub.guild_id, key, name)
        try:
            row = await cog.create_and_publish(
                interaction.guild,
                game_key=key,
                game_name=name,
                proposed_at=when_dt,
                min_players=lo,
                max_players=hi,
                steam_url=steam,
                store_note=note,
                created_by=interaction.user.id,
            )
        except PlayPublishError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return
        self.hub.page = "play"
        self.hub._rebuild()
        if interaction.message is not None:
            await interaction.message.edit(
                embed=hub_play_embed(interaction.guild, self.hub.bot),
                view=self.hub,
            )
        await interaction.followup.send(
            f"Posted **{row['game_name']}**.", ephemeral=True
        )


class EditPlayModal(discord.ui.Modal, title="Edit session"):
    when = discord.ui.TextInput(
        label="When",
        max_length=22,
        required=True,
        placeholder="15.08.2026 19:00",
    )
    party = discord.ui.TextInput(
        label="Party size",
        max_length=7,
        required=True,
        placeholder="3-6",
    )
    steam = discord.ui.TextInput(
        label="Steam URL",
        max_length=200,
        required=False,
    )
    note = discord.ui.TextInput(
        label="Note / price",
        max_length=120,
        required=False,
    )

    def __init__(self, hub, row) -> None:
        super().__init__()
        self.hub = hub
        self.suggestion_id = int(row["id"])
        when = parse_iso(row["proposed_at"])
        if when:
            self.when.default = when.astimezone(_tz()).strftime("%d.%m.%Y %H:%M")
        self.party.default = f"{row['min_players']}-{row['max_players']}"
        self.steam.default = (row["steam_url"] or "")[:200]
        self.note.default = (row["store_note"] or "")[:120]

    async def on_submit(self, interaction: discord.Interaction) -> None:
        cog = self.hub.bot.get_cog("PlayTogetherCog")
        when_dt = parse_play_when(str(self.when.value), _now_local())
        if when_dt is None:
            await interaction.response.send_message(
                "Could not parse that date. Use 15.08.2026 19:00.",
                ephemeral=True,
            )
            return
        lo, hi = parse_party_size(str(self.party.value), 3, 6)
        self.hub.bot.db.update_play_suggestion(
            self.suggestion_id,
            proposed_at=when_dt.astimezone(timezone.utc).isoformat(),
            min_players=lo,
            max_players=hi,
            steam_url=str(self.steam.value or "").strip() or None,
            store_note=str(self.note.value or "").strip() or None,
        )
        if cog is not None:
            await cog.after_rsvp_change(self.suggestion_id)
            await cog.sync_discord_event(self.suggestion_id)
        self.hub.page = "play_manage"
        self.hub.play_suggestion_id = self.suggestion_id
        self.hub._rebuild()
        await interaction.response.edit_message(
            embed=manage_embed(interaction.guild, self.hub.bot, self.suggestion_id),
            view=self.hub,
        )


class EditGameModal(discord.ui.Modal, title="Edit game"):
    display = discord.ui.TextInput(label="Display name", max_length=80, required=True)
    steam = discord.ui.TextInput(
        label="Store / page URL",
        max_length=200,
        required=False,
        placeholder="Use Add game to search instead of guessing",
    )
    note = discord.ui.TextInput(label="Note / price", max_length=120, required=False)
    party = discord.ui.TextInput(
        label="Party size for this game",
        max_length=7,
        required=False,
        placeholder="3-6",
    )

    def __init__(self, hub, game) -> None:
        super().__init__()
        self.hub = hub
        self.game_key = str(game["game_key"])
        self.display.default = str(game["game_name"])[:80]
        self.steam.default = (game["steam_url"] or "")[:200]
        self.note.default = (game["store_note"] or "")[:120]
        if game["min_players"] and game["max_players"]:
            self.party.default = f"{game['min_players']}-{game['max_players']}"

    async def on_submit(self, interaction: discord.Interaction) -> None:
        name = str(self.display.value).strip() or self.game_key
        party_raw = str(self.party.value or "").strip()
        kwargs = dict(
            steam_url=str(self.steam.value or "").strip() or None,
            store_note=str(self.note.value or "").strip() or None,
            set_steam=True,
            set_note=True,
        )
        if party_raw:
            lo, hi = parse_party_size(party_raw, 3, 6)
            kwargs.update(min_players=lo, max_players=hi, set_min=True, set_max=True)
        self.hub.bot.db.upsert_play_game(
            self.hub.guild_id, self.game_key, name, **kwargs
        )
        self.hub.page = "play_games"
        self.hub.play_game_key = self.game_key
        self.hub._rebuild()
        await interaction.response.edit_message(
            embed=games_embed(
                interaction.guild, self.hub.bot, selected=self.game_key
            ),
            view=self.hub,
        )


class SearchGameModal(discord.ui.Modal, title="Search a real game"):
    query = discord.ui.TextInput(
        label="Game name",
        max_length=80,
        required=True,
        placeholder="Minecraft",
    )

    def __init__(self, hub, *, default: str = "") -> None:
        super().__init__()
        self.hub = hub
        if default:
            self.query.default = default[:80]

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not await self.hub._admin_ok(interaction):
            return
        q = str(self.query.value).strip()
        await interaction.response.defer(ephemeral=True)
        hits = await search_catalog_games(q)
        if not hits:
            await interaction.followup.send(
                f"No real games matched **{q}**. Try the full title "
                f"(Steam or Wikipedia).",
                ephemeral=True,
            )
            return
        self.hub.play_search_query = q
        self.hub.play_search_hits = [h.as_dict() for h in hits]
        self.hub.page = "play_game_search"
        self.hub._rebuild()
        if interaction.message is not None:
            await interaction.message.edit(
                embed=search_results_embed(q, hits),
                view=self.hub,
            )
        await interaction.followup.send(
            f"**{len(hits)}** match(es). Pick the right one — nothing is added until you do.",
            ephemeral=True,
        )


def search_results_embed(query: str, hits: list[GameHit]) -> discord.Embed:
    embed = discord.Embed(
        title="Play together · pick a game",
        description=(
            f"Results for **{query}** from Steam and Wikipedia. "
            "Pick the real title — this will not add a made-up name."
        ),
        color=ACCENT,
    )
    lines: list[str] = []
    for i, hit in enumerate(hits[:12], start=1):
        src = "Steam" if hit.source == "steam" else "Wikipedia"
        extra = f" — {hit.snippet}" if hit.snippet and hit.source == "wikipedia" else ""
        lines.append(f"**{i}. {hit.name}** ({src}){extra}")
    embed.add_field(name="Matches", value="\n".join(lines)[:1024], inline=False)
    return embed


def hub_search_embed(hub) -> discord.Embed:
    query = str(getattr(hub, "play_search_query", "") or "")
    raw = getattr(hub, "play_search_hits", None) or []
    hits = [hit_from_dict(item) for item in raw if isinstance(item, dict)]
    return search_results_embed(query, hits)


def activity_embed(guild: discord.Guild, bot) -> discord.Embed:
    days, since = _lookback_since(guild.id, bot)
    live = live_playing(guild)
    rows = bot.db.list_play_activity_recent(guild.id, since, limit=25)
    embed = discord.Embed(
        title="Play together · activity",
        description=(
            "What Discord is sending the bot **right now**, and what it has "
            "stored recently. Review only lists games **two or more** people share."
        ),
        color=ACCENT if bot.intents.presences else MUTED,
    )
    watch_name, watch_value = tracking_field(guild, bot)
    embed.add_field(name=watch_name, value=watch_value, inline=False)

    if live:
        live_lines = [
            f"{person_label(bot, guild, member.id)} — **{game}**"
            for member, game in live[:15]
        ]
        if len(live) > 15:
            live_lines.append(f"_+{len(live) - 15} more_")
        embed.add_field(
            name=f"Playing right now ({len(live)})",
            value="\n".join(live_lines)[:1024],
            inline=False,
        )
    else:
        embed.add_field(
            name="Playing right now",
            value=(
                "_nothing visible — if people are in a game, Presence Intent "
                "is still off or Discord has not sent an update yet._"
                if not bot.intents.presences
                else "_nobody with a Playing status right now_"
            ),
            inline=False,
        )

    if rows:
        hist = []
        for row in rows[:18]:
            last = parse_iso(row["last_seen"])
            ago = (
                format_days_ago(
                    (_now_utc() - last).total_seconds() / 86400
                )
                if last
                else "?"
            )
            hist.append(
                f"{person_label(bot, guild, int(row['user_id']))} — "
                f"**{row['game_name']}** · {ago}"
            )
        embed.add_field(
            name=f"Stored ({days}d)",
            value="\n".join(hist)[:1024],
            inline=False,
        )
    else:
        embed.add_field(
            name=f"Stored ({days}d)",
            value="_empty — the bot has not recorded any Playing activity yet_",
            inline=False,
        )
    return embed


def games_embed(
    guild: discord.Guild, bot, *, selected: str | None = None
) -> discord.Embed:
    games = bot.db.list_known_play_games(guild.id, limit=25)
    embed = discord.Embed(
        title="Play together · games",
        description=(
            "Only **allowed** games can become automatic suggestions. "
            "**Blocked** games are recorded but never proposed.\n"
            "**Add game** searches Steam and Wikipedia — you pick a real title."
        ),
        color=ACCENT,
    )
    if not games:
        embed.add_field(
            name="None yet",
            value="Search with **Add game** and pick a real Steam / Wikipedia result.",
            inline=False,
        )
        return embed
    lines: list[str] = []
    for game in games:
        if game["blocked"]:
            state = "blocked"
        elif game["enabled"]:
            state = "allowed"
        else:
            state = "off"
        mark = "→ " if selected and game["game_key"] == selected else ""
        extra = ""
        if game["min_players"]:
            extra = f" · {game['min_players']}-{game['max_players'] or '?'}"
        lines.append(f"{mark}**{game['game_name']}** — {state}{extra}")
    embed.add_field(name="Known games", value="\n".join(lines)[:1024], inline=False)
    if selected:
        game = bot.db.get_play_game(guild.id, selected)
        if game:
            people = bot.db.list_play_activity_for_game(
                guild.id,
                selected,
                (_now_utc() - timedelta(days=30)).isoformat(),
            )
            who = ", ".join(
                f"{person_label(bot, guild, int(p['user_id']))} "
                f"({format_days_ago((_now_utc() - (parse_iso(p['last_seen']) or _now_utc())).total_seconds() / 86400)})"
                for p in people[:8]
            ) or "_nobody recently_"
            link = store_link_markdown(game["steam_url"]) or "_no store page yet — Add game to search_"
            embed.add_field(
                name=str(game["game_name"]),
                value=(
                    f"{who}\n"
                    f"{link}\n"
                    f"{game['store_note'] or ''}"
                )[:1024],
                inline=False,
            )
    return embed


def review_embed(guild: discord.Guild, bot) -> discord.Embed:
    groups = detect_groups(bot.db, guild.id)
    active = bot.db.list_play_suggestions(guild.id, statuses=ACTIVE_STATUSES, limit=10)
    embed = discord.Embed(
        title="Play together · review",
        description="Pick an overlap to turn into a session, or manage one that's already up.",
        color=ACCENT,
    )
    if groups:
        lines = []
        for group in groups[:10]:
            flag = "allowed" if group.allowed else ("blocked" if group.blocked else "off")
            names = join_names(
                [person_label(bot, guild, uid) for uid, _w, _t in group.people[:5]]
            )
            lines.append(
                f"**{group.game_name}** · {group.count} · {flag}\n{names}"
            )
        embed.add_field(name="Overlap", value="\n".join(lines)[:1024], inline=False)
    else:
        hint = (
            "_none yet — overlap means 2+ people with the same game. "
            "One person playing still shows up under Activity._"
        )
        if not bot.intents.presences:
            hint = "_none — the bot is not watching games (Presence Intent off)_"
        embed.add_field(name="Overlap", value=hint, inline=False)
    if active:
        lines = []
        for row in active:
            n = len(bot.db.list_play_rsvps(int(row["id"]), status="in"))
            lines.append(f"**{row['game_name']}** · {n} in · `{row['status']}`")
        embed.add_field(name="Open", value="\n".join(lines), inline=False)
    return embed


def manage_embed(guild: discord.Guild, bot, suggestion_id: int) -> discord.Embed:
    row = bot.db.get_play_suggestion(suggestion_id)
    if row is None:
        return discord.Embed(title="Session gone", color=MUTED)
    embed = suggestion_embed(bot, guild, row)
    ins = bot.db.list_play_rsvps(suggestion_id, status="in")
    nopes = bot.db.list_play_rsvps(suggestion_id, status="nope")
    in_lines = []
    for r in ins:
        src = r["source"]
        in_lines.append(
            f"{person_label(bot, guild, int(r['user_id']))} · {src}"
        )
    embed.add_field(
        name="Confirmed",
        value="\n".join(in_lines) if in_lines else "_none_",
        inline=False,
    )
    if nopes:
        embed.add_field(
            name="Nope",
            value=", ".join(
                person_label(bot, guild, int(r["user_id"])) for r in nopes[:12]
            ),
            inline=False,
        )
    embed.add_field(
        name="Admin",
        value=(
            f"Auto Discord event: **{'on' if row['auto_event'] else 'off'}**\n"
            f"Expansion invites sent: **{row['expansion_sent']}**"
        ),
        inline=False,
    )
    return embed


def add_play_hub_controls(hub) -> None:
    page = getattr(hub, "page", "play")
    if page == "play_games":
        _add_games_controls(hub)
        return
    if page == "play_game_search":
        _add_search_controls(hub)
        return
    if page == "play_activity":
        _add_activity_controls(hub)
        return
    if page == "play_review":
        _add_review_controls(hub)
        return
    if page == "play_manage":
        _add_manage_controls(hub)
        return
    _add_main_controls(hub)


def _add_main_controls(hub) -> None:
    suggest = discord.ui.ChannelSelect(
        placeholder="Set suggestion channel…",
        channel_types=[discord.ChannelType.text],
        min_values=1,
        max_values=1,
        row=1,
    )
    voice = discord.ui.ChannelSelect(
        placeholder="Set event voice channel…",
        channel_types=[discord.ChannelType.voice],
        min_values=1,
        max_values=1,
        row=2,
    )

    async def on_suggest(interaction: discord.Interaction) -> None:
        if not await hub._admin_ok(interaction):
            return
        channel_id = int(suggest.values[0].id)
        hub.bot.db.set_play_setting(hub.guild_id, "play_suggest_channel_id", channel_id)
        hub._rebuild()
        await interaction.response.edit_message(
            embed=hub_play_embed(interaction.guild, hub.bot), view=hub
        )

    async def on_voice(interaction: discord.Interaction) -> None:
        if not await hub._admin_ok(interaction):
            return
        channel_id = int(voice.values[0].id)
        hub.bot.db.set_play_setting(hub.guild_id, "play_voice_channel_id", channel_id)
        hub._rebuild()
        await interaction.response.edit_message(
            embed=hub_play_embed(interaction.guild, hub.bot), view=hub
        )

    suggest.callback = on_suggest
    voice.callback = on_voice
    hub.add_item(suggest)
    hub.add_item(voice)

    s = hub.bot.db.get_play_settings(hub.guild_id)

    async def toggle(interaction: discord.Interaction, column: str) -> None:
        if not await hub._admin_ok(interaction):
            return
        cur = hub.bot.db.get_play_settings(hub.guild_id)
        hub.bot.db.set_play_setting(
            hub.guild_id, column, 0 if cur[column] else 1
        )
        hub._rebuild()
        await interaction.response.edit_message(
            embed=hub_play_embed(interaction.guild, hub.bot), view=hub
        )

    auto_btn = discord.ui.Button(
        label="Auto on" if s["play_auto_enabled"] else "Auto off",
        style=discord.ButtonStyle.success if s["play_auto_enabled"] else discord.ButtonStyle.secondary,
        row=3,
    )
    event_btn = discord.ui.Button(
        label="Events on" if s["play_auto_event"] else "Events off",
        style=discord.ButtonStyle.success if s["play_auto_event"] else discord.ButtonStyle.secondary,
        row=3,
    )
    expand_btn = discord.ui.Button(
        label="Invites on" if s["play_auto_expand"] else "Invites off",
        style=discord.ButtonStyle.success if s["play_auto_expand"] else discord.ButtonStyle.secondary,
        row=3,
    )
    settings_btn = discord.ui.Button(
        label="Settings", style=discord.ButtonStyle.primary, row=3
    )

    async def on_auto(i: discord.Interaction) -> None:
        await toggle(i, "play_auto_enabled")

    async def on_event(i: discord.Interaction) -> None:
        await toggle(i, "play_auto_event")

    async def on_expand(i: discord.Interaction) -> None:
        await toggle(i, "play_auto_expand")

    async def on_settings(i: discord.Interaction) -> None:
        if not await hub._admin_ok(i):
            return
        await i.response.send_modal(PlaySettingsModal(hub))

    auto_btn.callback = on_auto
    event_btn.callback = on_event
    expand_btn.callback = on_expand
    settings_btn.callback = on_settings
    hub.add_item(auto_btn)
    hub.add_item(event_btn)
    hub.add_item(expand_btn)
    hub.add_item(settings_btn)

    games_btn = discord.ui.Button(label="Games", style=discord.ButtonStyle.primary, row=4)
    create_btn = discord.ui.Button(label="Create", style=discord.ButtonStyle.primary, row=4)
    review_btn = discord.ui.Button(label="Review", style=discord.ButtonStyle.primary, row=4)
    activity_btn = discord.ui.Button(
        label="Activity", style=discord.ButtonStyle.primary, row=4
    )

    async def on_games(i: discord.Interaction) -> None:
        if not await hub._admin_ok(i):
            return
        hub.page = "play_games"
        hub.play_game_key = None
        hub._rebuild()
        await i.response.edit_message(
            embed=games_embed(i.guild, hub.bot), view=hub
        )

    async def on_create(i: discord.Interaction) -> None:
        if not await hub._admin_ok(i):
            return
        await i.response.send_modal(CreatePlayModal(hub))

    async def on_review(i: discord.Interaction) -> None:
        if not await hub._admin_ok(i):
            return
        hub.page = "play_review"
        hub._rebuild()
        await i.response.edit_message(
            embed=review_embed(i.guild, hub.bot), view=hub
        )

    async def on_activity(i: discord.Interaction) -> None:
        if not await hub._admin_ok(i) or i.guild is None:
            return
        snapshot_guild_games(hub.bot, i.guild)
        hub.page = "play_activity"
        hub._rebuild()
        await i.response.edit_message(
            embed=activity_embed(i.guild, hub.bot), view=hub
        )

    games_btn.callback = on_games
    create_btn.callback = on_create
    review_btn.callback = on_review
    activity_btn.callback = on_activity
    hub.add_item(games_btn)
    hub.add_item(create_btn)
    hub.add_item(review_btn)
    hub.add_item(activity_btn)


def _back_to_play(hub) -> discord.ui.Button:
    btn = discord.ui.Button(label="Back", style=discord.ButtonStyle.secondary, row=4)

    async def go(i: discord.Interaction) -> None:
        if not await hub._admin_ok(i):
            return
        hub.page = "play"
        hub._rebuild()
        await i.response.edit_message(
            embed=hub_play_embed(i.guild, hub.bot), view=hub
        )

    btn.callback = go
    return btn


def _add_activity_controls(hub) -> None:
    scan = discord.ui.Button(
        label="Scan now", style=discord.ButtonStyle.primary, row=1
    )

    async def on_scan(i: discord.Interaction) -> None:
        if not await hub._admin_ok(i) or i.guild is None:
            return
        if not hub.bot.intents.presences:
            await i.response.send_message(
                "The bot is not watching games. Enable **Presence Intent** in "
                "the Developer Portal, set `PLAY_PRESENCE_INTENT=1` on the host, "
                "then restart.",
                ephemeral=True,
            )
            return
        seen = snapshot_guild_games(hub.bot, i.guild)
        hub.page = "play_activity"
        hub._rebuild()
        await i.response.edit_message(
            embed=activity_embed(i.guild, hub.bot), view=hub
        )
        await i.followup.send(
            f"Recorded Playing status for **{seen}** member(s).",
            ephemeral=True,
        )

    scan.callback = on_scan
    hub.add_item(scan)
    hub.add_item(_back_to_play(hub))


def _add_games_controls(hub) -> None:
    games = hub.bot.db.list_known_play_games(hub.guild_id, limit=25)
    options = [
        discord.SelectOption(
            label=str(g["game_name"])[:100],
            value=str(g["game_key"])[:100],
            description=(
                "blocked"
                if g["blocked"]
                else ("allowed" if g["enabled"] else "off")
            ),
        )
        for g in games
    ]
    if options:
        select = discord.ui.Select(
            placeholder="Pick a game…", options=options, min_values=1, max_values=1, row=1
        )

        async def on_pick(i: discord.Interaction) -> None:
            if not await hub._admin_ok(i):
                return
            hub.play_game_key = select.values[0]
            hub.page = "play_games"
            hub._rebuild()
            await i.response.edit_message(
                embed=games_embed(i.guild, hub.bot, selected=hub.play_game_key),
                view=hub,
            )

        select.callback = on_pick
        hub.add_item(select)

    key = getattr(hub, "play_game_key", None)

    async def set_state(i: discord.Interaction, *, enabled: int | None, blocked: int | None) -> None:
        if not await hub._admin_ok(i):
            return
        if not key:
            await i.response.send_message("Pick a game first.", ephemeral=True)
            return
        game = hub.bot.db.get_play_game(hub.guild_id, key)
        name = str(game["game_name"]) if game else key
        hub.bot.db.upsert_play_game(
            hub.guild_id, key, name, enabled=enabled, blocked=blocked
        )
        hub._rebuild()
        await i.response.edit_message(
            embed=games_embed(i.guild, hub.bot, selected=key), view=hub
        )

    allow = discord.ui.Button(label="Allow", style=discord.ButtonStyle.success, row=2)
    off = discord.ui.Button(label="Don't auto", style=discord.ButtonStyle.secondary, row=2)
    block = discord.ui.Button(label="Block", style=discord.ButtonStyle.danger, row=2)
    edit = discord.ui.Button(label="Edit details", style=discord.ButtonStyle.primary, row=3)
    add = discord.ui.Button(label="Add game", style=discord.ButtonStyle.primary, row=3)

    async def on_allow(i: discord.Interaction) -> None:
        await set_state(i, enabled=1, blocked=0)

    async def on_off(i: discord.Interaction) -> None:
        await set_state(i, enabled=0, blocked=0)

    async def on_block(i: discord.Interaction) -> None:
        await set_state(i, enabled=0, blocked=1)

    async def on_edit(i: discord.Interaction) -> None:
        if not await hub._admin_ok(i):
            return
        if not key:
            await i.response.send_message("Pick a game first.", ephemeral=True)
            return
        game = hub.bot.db.get_play_game(hub.guild_id, key)
        if game is None:
            await i.response.send_message("Unknown game.", ephemeral=True)
            return
        await i.response.send_modal(EditGameModal(hub, game))

    async def on_add(i: discord.Interaction) -> None:
        if not await hub._admin_ok(i):
            return
        default = ""
        if key:
            game = hub.bot.db.get_play_game(hub.guild_id, key)
            if game:
                default = str(game["game_name"])
        await i.response.send_modal(SearchGameModal(hub, default=default))

    allow.callback = on_allow
    off.callback = on_off
    block.callback = on_block
    edit.callback = on_edit
    add.callback = on_add
    hub.add_item(allow)
    hub.add_item(off)
    hub.add_item(block)
    hub.add_item(edit)
    hub.add_item(add)
    hub.add_item(_back_to_play(hub))


def _add_search_controls(hub) -> None:
    raw_hits = getattr(hub, "play_search_hits", None) or []
    hits = [hit_from_dict(x) for x in raw_hits if isinstance(x, dict)]
    if hits:
        options = []
        for i, hit in enumerate(hits[:25]):
            src = "Steam" if hit.source == "steam" else "Wikipedia"
            options.append(
                discord.SelectOption(
                    label=hit.name[:100],
                    value=str(i),
                    description=f"{src} · {(hit.snippet or src)[:80]}",
                )
            )
        select = discord.ui.Select(
            placeholder="Pick the real game…",
            options=options,
            min_values=1,
            max_values=1,
            row=1,
        )

        async def on_pick(i: discord.Interaction) -> None:
            if not await hub._admin_ok(i):
                return
            try:
                idx = int(select.values[0])
                hit = hits[idx]
            except (ValueError, IndexError):
                await i.response.send_message("That result expired. Search again.", ephemeral=True)
                return
            key = game_key(hit.name)
            existing = hub.bot.db.get_play_game(hub.guild_id, key)
            hub.bot.db.upsert_play_game(
                hub.guild_id,
                key,
                hit.name,
                enabled=None if existing else 1,
                steam_url=hit.url,
                set_steam=True,
            )
            hub.play_game_key = key
            hub.page = "play_games"
            hub._rebuild()
            await i.response.edit_message(
                embed=games_embed(i.guild, hub.bot, selected=key),
                view=hub,
            )
            await i.followup.send(
                f"Added **{hit.name}** from {hit.source}. "
                + ("Allow it for auto-suggestions if you want." if existing else "It's allowed for suggestions."),
                ephemeral=True,
            )

        select.callback = on_pick
        hub.add_item(select)

    again = discord.ui.Button(label="Search again", style=discord.ButtonStyle.primary, row=2)

    async def on_again(i: discord.Interaction) -> None:
        if not await hub._admin_ok(i):
            return
        default = str(getattr(hub, "play_search_query", "") or "")
        await i.response.send_modal(SearchGameModal(hub, default=default))

    async def go_games(i: discord.Interaction) -> None:
        if not await hub._admin_ok(i):
            return
        hub.page = "play_games"
        hub._rebuild()
        await i.response.edit_message(
            embed=games_embed(i.guild, hub.bot, selected=getattr(hub, "play_game_key", None)),
            view=hub,
        )

    back = discord.ui.Button(label="Back", style=discord.ButtonStyle.secondary, row=2)
    again.callback = on_again
    back.callback = go_games
    hub.add_item(again)
    hub.add_item(back)


def _add_review_controls(hub) -> None:
    groups = detect_groups(hub.bot.db, hub.guild_id)
    active = hub.bot.db.list_play_suggestions(
        hub.guild_id, statuses=ACTIVE_STATUSES, limit=10
    )
    options: list[discord.SelectOption] = []
    for group in groups[:15]:
        options.append(
            discord.SelectOption(
                label=f"{group.game_name} · {group.count} people"[:100],
                value=f"g:{group.game_key}"[:100],
                description="allowed" if group.allowed else "manual only",
            )
        )
    for row in active:
        options.append(
            discord.SelectOption(
                label=f"Open: {row['game_name']}"[:100],
                value=f"s:{row['id']}",
                description=str(row["status"]),
            )
        )
    if options:
        select = discord.ui.Select(
            placeholder="Pick overlap or an open session…",
            options=options[:25],
            min_values=1,
            max_values=1,
            row=1,
        )

        async def on_pick(i: discord.Interaction) -> None:
            if not await hub._admin_ok(i):
                return
            hub.play_review_pick = select.values[0]
            await i.response.defer()
            await i.edit_original_response(
                embed=review_embed(i.guild, hub.bot), view=hub
            )

        select.callback = on_pick
        hub.add_item(select)

    create_btn = discord.ui.Button(
        label="Suggest this game", style=discord.ButtonStyle.success, row=2
    )
    manage_btn = discord.ui.Button(
        label="Manage session", style=discord.ButtonStyle.primary, row=2
    )

    async def on_create(i: discord.Interaction) -> None:
        if not await hub._admin_ok(i):
            return
        pick = getattr(hub, "play_review_pick", None) or ""
        if not pick.startswith("g:"):
            await i.response.send_message(
                "Pick an overlap (not an open session) first.", ephemeral=True
            )
            return
        key = pick[2:]
        game = hub.bot.db.get_play_game(hub.guild_id, key)
        name = str(game["game_name"]) if game else key
        await i.response.send_modal(CreatePlayModal(hub, game_name=name))

    async def on_manage(i: discord.Interaction) -> None:
        if not await hub._admin_ok(i):
            return
        pick = getattr(hub, "play_review_pick", None) or ""
        if not pick.startswith("s:"):
            await i.response.send_message("Pick an open session first.", ephemeral=True)
            return
        hub.play_suggestion_id = int(pick[2:])
        hub.page = "play_manage"
        hub._rebuild()
        await i.response.edit_message(
            embed=manage_embed(i.guild, hub.bot, hub.play_suggestion_id),
            view=hub,
        )

    create_btn.callback = on_create
    manage_btn.callback = on_manage
    hub.add_item(create_btn)
    hub.add_item(manage_btn)
    hub.add_item(_back_to_play(hub))


def _add_manage_controls(hub) -> None:
    sid = getattr(hub, "play_suggestion_id", None)
    add = discord.ui.UserSelect(
        placeholder="Add / confirm someone…", min_values=1, max_values=1, row=1
    )
    remove = discord.ui.UserSelect(
        placeholder="Remove someone…", min_values=1, max_values=1, row=2
    )

    async def on_add(i: discord.Interaction) -> None:
        if not await hub._admin_ok(i) or not sid:
            return
        user = add.values[0]
        if getattr(user, "bot", False):
            await i.response.send_message("That's a bot.", ephemeral=True)
            return
        hub.bot.db.set_play_rsvp(
            sid, user.id, "in", "admin", _now_utc().isoformat()
        )
        cog = hub.bot.get_cog("PlayTogetherCog")
        if cog is not None:
            await cog.after_rsvp_change(sid)
        hub._rebuild()
        await i.response.edit_message(
            embed=manage_embed(i.guild, hub.bot, sid), view=hub
        )

    async def on_remove(i: discord.Interaction) -> None:
        if not await hub._admin_ok(i) or not sid:
            return
        user = remove.values[0]
        hub.bot.db.set_play_rsvp(
            sid, user.id, "nope", "admin", _now_utc().isoformat()
        )
        cog = hub.bot.get_cog("PlayTogetherCog")
        if cog is not None:
            await cog.after_rsvp_change(sid)
        hub._rebuild()
        await i.response.edit_message(
            embed=manage_embed(i.guild, hub.bot, sid), view=hub
        )

    add.callback = on_add
    remove.callback = on_remove
    hub.add_item(add)
    hub.add_item(remove)

    edit = discord.ui.Button(label="Edit", style=discord.ButtonStyle.primary, row=3)
    event_btn = discord.ui.Button(
        label="Create event", style=discord.ButtonStyle.success, row=3
    )
    cancel = discord.ui.Button(label="Cancel", style=discord.ButtonStyle.danger, row=3)
    invite = discord.ui.Button(
        label="Invite more", style=discord.ButtonStyle.secondary, row=4
    )
    skip_event = discord.ui.Button(
        label="No auto event", style=discord.ButtonStyle.secondary, row=4
    )

    async def on_edit(i: discord.Interaction) -> None:
        if not await hub._admin_ok(i) or not sid:
            return
        row = hub.bot.db.get_play_suggestion(sid)
        if row is None:
            await i.response.send_message("Gone.", ephemeral=True)
            return
        await i.response.send_modal(EditPlayModal(hub, row))

    async def on_event(i: discord.Interaction) -> None:
        if not await hub._admin_ok(i) or not sid:
            return
        await i.response.defer()
        cog = hub.bot.get_cog("PlayTogetherCog")
        if cog is None:
            return
        try:
            await cog.create_discord_event(sid, force=True)
        except PlayPublishError as exc:
            await i.followup.send(str(exc), ephemeral=True)
            return
        hub._rebuild()
        await i.edit_original_response(
            embed=manage_embed(i.guild, hub.bot, sid), view=hub
        )

    async def on_cancel(i: discord.Interaction) -> None:
        if not await hub._admin_ok(i) or not sid:
            return
        await i.response.defer()
        cog = hub.bot.get_cog("PlayTogetherCog")
        if cog is not None:
            await cog.cancel_suggestion(sid)
        hub.page = "play"
        hub._rebuild()
        await i.edit_original_response(
            embed=hub_play_embed(i.guild, hub.bot), view=hub
        )

    async def on_invite(i: discord.Interaction) -> None:
        if not await hub._admin_ok(i) or not sid:
            return
        await i.response.defer()
        cog = hub.bot.get_cog("PlayTogetherCog")
        sent = 0
        if cog is not None:
            sent = await cog.run_social_expansion(sid, force=True)
        hub._rebuild()
        await i.edit_original_response(
            embed=manage_embed(i.guild, hub.bot, sid), view=hub
        )
        await i.followup.send(f"Sent **{sent}** personal invite(s).", ephemeral=True)

    async def on_skip(i: discord.Interaction) -> None:
        if not await hub._admin_ok(i) or not sid:
            return
        row = hub.bot.db.get_play_suggestion(sid)
        if row is None:
            return
        hub.bot.db.update_play_suggestion(
            sid, auto_event=0 if row["auto_event"] else 1
        )
        hub._rebuild()
        await i.response.edit_message(
            embed=manage_embed(i.guild, hub.bot, sid), view=hub
        )

    edit.callback = on_edit
    event_btn.callback = on_event
    cancel.callback = on_cancel
    invite.callback = on_invite
    skip_event.callback = on_skip
    hub.add_item(edit)
    hub.add_item(event_btn)
    hub.add_item(cancel)
    hub.add_item(invite)
    hub.add_item(skip_event)
    hub.add_item(_back_to_play(hub))


class PlayPublishError(RuntimeError):
    pass


class PlayTogetherCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._last_voice_sample = 0.0
        self.sample_voice.start()
        self.scan_presence.start()
        self.detect_loop.start()
        self.lifecycle_loop.start()

    def cog_unload(self) -> None:
        self.sample_voice.cancel()
        self.scan_presence.cancel()
        self.detect_loop.cancel()
        self.lifecycle_loop.cancel()

    def record_member_games(self, member: discord.Member) -> None:
        if member.bot or member.guild is None:
            return
        now = _now_utc().isoformat()
        for name, app_id in iter_playing_names(member):
            key = game_key(name)
            if not key:
                continue
            self.bot.db.upsert_play_activity(
                member.guild.id,
                member.id,
                key,
                name,
                app_id,
                now,
            )

    @commands.Cog.listener()
    async def on_presence_update(
        self, before: discord.Member, after: discord.Member
    ) -> None:
        self.record_member_games(after)

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        if not self.bot.intents.presences:
            log.warning(
                "Play together: Presence Intent is off — game history will stay empty"
            )
            return
        for guild in self.bot.guilds:
            for member in guild.members:
                try:
                    self.record_member_games(member)
                except Exception as exc:
                    log.warning("Activity scan failed for %s: %s", member.id, exc)

    @tasks.loop(minutes=15)
    async def scan_presence(self) -> None:
        if not self.bot.intents.presences:
            return
        for guild in self.bot.guilds:
            for member in guild.members:
                self.record_member_games(member)

    @scan_presence.before_loop
    async def before_scan(self) -> None:
        await self.bot.wait_until_ready()

    @tasks.loop(minutes=config.PLAY_VOICE_SAMPLE_MINUTES)
    async def sample_voice(self) -> None:
        now = _now_utc().isoformat()
        minutes = max(1, int(getattr(config, "PLAY_VOICE_SAMPLE_MINUTES", 5)))
        for guild in self.bot.guilds:
            for channel in guild.voice_channels:
                ids = [
                    m.id
                    for m in channel.members
                    if not m.bot
                ]
                if len(ids) < 2:
                    continue
                for i, a in enumerate(ids):
                    for b in ids[i + 1 :]:
                        self.bot.db.add_voice_pair_minutes(
                            guild.id, a, b, minutes, now
                        )

    @sample_voice.before_loop
    async def before_voice(self) -> None:
        await self.bot.wait_until_ready()

    @tasks.loop(hours=config.PLAY_DETECT_INTERVAL_HOURS)
    async def detect_loop(self) -> None:
        for guild in self.bot.guilds:
            try:
                await self.run_detection(guild)
            except Exception as exc:
                log.warning("Detection failed in %s: %s", guild.name, exc)

    @detect_loop.before_loop
    async def before_detect(self) -> None:
        await self.bot.wait_until_ready()

    @tasks.loop(minutes=10)
    async def lifecycle_loop(self) -> None:
        now = _now_utc()
        remind_until = (
            now + timedelta(minutes=max(5, int(config.PLAY_REMIND_MINUTES)))
        ).isoformat()
        for row in self.bot.db.list_due_play_reminders(now.isoformat(), remind_until):
            try:
                await self.send_reminder(int(row["id"]))
            except Exception as exc:
                log.warning("Reminder failed for %s: %s", row["id"], exc)
        cutoff = (now - timedelta(hours=4)).isoformat()
        for row in self.bot.db.list_play_suggestions_to_complete(cutoff):
            self.bot.db.update_play_suggestion(int(row["id"]), status="completed")

    @lifecycle_loop.before_loop
    async def before_life(self) -> None:
        await self.bot.wait_until_ready()

    async def run_detection(self, guild: discord.Guild) -> int:
        settings = self.bot.db.get_play_settings(guild.id)
        decay_days = max(3, _settings_int(settings, "play_decay_days", 30))
        self.bot.db.purge_old_play_activity(
            guild.id, (_now_utc() - timedelta(days=decay_days * 2)).isoformat()
        )
        if not settings["play_auto_enabled"]:
            return 0
        if not settings["play_suggest_channel_id"]:
            return 0
        min_people = max(2, _settings_int(settings, "play_detect_min_people", 4))
        cooldown = max(1, _settings_int(settings, "play_cooldown_days", 7))
        hour = _settings_int(settings, "play_default_hour", 19)
        posted = 0
        for group in detect_groups(self.bot.db, guild.id):
            if not group.allowed:
                continue
            if not should_auto_suggest(group.count, group.weight_sum, min_people):
                continue
            latest = self.bot.db.latest_play_suggestion_for_game(
                guild.id, group.game_key
            )
            if latest is not None:
                if str(latest["status"]) in ACTIVE_STATUSES:
                    continue
                created = parse_iso(latest["created_at"])
                if created and (_now_utc() - created).days < cooldown:
                    continue
            game = self.bot.db.get_play_game(guild.id, group.game_key)
            lo = int(game["min_players"]) if game and game["min_players"] else int(
                settings["play_default_min_players"] or 3
            )
            hi = int(game["max_players"]) if game and game["max_players"] else int(
                settings["play_default_max_players"] or 6
            )
            steam = (game["steam_url"] if game else None) or None
            note = (game["store_note"] if game else None) or None
            when = next_saturday_evening(_now_local(), hour=hour)
            try:
                await self.create_and_publish(
                    guild,
                    game_key=group.game_key,
                    game_name=group.game_name,
                    proposed_at=when,
                    min_players=lo,
                    max_players=hi,
                    steam_url=steam,
                    store_note=note,
                    created_by=None,
                )
                posted += 1
            except PlayPublishError as exc:
                log.info("Skip auto suggest %s: %s", group.game_key, exc)
        return posted

    async def create_and_publish(
        self,
        guild: discord.Guild,
        *,
        game_key: str,
        game_name: str,
        proposed_at: datetime,
        min_players: int,
        max_players: int,
        steam_url: str | None,
        store_note: str | None,
        created_by: int | None,
    ):
        settings = self.bot.db.get_play_settings(guild.id)
        channel_id = settings["play_suggest_channel_id"]
        if not channel_id:
            raise PlayPublishError("Set a suggestion channel first.")
        channel = guild.get_channel(int(channel_id))
        if not isinstance(channel, discord.TextChannel):
            raise PlayPublishError("Suggestion channel is missing or not a text channel.")
        open_already = self.bot.db.list_play_suggestions(
            guild.id, statuses=ACTIVE_STATUSES, game_key=game_key, limit=1
        )
        if open_already:
            raise PlayPublishError(
                f"There's already an open session for {game_name}."
            )
        if proposed_at.tzinfo is None:
            proposed_at = proposed_at.replace(tzinfo=_tz())
        sid = self.bot.db.create_play_suggestion(
            guild.id,
            game_key=game_key,
            game_name=game_name,
            status="published",
            proposed_at=proposed_at.astimezone(timezone.utc).isoformat(),
            min_players=min_players,
            max_players=max_players,
            steam_url=steam_url,
            store_note=store_note,
            created_by=created_by,
            auto_event=1 if settings["play_auto_event"] else 0,
        )
        row = self.bot.db.get_play_suggestion(sid)
        assert row is not None
        try:
            message = await channel.send(
                embed=suggestion_embed(self.bot, guild, row),
                view=PlayRsvpView(),
            )
        except discord.HTTPException as exc:
            self.bot.db.update_play_suggestion(sid, status="cancelled")
            raise PlayPublishError(f"Could not post: {exc}") from exc
        self.bot.db.update_play_suggestion(
            sid, channel_id=channel.id, message_id=message.id
        )
        return self.bot.db.get_play_suggestion(sid)

    async def refresh_suggestion_message(self, suggestion_id: int) -> None:
        row = self.bot.db.get_play_suggestion(suggestion_id)
        if row is None or not row["channel_id"] or not row["message_id"]:
            return
        guild = self.bot.get_guild(int(row["guild_id"]))
        if guild is None:
            return
        channel = guild.get_channel(int(row["channel_id"]))
        if not isinstance(channel, discord.TextChannel):
            return
        try:
            message = await channel.fetch_message(int(row["message_id"]))
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return
        view = PlayRsvpView() if str(row["status"]) in ACTIVE_STATUSES else None
        try:
            await message.edit(
                embed=suggestion_embed(self.bot, guild, row), view=view
            )
        except discord.HTTPException as exc:
            log.warning("Could not refresh suggestion %s: %s", suggestion_id, exc)

    async def after_rsvp_change(self, suggestion_id: int) -> None:
        await self.refresh_suggestion_message(suggestion_id)
        row = self.bot.db.get_play_suggestion(suggestion_id)
        if row is None or str(row["status"]) not in ACTIVE_STATUSES:
            return
        confirmed = self.bot.db.list_play_rsvps(suggestion_id, status="in")
        min_p = int(row["min_players"])
        settings = self.bot.db.get_play_settings(int(row["guild_id"]))
        if (
            len(confirmed) >= min_p
            and settings["play_auto_expand"]
            and not row["expansion_sent"]
        ):
            await self.run_social_expansion(suggestion_id)
        if (
            len(confirmed) >= min_p
            and row["auto_event"]
            and settings["play_auto_event"]
            and not row["discord_event_id"]
            and str(row["status"]) == "published"
        ):
            try:
                await self.create_discord_event(suggestion_id)
            except PlayPublishError as exc:
                log.info("Auto event skipped: %s", exc)

    async def run_social_expansion(
        self, suggestion_id: int, *, force: bool = False
    ) -> int:
        row = self.bot.db.get_play_suggestion(suggestion_id)
        if row is None:
            return 0
        guild = self.bot.get_guild(int(row["guild_id"]))
        if guild is None:
            return 0
        settings = self.bot.db.get_play_settings(guild.id)
        if not force and not settings["play_auto_expand"]:
            return 0
        confirmed = [
            int(r["user_id"])
            for r in self.bot.db.list_play_rsvps(suggestion_id, status="in")
        ]
        if len(confirmed) < int(row["min_players"]) and not force:
            return 0
        already = {int(r["user_id"]) for r in self.bot.db.list_play_rsvps(suggestion_id)}
        invited = self.bot.db.list_play_expansion_invites(suggestion_id)
        skip = already | invited | set(confirmed)
        detect_days = max(1, _settings_int(settings, "play_detect_days", 14))
        since = (_now_utc() - timedelta(days=detect_days)).isoformat()
        half_life = float(config.PLAY_HALF_LIFE_DAYS)
        now = _now_utc()

        confirmed_games: dict[str, float] = {}
        for uid in confirmed:
            for act in self.bot.db.list_user_play_activity(guild.id, uid, since):
                last = parse_iso(act["last_seen"])
                days = (
                    max(0.0, (now - last).total_seconds() / 86400) if last else detect_days
                )
                w = recency_weight(days, half_life)
                key = str(act["game_key"])
                confirmed_games[key] = max(confirmed_games.get(key, 0.0), w)

        game_key_s = str(row["game_key"])
        players = self.bot.db.list_play_activity_for_game(guild.id, game_key_s, since)
        candidates: list[tuple[float, int]] = []
        seen: set[int] = set()
        pool = [int(p["user_id"]) for p in players]
        for channel in guild.voice_channels:
            for member in channel.members:
                if not member.bot:
                    pool.append(member.id)
        for uid in pool:
            if uid in skip or uid in seen:
                continue
            member = guild.get_member(uid)
            if member is None or member.bot:
                continue
            seen.add(uid)
            this_w = 0.0
            affinity = 0.0
            for act in self.bot.db.list_user_play_activity(guild.id, uid, since):
                last = parse_iso(act["last_seen"])
                days = (
                    max(0.0, (now - last).total_seconds() / 86400) if last else detect_days
                )
                w = recency_weight(days, half_life)
                key = str(act["game_key"])
                if key == game_key_s:
                    this_w = max(this_w, w)
                if key in confirmed_games:
                    affinity += min(w, confirmed_games[key])
            voice = self.bot.db.voice_minutes_between(guild.id, uid, confirmed)
            shared = self.bot.db.shared_play_session_counts(guild.id, uid, confirmed)
            score = social_score(
                this_game_weight=this_w,
                voice_minutes=voice,
                shared_sessions=shared,
                confirmed_ids=confirmed,
                shared_game_affinity=affinity,
            )
            if score >= float(config.PLAY_EXPAND_MIN_SCORE):
                candidates.append((score, uid))
        candidates.sort(reverse=True)
        max_send = int(config.PLAY_EXPAND_MAX)
        sent = 0
        names = join_names(
            [person_label(self.bot, guild, uid) for uid in confirmed[:4]]
        )
        when = parse_iso(row["proposed_at"])
        when_line = (
            format_play_when(when.astimezone(_tz())) if when else "soon"
        )
        body = (
            f"{names} are getting together for **{row['game_name']}** "
            f"{when_line}. Wanna join them?"
        )
        for _score, uid in candidates[:max_send]:
            member = guild.get_member(uid)
            if member is None:
                continue
            try:
                await member.send(body, view=PlayExpandView(suggestion_id))
            except (discord.Forbidden, discord.HTTPException):
                continue
            self.bot.db.add_play_expansion_invite(
                suggestion_id, uid, now.isoformat()
            )
            sent += 1
        if sent or not force:
            self.bot.db.update_play_suggestion(
                suggestion_id, expansion_sent=int(row["expansion_sent"] or 0) + 1
            )
        return sent

    async def create_discord_event(
        self, suggestion_id: int, *, force: bool = False
    ) -> discord.ScheduledEvent:
        row = self.bot.db.get_play_suggestion(suggestion_id)
        if row is None:
            raise PlayPublishError("Session is gone.")
        guild = self.bot.get_guild(int(row["guild_id"]))
        if guild is None:
            raise PlayPublishError("Guild unavailable.")
        if row["discord_event_id"]:
            try:
                existing = guild.get_scheduled_event(int(row["discord_event_id"]))
                if existing is None:
                    existing = await guild.fetch_scheduled_event(
                        int(row["discord_event_id"])
                    )
                if existing is not None:
                    return existing
            except (discord.NotFound, discord.HTTPException):
                pass
        settings = self.bot.db.get_play_settings(guild.id)
        voice_id = settings["play_voice_channel_id"]
        if not voice_id:
            raise PlayPublishError("Set an event voice channel first.")
        voice = guild.get_channel(int(voice_id))
        if not isinstance(voice, discord.VoiceChannel):
            raise PlayPublishError("Event voice channel is missing.")
        start = parse_iso(row["proposed_at"])
        if start is None:
            raise PlayPublishError("Session has no time.")
        if start <= _now_utc():
            raise PlayPublishError("That time is already in the past.")
        confirmed = [
            person_label(self.bot, guild, int(r["user_id"]))
            for r in self.bot.db.list_play_rsvps(suggestion_id, status="in")
        ]
        steam = (row["steam_url"] or "").strip()
        desc = f"Let's play {row['game_name']}."
        if steam:
            desc += f"\n{steam}"
        if confirmed:
            desc += f"\nIn: {join_names(confirmed[:15])}"
        try:
            event = await guild.create_scheduled_event(
                name=str(row["game_name"])[:100],
                start_time=start,
                end_time=start + timedelta(hours=3),
                description=desc[:1000],
                channel=voice,
                privacy_level=discord.PrivacyLevel.guild_only,
            )
        except discord.HTTPException as exc:
            raise PlayPublishError(f"Could not create Discord event: {exc}") from exc
        self.bot.db.update_play_suggestion(
            suggestion_id,
            discord_event_id=event.id,
            status="event",
        )
        await self.refresh_suggestion_message(suggestion_id)
        return event

    async def sync_discord_event(self, suggestion_id: int) -> None:
        row = self.bot.db.get_play_suggestion(suggestion_id)
        if row is None or not row["discord_event_id"]:
            return
        guild = self.bot.get_guild(int(row["guild_id"]))
        if guild is None:
            return
        start = parse_iso(row["proposed_at"])
        if start is None:
            return
        try:
            event = await guild.fetch_scheduled_event(int(row["discord_event_id"]))
            await event.edit(
                name=str(row["game_name"])[:100],
                start_time=start,
                end_time=start + timedelta(hours=3),
            )
        except (discord.NotFound, discord.Forbidden, discord.HTTPException) as exc:
            log.warning("Could not sync event %s: %s", suggestion_id, exc)

    async def cancel_suggestion(self, suggestion_id: int) -> None:
        row = self.bot.db.get_play_suggestion(suggestion_id)
        if row is None:
            return
        self.bot.db.update_play_suggestion(suggestion_id, status="cancelled")
        if row["discord_event_id"]:
            guild = self.bot.get_guild(int(row["guild_id"]))
            if guild is not None:
                try:
                    event = await guild.fetch_scheduled_event(
                        int(row["discord_event_id"])
                    )
                    await event.delete()
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    pass
        await self.refresh_suggestion_message(suggestion_id)

    async def send_reminder(self, suggestion_id: int) -> None:
        row = self.bot.db.get_play_suggestion(suggestion_id)
        if row is None or row["reminder_sent"]:
            return
        guild = self.bot.get_guild(int(row["guild_id"]))
        if guild is None:
            return
        when = parse_iso(row["proposed_at"])
        when_line = format_play_when(when.astimezone(_tz())) if when else "soon"
        confirmed = self.bot.db.list_play_rsvps(suggestion_id, status="in")
        text = f"Reminder: **{row['game_name']}** {when_line}."
        for rsvp in confirmed:
            member = guild.get_member(int(rsvp["user_id"]))
            if member is None:
                continue
            try:
                await member.send(text)
            except (discord.Forbidden, discord.HTTPException):
                continue
        if row["channel_id"] and row["message_id"]:
            channel = guild.get_channel(int(row["channel_id"]))
            if isinstance(channel, discord.TextChannel):
                mentions = " ".join(
                    m.mention
                    for r in confirmed
                    if (m := guild.get_member(int(r["user_id"]))) is not None
                )
                try:
                    await channel.send(
                        f"{text} {mentions}".strip(),
                        allowed_mentions=discord.AllowedMentions(users=True),
                    )
                except discord.HTTPException:
                    pass
        self.bot.db.update_play_suggestion(suggestion_id, reminder_sent=1)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(PlayTogetherCog(bot))
