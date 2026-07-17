from __future__ import annotations

import asyncio
import logging
import re
import shutil
import time
import traceback
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands, tasks
from rich_presence import (
    presence_between_tracks,
    presence_idle,
    presence_now_playing,
    update_presence as apply_rich_presence,
)
import yt_dlp

import config

log = logging.getLogger("dream_team.music")

YTDL_OPTS = {
    # Prefer audio-only; fall back broadly — android_vr/web clients expose
    # different containers, and FFmpeg re-encodes anyway for Discord.
    "format": "bestaudio/best",
    "quiet": True,
    "no_warnings": True,
    "default_search": "ytsearch1",
    "noplaylist": True,
    "extract_flat": False,
    # Prefer clients that are less aggressive about bot checks on shared IPs.
    "extractor_args": {
        "youtube": {
            "player_client": ["android_vr", "web", "mweb"],
        }
    },
}

_cookies_logged = False


def _cookies_path() -> Path | None:
    """Return cookies.txt if present (also try common misnamed uploads)."""
    candidates = [
        config.YTDLP_COOKIES,
        config.BASE_DIR / "cookies.txt",
        config.BASE_DIR / "cookie.txt",
        config.BASE_DIR / "Cookies.txt",
    ]
    seen: set[Path] = set()
    for path in candidates:
        resolved = path.expanduser()
        if not resolved.is_absolute():
            resolved = (config.BASE_DIR / resolved).resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        if resolved.is_file() and resolved.stat().st_size > 0:
            return resolved
    return None


def _ytdl_opts(**extra) -> dict:
    global _cookies_logged
    opts = {**YTDL_OPTS, **extra}
    cookies = _cookies_path()
    if cookies is not None:
        opts["cookiefile"] = str(cookies)
        if not _cookies_logged:
            log.info("yt-dlp using cookiefile %s (%s bytes)", cookies, cookies.stat().st_size)
            _cookies_logged = True
    elif not _cookies_logged:
        log.warning(
            "No cookies.txt found at %s — YouTube may block this host IP",
            config.YTDLP_COOKIES,
        )
        _cookies_logged = True
    return opts

_YT_HEADERS = (
    "User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36\r\n"
)

SOURCE_COLORS = {
    "youtube": discord.Color.from_rgb(200, 45, 45),
    "soundcloud": discord.Color.from_rgb(232, 98, 42),
}

# Shared Dream Team palette for idle / queue cards
BRAND_COLOR = discord.Color.from_rgb(14, 28, 48)
ACCENT_TEAL = discord.Color.from_rgb(46, 230, 166)


def _ffmpeg_executable() -> str:
    found = shutil.which("ffmpeg")
    if found:
        return found
    for candidate in (
        "/opt/homebrew/bin/ffmpeg",
        "/usr/local/bin/ffmpeg",
        "/usr/bin/ffmpeg",
    ):
        if Path(candidate).is_file():
            return candidate
    return "ffmpeg"


FFMPEG_EXECUTABLE = _ffmpeg_executable()
FFMPEG_BEFORE = (
    f"-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 "
    f"-headers '{_YT_HEADERS}'"
)
FFMPEG_OPTIONS = "-vn"


@dataclass
class Track:
    title: str
    webpage_url: str
    requester: discord.Member
    source: str
    query: str
    thumbnail: str | None = None
    uploader: str | None = None
    duration: int | None = None
    started_at: float | None = field(default=None, repr=False)


def _detect_source(query: str) -> str:
    lower = query.lower()
    if "soundcloud.com" in lower:
        return "soundcloud"
    if "youtube.com" in lower or "youtu.be" in lower:
        return "youtube"
    return "search"


def _pick_thumbnail(info: dict) -> str | None:
    thumb = info.get("thumbnail")
    if isinstance(thumb, str) and thumb.startswith("http"):
        return thumb
    thumbs = info.get("thumbnails") or []
    if isinstance(thumbs, list) and thumbs:
        best = max(
            (t for t in thumbs if isinstance(t, dict) and t.get("url")),
            key=lambda t: (t.get("height") or 0) * (t.get("width") or 0),
            default=None,
        )
        if best:
            return best.get("url")
    return None


