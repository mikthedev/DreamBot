"""
Discord Rich Presence helper modeled after the classic C Discord RPC API:

    DiscordRichPresence discordPresence;
    discordPresence.state = "Playing Solo";
    discordPresence.details = "Competitive";
    discordPresence.startTimestamp = ...;
    discordPresence.endTimestamp = ...;
    discordPresence.largeImageKey = "numbani";
    discordPresence.largeImageText = "Numbani";
    discordPresence.smallImageKey = "rogue";
    discordPresence.smallImageText = "Rogue - Level 100";
    discordPresence.partyId = "...";
    discordPresence.partySize = 1;
    discordPresence.partyMax = 5;
    Discord_UpdatePresence(&discordPresence);

Note: joinSecret / spectateSecret are Game SDK invite features and are NOT
supported for Discord bot gateway presence. Use a track URL button instead.
"""

from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass, field
from typing import Any

import discord
from discord.ext import commands, tasks

log = logging.getLogger("dream_team.presence")


def _clip(value: str | None, limit: int) -> str | None:
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


@dataclass
class DiscordRichPresence:
    """Mirrors the classic DiscordRichPresence C struct (bot-compatible fields)."""

    # Top line context under the activity type, e.g. "Playing Solo"
    state: str | None = None
    # Main line, e.g. "Competitive" / song title
    details: str | None = None

    # Activity header: Listening to {name} / Playing {name} / Watching {name}
    name: str = "Dream Team"
    activity_type: discord.ActivityType = discord.ActivityType.listening

    # Unix timestamps in SECONDS (same unit as the C SDK examples)
    start_timestamp: int | None = None
    end_timestamp: int | None = None

    # Art asset keys from Developer Portal → Rich Presence → Art Assets
    large_image_key: str | None = None
    large_image_text: str | None = None
    small_image_key: str | None = None
    small_image_text: str | None = None

    # Party / queue style counters → shown as (partySize of partyMax)
    party_id: str | None = None
    party_size: int | None = None
    party_max: int | None = None

    # Not supported on bot gateway presence (kept for API familiarity)
    join_secret: str | None = None
    spectate_secret: str | None = None

    # Bot-friendly stand-in for join links (may be hidden by Discord for some clients)
    button_label: str | None = None
    button_url: str | None = None

    application_id: int | None = None
    # Optional unicode emoji for ActivityType.custom (shows beside status)
    emoji: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_activity(self) -> discord.BaseActivity:
        if self.join_secret or self.spectate_secret:
            log.debug(
                "joinSecret/spectateSecret are ignored for bot presence "
                "(Game SDK only)."
            )

        # Custom status: short line + emoji — looks cleaner than
        # "Listening to <very long song title>"
        if self.activity_type == discord.ActivityType.custom:
            text = _clip(self.state or self.name, 48) or "Dream Team"
            return discord.CustomActivity(name=text, emoji=self.emoji)

        timestamps: dict[str, int] = {}
        if self.start_timestamp is not None:
            timestamps["start"] = int(self.start_timestamp) * 1000
        if self.end_timestamp is not None:
            timestamps["end"] = int(self.end_timestamp) * 1000

        assets: dict[str, str] = {}
        if self.large_image_key:
            key = self.large_image_key.strip()
            if key.startswith(("http://", "https://")):
                assets["large_image"] = key
            else:
                assets["large_image"] = key.lower()[:32]
        if self.large_image_text:
            assets["large_text"] = _clip(self.large_image_text, 128) or ""
        if self.small_image_key:
            key = self.small_image_key.strip()
            if key.startswith(("http://", "https://")):
                assets["small_image"] = key
            else:
                assets["small_image"] = key.lower()[:32]
        if self.small_image_text:
            assets["small_text"] = _clip(self.small_image_text, 128) or ""

        party: dict[str, Any] | None = None
        if self.party_id or self.party_size is not None or self.party_max is not None:
            party = {}
            if self.party_id:
                party["id"] = _clip(self.party_id, 128)
            size_now = self.party_size if self.party_size is not None else 1
            size_max = self.party_max if self.party_max is not None else max(size_now, 1)
            party["size"] = [max(0, int(size_now)), max(1, int(size_max))]

        buttons: list[dict[str, str]] | None = None
        if self.button_label and self.button_url:
            buttons = [
                {
                    "label": _clip(self.button_label, 32) or "Open",
                    "url": self.button_url,
                }
            ]

        kwargs: dict[str, Any] = {
            "name": _clip(self.name, 128) or "Dream Team",
            "type": self.activity_type,
            "state": _clip(self.state, 128),
            "details": _clip(self.details, 128),
            "application_id": self.application_id,
            "timestamps": timestamps,
            "assets": assets,
            "party": party or {},
            **self.extra,
        }
        activity = discord.Activity(
            **{k: v for k, v in kwargs.items() if v is not None and v != {}}
        )
        if buttons:
            activity.buttons = buttons  # type: ignore[attr-defined]
        return activity


async def update_presence(
    bot: discord.Client,
    presence: DiscordRichPresence,
    *,
    status: discord.Status = discord.Status.online,
) -> discord.Activity:
    """Python equivalent of Discord_UpdatePresence(&discordPresence)."""
    if presence.application_id is None:
        presence.application_id = bot.application_id or (
            bot.user.id if bot.user else None
        )
    activity = presence.to_activity()
    await bot.change_presence(status=status, activity=activity)
    log.info(
        "Rich presence updated: name=%r details=%r state=%r party=%s/%s",
        presence.name,
        presence.details,
        presence.state,
        presence.party_size,
        presence.party_max,
    )
    return activity


# Bold status title rotates — this is the big line under "Watching"
_IDLE_TITLES = (
    "Helping with the boring stuff",
    "Keeping the server tidy",
    "Watching the lobby",
    "Ready for the squad",
    "Holding down the fort",
    "On standby for Dream Team",
)


def presence_idle(*, application_id: int | None = None) -> DiscordRichPresence:
    """Idle — rotating title under Watching (not stuck on 'DreamBot')."""
    title = random.choice(_IDLE_TITLES)
    return DiscordRichPresence(
        name=title,
        activity_type=discord.ActivityType.watching,
        details="DreamBot",
        state="Always around",
        large_image_key="dreamteam",
        large_image_text="DreamBot",
        application_id=application_id,
    )


class IdlePresenceCog(commands.Cog):
    """Rotate Watching title while the bot is online."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._last_rotate_at = 0.0
        self.idle_watchdog.start()

    def cog_unload(self) -> None:
        self.idle_watchdog.cancel()

    async def force_idle(self) -> None:
        app_id = self.bot.application_id or (
            self.bot.user.id if self.bot.user else None
        )
        try:
            await update_presence(
                self.bot, presence_idle(application_id=int(app_id) if app_id else None)
            )
            self._last_rotate_at = time.time()
        except Exception as exc:
            log.warning("Could not set idle presence: %s", exc)

    @tasks.loop(seconds=30)
    async def idle_watchdog(self) -> None:
        import config

        now = time.time()
        if now - self._last_rotate_at < config.IDLE_ROTATE_SECONDS:
            return
        await self.force_idle()
        log.info("Rotated idle Watching title")

    @idle_watchdog.before_loop
    async def before_idle_watchdog(self) -> None:
        await self.bot.wait_until_ready()