def _format_duration(seconds: int | None) -> str:
    if not seconds or seconds <= 0:
        return "Live / unknown"
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def _source_label(source: str) -> str:
    if source == "soundcloud":
        return "SoundCloud"
    if source == "youtube":
        return "YouTube"
    return source.title()


def _clip(text: str, limit: int) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


async def resolve_track(query: str, requester: discord.Member) -> Track:
    source = _detect_source(query)

    def extract() -> dict:
        opts = _ytdl_opts(skip_download=True)
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(query, download=False)
            if info is None:
                raise ValueError("Nothing found for that link/search.")
            if "entries" in info:
                entries = [e for e in info["entries"] if e]
                if not entries:
                    raise ValueError("Nothing found for that search.")
                info = entries[0]
            return info

    info = await asyncio.to_thread(extract)
    webpage = info.get("webpage_url") or info.get("original_url") or query
    extractor = (info.get("extractor") or "").lower()
    resolved_source = source
    if source == "search":
        resolved_source = "soundcloud" if "soundcloud" in extractor else "youtube"

    duration = info.get("duration")
    try:
        duration_i = int(duration) if duration is not None else None
    except (TypeError, ValueError):
        duration_i = None

    return Track(
        title=info.get("title") or "Unknown track",
        webpage_url=webpage,
        requester=requester,
        source=resolved_source,
        query=webpage,
        thumbnail=_pick_thumbnail(info),
        uploader=info.get("uploader") or info.get("channel") or info.get("creator"),
        duration=duration_i,
    )


async def resolve_stream_url(track: Track) -> str:
    def extract_url() -> str:
        with yt_dlp.YoutubeDL(_ytdl_opts()) as ydl:
            info = ydl.extract_info(track.query, download=False)
            if info is None:
                raise ValueError("Could not refresh audio stream.")
            if "entries" in info:
                entries = [e for e in info["entries"] if e]
                if not entries:
                    raise ValueError("Could not refresh audio stream.")
                info = entries[0]
            url = info.get("url")
            if not url:
                raise ValueError("Could not get an audio stream for that track.")
            return url

    return await asyncio.to_thread(extract_url)


def make_audio_source(stream_url: str) -> discord.AudioSource:
    return discord.FFmpegOpusAudio(
        stream_url,
        executable=FFMPEG_EXECUTABLE,
        before_options=FFMPEG_BEFORE,
        options=FFMPEG_OPTIONS,
        bitrate=128,
    )


class TrackLinkView(discord.ui.View):
    def __init__(self, url: str, platform: str) -> None:
        super().__init__(timeout=None)
        self.add_item(
            discord.ui.Button(
                label=f"Open on {platform}",
                style=discord.ButtonStyle.link,
                url=url,
            )
        )


def track_embed(
    track: Track,
    *,
    heading: str = "Now playing",
    queue_position: int | None = None,
) -> discord.Embed:
    """Clean card: cover + title + a few quiet meta lines."""
    platform = _source_label(track.source)
    color = SOURCE_COLORS.get(track.source, BRAND_COLOR)

    artist = _clip(track.uploader, 80) if track.uploader else None
    length = _format_duration(track.duration)

    lines = [f"**[{_clip(track.title, 180)}]({track.webpage_url})**"]
    if artist:
        lines.append(artist)
    meta = f"{platform}  ·  {length}"
    if queue_position is not None:
        meta += f"  ·  #{queue_position} in queue"
    lines.append(f"*{meta}*")

    embed = discord.Embed(
        title=heading,
        description="\n".join(lines),
        color=color,
    )
    if track.thumbnail:
        embed.set_image(url=track.thumbnail)

    embed.set_author(name="Dream Team")
    embed.set_footer(
        text=track.requester.display_name,
        icon_url=track.requester.display_avatar.url,
    )
    return embed


def idle_music_embed() -> discord.Embed:
    embed = discord.Embed(
        title="Now playing",
        description="Nothing yet.\n`/play` a track from YouTube or SoundCloud.",
        color=BRAND_COLOR,
    )
    embed.set_author(name="Dream Team")
    embed.set_footer(text="Music")
    return embed


def queue_embed(player: "GuildPlayer") -> discord.Embed:
    embed = discord.Embed(title="Queue", color=BRAND_COLOR)
    embed.set_author(name="Dream Team")

    if player.current:
        cur = player.current
        embed.description = (
            f"**Now** — [{_clip(cur.title, 90)}]({cur.webpage_url})"
        )
        if cur.thumbnail:
            embed.set_thumbnail(url=cur.thumbnail)

    if player.queue:
        lines = [
            f"`{i:02d}`  [{_clip(t.title, 55)}]({t.webpage_url})"
            for i, t in enumerate(list(player.queue)[:10], start=1)
        ]
        if len(player.queue) > 10:
            lines.append(f"*+{len(player.queue) - 10} more*")
        embed.add_field(name="Up next", value="\n".join(lines), inline=False)
    elif not player.current:
        embed.description = "Empty. Use `/play` to add something."

    return embed


class GuildPlayer:
    def __init__(self, bot: commands.Bot, guild_id: int) -> None:
        self.bot = bot
        self.guild_id = guild_id
        self.queue: deque[Track] = deque()
        self.current: Track | None = None
        self.voice: discord.VoiceClient | None = None
        self._play_next = asyncio.Event()
        self._audio_lock = asyncio.Lock()
        self._player_task: asyncio.Task | None = None
        self._stopped = False

    def ensure_player_loop(self) -> None:
        if self._player_task is None or self._player_task.done():
            self._stopped = False
            self._player_task = asyncio.create_task(self._player_loop())

    def _sync_voice(self) -> discord.VoiceClient | None:
        guild = self.bot.get_guild(self.guild_id)
        if guild and guild.voice_client and guild.voice_client.is_connected():
            self.voice = guild.voice_client
        return self.voice

    async def connect(self, channel: discord.VoiceChannel) -> discord.VoiceClient:
        guild = self.bot.get_guild(self.guild_id)
        if guild is None:
            raise RuntimeError("Guild not found.")

        existing = guild.voice_client
        if existing and existing.is_connected():
            if existing.channel and existing.channel.id != channel.id:
                await existing.move_to(channel)
            self.voice = existing
            return existing

        self.voice = await channel.connect(reconnect=True, self_deaf=True)
        await asyncio.sleep(0.5)
        return self.voice

    async def enqueue(self, track: Track) -> int:
        self.queue.append(track)
        self.ensure_player_loop()
        self._play_next.set()
        return len(self.queue)

    async def skip(self) -> Track | None:
        skipped = self.current
        voice = self._sync_voice()
        if voice and voice.is_playing():
            voice.stop()
        return skipped

    async def stop(self) -> None:
        self.queue.clear()
        self.current = None
        self._stopped = True
        voice = self._sync_voice()
        if voice and (voice.is_playing() or voice.is_paused()):
            voice.stop()
        if voice and voice.is_connected():
            await voice.disconnect()
        self.voice = None
        if self._player_task and not self._player_task.done():
            self._player_task.cancel()
            try:
                await self._player_task
            except asyncio.CancelledError:
                pass
        self._player_task = None
        await self._set_presence(None)

    async def _set_presence(self, track: Track | None) -> None:
        cog = self.bot.get_cog("MusicCog")
        if isinstance(cog, MusicCog):
            await cog.update_presence(track, owner_guild_id=self.guild_id)

    def pause(self) -> bool:
        voice = self._sync_voice()
        if voice and voice.is_playing():
            voice.pause()
            return True
        return False

    def resume(self) -> bool:
        voice = self._sync_voice()
        if voice and voice.is_paused():
            voice.resume()
            return True
        return False

    async def _player_loop(self) -> None:
        try:
            while not self._stopped:
                if not self.queue:
                    self.current = None
                    self._play_next.clear()
                    try:
                        await asyncio.wait_for(self._play_next.wait(), timeout=180)
                    except asyncio.TimeoutError:
                        voice = self._sync_voice()
                        if voice and voice.is_connected() and not self.queue:
                            await voice.disconnect()
                            self.voice = None
                        break
                    continue

                voice = self._sync_voice()
                if voice is None or not voice.is_connected():
                    log.warning("Voice disconnected; clearing music queue")
                    self.queue.clear()
                    break

                track = self.queue.popleft()
                self.current = track
                finished = asyncio.Event()

                def after_play(error: Exception | None) -> None:
                    if error:
                        log.warning(
                            "Playback error for %s: %r\n%s",
                            track.title,
                            error,
                            "".join(traceback.format_exception(error)),
                        )
                    self.bot.loop.call_soon_threadsafe(finished.set)

                async with self._audio_lock:
                    try:
                        if voice.is_playing():
                            voice.stop()
                            await asyncio.sleep(0.2)

                        stream_url = await resolve_stream_url(track)
                        log.info(
                            "Starting '%s' via ffmpeg=%s",
                            track.title,
                            FFMPEG_EXECUTABLE,
                        )
                        source = make_audio_source(stream_url)
                        track.started_at = time.time()
                        voice.play(source, after=after_play, signal_type="music")
                        await self._set_presence(track)
                    except Exception as exc:
                        log.warning(
                            "Failed to start track %s: %r\n%s",
                            track.title,
                            exc,
                            traceback.format_exc(),
                        )
                        self.current = None
                        await self._set_presence(None)
                        continue

                await finished.wait()
                self.current = None
                if not self.queue:
                    await self._set_presence(None)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.exception("Music player crashed: %s", exc)
        finally:
            self.current = None
            if not self.queue:
                try:
                    await self._set_presence(None)
                except Exception:
                    pass


class MusicCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.players: dict[int, GuildPlayer] = {}
        self._presence_guild_id: int | None = None
        self._music_stopped_at: float | None = None
        self._showing_full_idle = True
        self._last_idle_rotate_at = time.time()
        self.idle_watchdog.start()
        log.info("Music cog loaded (ffmpeg=%s)", FFMPEG_EXECUTABLE)

    def cog_unload(self) -> None:
        self.idle_watchdog.cancel()

    def _app_id(self) -> int | None:
        if self.bot.application_id:
            return int(self.bot.application_id)
        if self.bot.user:
            return self.bot.user.id
        return None

    def get_player(self, guild_id: int) -> GuildPlayer:
        if guild_id not in self.players:
            self.players[guild_id] = GuildPlayer(self.bot, guild_id)
        return self.players[guild_id]

    def _any_music_active(self) -> bool:
        return any(p.current is not None for p in self.players.values())

    @tasks.loop(seconds=20)
    async def idle_watchdog(self) -> None:
        """Idle after 2 min no music; rotate the Watching title while idle."""
        if self._any_music_active():
            return

        app_id = self._app_id()
        now = time.time()

        # Finish the post-music cooldown → full idle
        if (
            self._music_stopped_at is not None
            and not self._showing_full_idle
            and now - self._music_stopped_at >= config.IDLE_AFTER_SECONDS
        ):
            try:
                await apply_rich_presence(
                    self.bot, presence_idle(application_id=app_id)
                )
                self._showing_full_idle = True
                self._music_stopped_at = None
                self._last_idle_rotate_at = now
                log.info("Idle status restored after cooldown")
            except Exception as exc:
                log.warning("Idle watchdog failed: %r", exc)
            return

        # While idle, change the big title every IDLE_ROTATE_SECONDS
        if self._showing_full_idle and (
            now - self._last_idle_rotate_at >= config.IDLE_ROTATE_SECONDS
        ):
            try:
                await apply_rich_presence(
                    self.bot, presence_idle(application_id=app_id)
                )
                self._last_idle_rotate_at = now
                log.info("Rotated idle Watching title")
            except Exception as exc:
                log.warning("Idle rotate failed: %r", exc)

    @idle_watchdog.before_loop
    async def before_idle_watchdog(self) -> None:
        await self.bot.wait_until_ready()

    async def update_presence(
        self, track: Track | None, *, owner_guild_id: int, paused: bool = False
    ) -> None:
        app_id = self._app_id()
        if track is not None:
            self._presence_guild_id = owner_guild_id
            self._music_stopped_at = None
            self._showing_full_idle = False
            player = self.players.get(owner_guild_id)
            queue_len = len(player.queue) if player else 0
            platform = _source_label(track.source)
            presence = presence_now_playing(
                title=track.title,
                platform=platform,
                requester=track.requester.display_name,
                uploader=track.uploader,
                source_key=track.source if track.source in {"youtube", "soundcloud"} else "youtube",
                started_at=track.started_at,
                duration=track.duration,
                queue_len=queue_len,
                party_id=f"dream-team-{owner_guild_id}",
                track_url=track.webpage_url,
                thumbnail_url=track.thumbnail,
                application_id=app_id,
                paused=paused,
            )
            try:
                await apply_rich_presence(self.bot, presence)
            except Exception as exc:
                log.warning("Could not update presence: %r", exc)
            await self.refresh_now_playing_panel(
                owner_guild_id, track=track, queue_len=queue_len, paused=paused
            )
            return

        if (
            self._presence_guild_id is not None
            and self._presence_guild_id != owner_guild_id
        ):
            other = self.players.get(self._presence_guild_id)
            if other and other.current is not None:
                return

        for gid, player in self.players.items():
            if player.current is not None:
                voice = player._sync_voice()
                is_paused = bool(voice and voice.is_paused())
                await self.update_presence(
                    player.current, owner_guild_id=gid, paused=is_paused
                )
                return

        # Nothing playing — start 2-minute countdown to full idle
        self._presence_guild_id = None
        self._music_stopped_at = time.time()
        self._showing_full_idle = False
        try:
            await apply_rich_presence(
                self.bot, presence_between_tracks(application_id=app_id)
            )
        except Exception as exc:
            log.warning("Could not set between-tracks presence: %r", exc)
        await self.refresh_now_playing_panel(
            owner_guild_id, track=None, queue_len=0, paused=False
        )

    async def refresh_now_playing_panel(
        self,
        guild_id: int,
        *,
        track: Track | None,
        queue_len: int,
        paused: bool = False,
    ) -> None:
        """Editable channel message — this is the fancy UI bots CAN show."""
        channel_id, message_id = self.bot.db.get_now_playing_panel(guild_id)
        if not channel_id:
            return

        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return
        channel = guild.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            return

        if track is None:
            embed = idle_music_embed()
        else:
            heading = "Paused" if paused else "Now playing"
            embed = track_embed(track, heading=heading)
            if queue_len:
                embed.description = (
                    (embed.description or "")
                    + f"\n*{queue_len} waiting in queue*"
                )

        try:
            view = None
            if track is not None:
                view = TrackLinkView(
                    track.webpage_url, _source_label(track.source)
                )

            if message_id:
                try:
                    msg = await channel.fetch_message(message_id)
                    await msg.edit(embed=embed, view=view)
                    return
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    pass

            msg = await channel.send(embed=embed, view=view)
            self.bot.db.set_now_playing_panel(guild_id, channel.id, msg.id)
            try:
                await msg.pin(reason="Dream Team now-playing panel")
            except discord.HTTPException:
                pass
        except discord.Forbidden:
            log.warning("Cannot update now-playing panel in %s", guild.name)
        except discord.HTTPException as exc:
            log.warning("Now-playing panel failed: %s", exc)

    async def _require_voice(
        self, interaction: discord.Interaction
    ) -> tuple[discord.Member, discord.VoiceChannel] | None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                "Use this in a server.", ephemeral=True
            )
            return None

        if interaction.user.voice is None or interaction.user.voice.channel is None:
            await interaction.response.send_message(
                "Join a voice channel first.", ephemeral=True
            )
            return None

        channel = interaction.user.voice.channel
        if not isinstance(channel, discord.VoiceChannel):
            await interaction.response.send_message(
                "I can only join regular voice channels.", ephemeral=True
            )
            return None
        return interaction.user, channel

    @app_commands.command(
        name="play",
        description="Play audio from YouTube or SoundCloud (link or search)",
    )
    @app_commands.describe(query="YouTube/SoundCloud URL or search text")
    async def play(self, interaction: discord.Interaction, query: str) -> None:
        ctx = await self._require_voice(interaction)
        if ctx is None:
            return
        member, channel = ctx

        await interaction.response.defer()
        player = self.get_player(interaction.guild_id)
        was_idle = player.current is None and not player.queue

        try:
            if FFMPEG_EXECUTABLE == "ffmpeg" and not shutil.which("ffmpeg"):
                raise RuntimeError(
                    "FFmpeg is not installed (or not on PATH). "
                    "Install it with: brew install ffmpeg"
                )
            await player.connect(channel)
            track = await resolve_track(query.strip(), member)
            position = await player.enqueue(track)
        except Exception as exc:
            log.warning("/play failed: %r\n%s", exc, traceback.format_exc())
            msg = str(exc)
            # Strip ANSI color codes from yt-dlp errors for Discord.
            msg = re.sub(r"\x1b\[[0-9;]*m", "", msg)
            if "Sign in to confirm" in msg or "not a bot" in msg:
                cookies = _cookies_path()
                if cookies is None:
                    msg = (
                        "YouTube blocked this server IP. Upload a Netscape "
                        "`cookies.txt` next to bot.py (see README), then restart."
                    )
                else:
                    msg = (
                        "YouTube still blocked playback even with cookies. "
                        "Re-export cookies via private window → youtube.com/robots.txt "
                        "(throwaway account), replace cookies.txt, restart."
                    )
            await interaction.followup.send(f"Couldn't play that: {msg}")
            return

        if was_idle:
            embed = track_embed(track, heading="Now playing")
        else:
            embed = track_embed(
                track, heading="Queued", queue_position=position
            )

        await interaction.followup.send(
            embed=embed,
            view=TrackLinkView(track.webpage_url, _source_label(track.source)),
        )

    @app_commands.command(name="skip", description="Skip the current track")
    async def skip(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                "Use this in a server.", ephemeral=True
            )
            return
        player = self.get_player(interaction.guild_id)
        skipped = await player.skip()
        if skipped is None and not player.queue:
            await interaction.response.send_message(
                "Nothing to skip.", ephemeral=True
            )
            return
        title = skipped.title if skipped else "current track"
        await interaction.response.send_message(f"Skipped **{title}**.")

    @app_commands.command(name="stop", description="Stop music and leave voice")
    async def stop(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                "Use this in a server.", ephemeral=True
            )
            return
        await self.get_player(interaction.guild_id).stop()
        await interaction.response.send_message(
            "Stopped and left the voice channel."
        )

    @app_commands.command(name="pause", description="Pause the current track")
    async def pause(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                "Use this in a server.", ephemeral=True
            )
            return
        player = self.get_player(interaction.guild_id)
        if player.pause():
            if player.current:
                await self.update_presence(
                    player.current, owner_guild_id=interaction.guild_id, paused=True
                )
            await interaction.response.send_message("Paused.")
        else:
            await interaction.response.send_message(
                "Nothing is playing.", ephemeral=True
            )

    @app_commands.command(name="resume", description="Resume paused music")
    async def resume(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                "Use this in a server.", ephemeral=True
            )
            return
        player = self.get_player(interaction.guild_id)
        if player.resume():
            if player.current:
                await self.update_presence(
                    player.current, owner_guild_id=interaction.guild_id, paused=False
                )
            await interaction.response.send_message("Resumed.")
        else:
            await interaction.response.send_message(
                "Nothing is paused.", ephemeral=True
            )

    @app_commands.command(name="queue", description="Show the music queue")
    async def queue(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                "Use this in a server.", ephemeral=True
            )
            return
        player = self.get_player(interaction.guild_id)
        if not player.current and not player.queue:
            await interaction.response.send_message(
                "Queue is empty.", ephemeral=True
            )
            return

        await interaction.response.send_message(embed=queue_embed(player))

    @app_commands.command(
        name="nowplaying", description="Show the track currently playing"
    )
    async def nowplaying(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                "Use this in a server.", ephemeral=True
            )
            return
        track = self.get_player(interaction.guild_id).current
        if track is None:
            await interaction.response.send_message(
                "Nothing is playing.", ephemeral=True
            )
            return
        await interaction.response.send_message(
            embed=track_embed(track, heading="Now playing"),
            view=TrackLinkView(track.webpage_url, _source_label(track.source)),
        )

    @app_commands.command(name="leave", description="Disconnect the bot from voice")
    async def leave(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                "Use this in a server.", ephemeral=True
            )
            return
        await self.get_player(interaction.guild_id).stop()
        await interaction.response.send_message("Left the voice channel.")
