import asyncio
import logging
import re
from datetime import datetime
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import commands, tasks

import config
from birthdays import (
    Birthday,
    celebration_embed,
    is_birthday_today,
    parse_birthday,
)
from database import Database
from music import MusicCog
from nicknames import build_nickname, display_base, is_guild_manager
from panel import PanelCog

log = logging.getLogger("dream_team")

NAME_PATTERN = re.compile(r"^[\w\s\-'.А-Яа-яЁёІіЇїЄєҐґ]{1,24}$", re.UNICODE)


class DreamTeamBot(commands.Bot):
    def __init__(self, db: Database) -> None:
        intents = discord.Intents.default()
        intents.members = True
        intents.message_content = True

        super().__init__(command_prefix="!", intents=intents)
        self.db = db

    async def setup_hook(self) -> None:
        await self.add_cog(WelcomeCog(self))
        await self.add_cog(BirthdayCog(self))
        await self.add_cog(MusicCog(self))
        await self.add_cog(AdminCog(self))
        await self.add_cog(PanelCog(self))

    async def on_ready(self) -> None:
        # Sync to each guild only (instant). Clear globals so Discord doesn't show duplicates.
        synced_total = 0
        for guild in self.guilds:
            try:
                self.tree.copy_global_to(guild=guild)
                synced = await self.tree.sync(guild=guild)
                synced_total += len(synced)
                log.info("Synced %s command(s) to guild %s", len(synced), guild.name)
            except Exception as exc:
                log.warning("Guild sync failed for %s: %s", guild.name, exc)
        try:
            if self.application_id:
                await self.http.bulk_upsert_global_commands(self.application_id, [])
                log.info("Cleared global slash commands (avoids duplicates)")
        except Exception as exc:
            log.warning("Could not clear global commands: %s", exc)

        log.info(
            "Logged in as %s (%s) — %s guild command(s) synced",
            self.user,
            self.user.id,
            synced_total,
        )
        try:
            from rich_presence import presence_idle, update_presence

            app_id = self.application_id or (self.user.id if self.user else None)
            await update_presence(
                self,
                presence_idle(application_id=int(app_id) if app_id else None),
            )
            music = self.get_cog("MusicCog")
            if music is not None and hasattr(music, "_last_idle_rotate_at"):
                music._last_idle_rotate_at = __import__("time").time()
                music._showing_full_idle = True
        except Exception as exc:
            log.warning("Could not set idle presence: %s", exc)


class WelcomeCog(commands.Cog):
    def __init__(self, bot: DreamTeamBot) -> None:
        self.bot = bot
        self.sync_nicknames.start()

    def cog_unload(self) -> None:
        self.sync_nicknames.cancel()

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        if member.bot:
            return

        await self._assign_auto_role(member)
        await self._prompt_for_real_name(member)

    async def _assign_auto_role(self, member: discord.Member) -> None:
        settings = self.bot.db.get_settings(member.guild.id)
        if not settings or not settings["auto_role_id"]:
            return

        role = member.guild.get_role(settings["auto_role_id"])
        if role is None:
            log.warning(
                "Auto-role %s missing in guild %s",
                settings["auto_role_id"],
                member.guild.id,
            )
            return

        try:
            await member.add_roles(role, reason="Dream Team auto-role")
        except discord.Forbidden:
            log.warning(
                "Missing permission to assign role %s in %s",
                role.name,
                member.guild.name,
            )
        except discord.HTTPException as exc:
            log.warning("Failed to assign auto-role: %s", exc)

    async def _resolve_welcome_channel(
        self, guild: discord.Guild
    ) -> discord.TextChannel | None:
        settings = self.bot.db.get_settings(guild.id)
        channel_id = None
        if settings and settings["welcome_channel_id"]:
            channel_id = settings["welcome_channel_id"]
        elif config.WELCOME_CHANNEL_ID.isdigit():
            channel_id = int(config.WELCOME_CHANNEL_ID)

        if channel_id:
            channel = guild.get_channel(channel_id)
            if isinstance(channel, discord.TextChannel):
                return channel

        if isinstance(guild.system_channel, discord.TextChannel):
            return guild.system_channel
        return None

    async def _prompt_for_real_name(self, member: discord.Member) -> None:
        prompt = (
            f"Hello {member.mention}, welcome to **Dream Team**!\n"
            "How should we call you? Reply with your **real name** "
            "(for example: `Миша` or `Mike`).\n"
            "Your server nickname will look like: "
            f"`{display_base(member)} (YourName)`"
        )

        channel = await self._resolve_welcome_channel(member.guild)
        destination: discord.abc.Messageable | None = channel

        if destination is None:
            try:
                destination = await member.create_dm()
            except discord.HTTPException:
                log.warning("No welcome channel and cannot DM %s", member)
                return

        try:
            await destination.send(prompt)
        except discord.Forbidden:
            log.warning("Cannot send welcome prompt in guild %s", member.guild.id)
            return

        def check(message: discord.Message) -> bool:
            if message.author.id != member.id:
                return False
            if isinstance(destination, discord.TextChannel):
                return message.channel.id == destination.id
            return isinstance(message.channel, discord.DMChannel)

        timeout = config.NAME_PROMPT_TIMEOUT_MINUTES * 60
        try:
            reply = await self.bot.wait_for("message", check=check, timeout=timeout)
        except asyncio.TimeoutError:
            await destination.send(
                f"{member.mention} No name received in time. "
                "An admin can set it later with `/setname`."
            )
            return

        real_name = reply.content.strip()
        if not NAME_PATTERN.match(real_name):
            await destination.send(
                f"{member.mention} That name looks invalid. "
                "Use 1–24 letters/numbers (spaces and `- ' .` allowed). "
                "Ask an admin to run `/setname` if you need help."
            )
            return

        await self._apply_real_name(member, real_name, announce_in=destination)

    async def _apply_real_name(
        self,
        member: discord.Member,
        real_name: str,
        announce_in: discord.abc.Messageable | None = None,
    ) -> bool:
        self.bot.db.set_real_name(member.guild.id, member.id, real_name)
        nick = build_nickname(display_base(member), real_name)

        try:
            await member.edit(nick=nick, reason="Dream Team real-name nickname")
        except discord.Forbidden:
            msg = (
                f"Saved **{real_name}**, but I can't change nicknames "
                "(need **Manage Nicknames** and a role above the member)."
            )
            if announce_in:
                await announce_in.send(f"{member.mention} {msg}")
            return False
        except discord.HTTPException as exc:
            log.warning("Nickname edit failed: %s", exc)
            return False

        if announce_in:
            await announce_in.send(
                f"Welcome, **{real_name}**! Your nickname is now `{nick}`.\n"
                "Optional: reply with your birthday as `DD.MM` (or `skip`). "
                "You can also set it later with `/setbirthday`."
            )
            await self._prompt_for_birthday(member, announce_in)
        return True

    async def _prompt_for_birthday(
        self,
        member: discord.Member,
        destination: discord.abc.Messageable,
    ) -> None:
        def check(message: discord.Message) -> bool:
            if message.author.id != member.id:
                return False
            if isinstance(destination, discord.TextChannel):
                return message.channel.id == destination.id
            return isinstance(message.channel, discord.DMChannel)

        try:
            reply = await self.bot.wait_for(
                "message",
                check=check,
                timeout=config.NAME_PROMPT_TIMEOUT_MINUTES * 60,
            )
        except asyncio.TimeoutError:
            return

        text = reply.content.strip()
        if text.lower() in {"skip", "no", "-", "пропустити", "нет"}:
            await destination.send(
                f"{member.mention} No worries — use `/setbirthday` anytime."
            )
            return

        bday = parse_birthday(text)
        if bday is None:
            await destination.send(
                f"{member.mention} Couldn't read that date. "
                "Use `/setbirthday` with `DD.MM` (example: `15.03`)."
            )
            return

        self.bot.db.set_birthday(
            member.guild.id, member.id, bday.month, bday.day, bday.year
        )
        await destination.send(
            f"{member.mention} Birthday saved: **{bday.display()}**. "
            "We'll celebrate it with Dream Team!"
        )

    @tasks.loop(hours=config.NICKNAME_SYNC_HOURS)
    async def sync_nicknames(self) -> None:
        """Adapt Discord display name; keep the real name in parentheses."""
        for guild in self.bot.guilds:
            rows = self.bot.db.all_real_names(guild.id)
            for row in rows:
                member = guild.get_member(row["user_id"])
                if member is None or member.bot:
                    continue
                if member.id == guild.owner_id:
                    continue

                desired = build_nickname(display_base(member), row["real_name"])
                if member.nick == desired:
                    continue

                try:
                    await member.edit(nick=desired, reason="Daily Discord name sync")
                    await asyncio.sleep(1.0)
                except discord.Forbidden:
                    continue
                except discord.HTTPException as exc:
                    log.warning("Sync failed for %s in %s: %s", member, guild.name, exc)

    @sync_nicknames.before_loop
    async def before_sync(self) -> None:
        await self.bot.wait_until_ready()


class BirthdayCog(commands.Cog):
    def __init__(self, bot: DreamTeamBot) -> None:
        self.bot = bot
        self.check_birthdays.start()

    def cog_unload(self) -> None:
        self.check_birthdays.cancel()

    def _local_now(self) -> datetime:
        try:
            tz = ZoneInfo(config.BIRTHDAY_TIMEZONE)
        except Exception:
            tz = ZoneInfo("UTC")
        return datetime.now(tz)

    async def _birthday_channel(self, guild: discord.Guild) -> discord.TextChannel | None:
        settings = self.bot.db.get_settings(guild.id)
        if settings and settings["birthday_channel_id"]:
            channel = guild.get_channel(settings["birthday_channel_id"])
            if isinstance(channel, discord.TextChannel):
                return channel
        if settings and settings["welcome_channel_id"]:
            channel = guild.get_channel(settings["welcome_channel_id"])
            if isinstance(channel, discord.TextChannel):
                return channel
        if isinstance(guild.system_channel, discord.TextChannel):
            return guild.system_channel
        return None

    async def _post_birthday(
        self,
        channel: discord.TextChannel,
        member: discord.Member,
        *,
        mark: bool = True,
    ) -> bool:
        real_name = self.bot.db.get_real_name(member.guild.id, member.id)
        embed = celebration_embed(
            mention=member.mention,
            real_name=real_name,
            avatar_url=member.display_avatar.url,
        )
        try:
            await channel.send(content=member.mention, embed=embed)
            if mark:
                today = self._local_now().date()
                self.bot.db.mark_birthday_announced(
                    member.guild.id, member.id, today.year
                )
            return True
        except discord.Forbidden:
            log.warning("Cannot post birthday in %s", member.guild.name)
        except discord.HTTPException as exc:
            log.warning("Birthday announce failed: %s", exc)
        return False

    async def announce_member_if_today(
        self, member: discord.Member, bday: Birthday
    ) -> str | None:
        """If bday is today, post celebration (once). Returns status note or None."""
        today = self._local_now().date()
        if not is_birthday_today(bday, today):
            return None

        channel = await self._birthday_channel(member.guild)
        if channel is None:
            return (
                "It's their birthday today, but no birthday/welcome channel is set. "
                "Use `/panel` → Birthday channel."
            )

        if self.bot.db.was_birthday_announced(
            member.guild.id, member.id, today.year
        ):
            return f"Birthday already announced this year in {channel.mention}."

        ok = await self._post_birthday(channel, member, mark=True)
        if ok:
            return f"Also posted today's celebration in {channel.mention}."
        return f"Tried to post in {channel.mention} but Discord blocked it."

    async def announce_todays_birthdays(
        self, guild: discord.Guild, *, force: bool = False
    ) -> tuple[int, str]:
        """Post celebrations for everyone whose birthday is today."""
        channel = await self._birthday_channel(guild)
        if channel is None:
            return 0, "No birthday channel (set it in `/panel`)."

        today = self._local_now().date()
        posted = 0
        skipped = 0
        for row in self.bot.db.all_birthdays(guild.id):
            bday = Birthday(month=row["month"], day=row["day"], year=row["year"])
            if not is_birthday_today(bday, today):
                continue
            if not force and self.bot.db.was_birthday_announced(
                guild.id, row["user_id"], today.year
            ):
                skipped += 1
                continue
            member = guild.get_member(row["user_id"])
            if member is None or member.bot:
                continue
            if await self._post_birthday(channel, member, mark=True):
                posted += 1

        detail = f"Channel: {channel.mention}."
        if skipped:
            detail += f" Skipped {skipped} already announced."
        if posted == 0 and skipped == 0:
            detail += " Nobody in the DB has a birthday today."
        return posted, detail

    @app_commands.command(
        name="setbirthday",
        description="Save a birthday (DD.MM or DD.MM.YYYY)",
    )
    @app_commands.describe(
        date="Birthday as DD.MM (example: 15.03)",
        member="Admin only: set someone else's birthday",
    )
    async def setbirthday(
        self,
        interaction: discord.Interaction,
        date: str,
        member: discord.Member | None = None,
    ) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                "Use this command inside the server.", ephemeral=True
            )
            return

        target = member or interaction.user
        if member is not None and member.id != interaction.user.id:
            if not is_guild_manager(interaction.user):
                await interaction.response.send_message(
                    "Only admins can set someone else's birthday.",
                    ephemeral=True,
                )
                return

        bday = parse_birthday(date)
        if bday is None:
            await interaction.response.send_message(
                "Invalid date. Use `DD.MM` or `DD.MM.YYYY` (example: `15.03`).",
                ephemeral=True,
            )
            return

        self.bot.db.set_birthday(
            interaction.guild_id, target.id, bday.month, bday.day, bday.year
        )

        note = await self.announce_member_if_today(target, bday)
        text = f"Saved birthday for {target.mention}: **{bday.display()}**"
        if note:
            text += f"\n{note}"

        await interaction.response.send_message(text, ephemeral=True)

    @app_commands.command(name="mybirthday", description="Show your saved birthday")
    async def mybirthday(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                "Use this command inside the server.", ephemeral=True
            )
            return

        row = self.bot.db.get_birthday(interaction.guild_id, interaction.user.id)
        if row is None:
            await interaction.response.send_message(
                "No birthday saved yet. Use `/setbirthday` with `DD.MM`.",
                ephemeral=True,
            )
            return

        bday = Birthday(month=row["month"], day=row["day"], year=row["year"])
        await interaction.response.send_message(
            f"Your birthday: **{bday.display()}**",
            ephemeral=True,
        )

    @app_commands.command(name="clearbirthday", description="Remove your saved birthday")
    async def clearbirthday(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                "Use this command inside the server.", ephemeral=True
            )
            return

        self.bot.db.clear_birthday(interaction.guild_id, interaction.user.id)
        await interaction.response.send_message("Birthday removed.", ephemeral=True)

    @tasks.loop(hours=1)
    async def check_birthdays(self) -> None:
        now = self._local_now()
        if now.hour != config.BIRTHDAY_ANNOUNCE_HOUR:
            return

        for guild in self.bot.guilds:
            posted, detail = await self.announce_todays_birthdays(guild, force=False)
            if posted:
                log.info(
                    "Birthday run in %s: posted=%s (%s)",
                    guild.name,
                    posted,
                    detail,
                )

    @check_birthdays.before_loop
    async def before_birthday_check(self) -> None:
        await self.bot.wait_until_ready()


class AdminCog(commands.Cog):
    def __init__(self, bot: DreamTeamBot) -> None:
        self.bot = bot

    def _deny_if_not_manager(self, interaction: discord.Interaction) -> str | None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            return "Use this command inside the server."
        if not is_guild_manager(interaction.user):
            return "Only the server owner or admins can use this."
        return None

    @app_commands.command(
        name="setname",
        description="Set a member's real name and update their nickname",
    )
    @app_commands.describe(
        member="Who to rename",
        real_name="Real name to show in parentheses",
    )
    async def setname(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        real_name: str,
    ) -> None:
        err = self._deny_if_not_manager(interaction)
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return

        real_name = real_name.strip()
        if not NAME_PATTERN.match(real_name):
            await interaction.response.send_message(
                "Invalid name. Use 1–24 letters/numbers (spaces and `- ' .` allowed).",
                ephemeral=True,
            )
            return

        self.bot.db.set_real_name(interaction.guild_id, member.id, real_name)
        nick = build_nickname(display_base(member), real_name)

        try:
            await member.edit(nick=nick, reason=f"Set by {interaction.user}")
        except discord.Forbidden:
            await interaction.response.send_message(
                "Saved the name, but I can't edit that member's nickname "
                "(role hierarchy / Manage Nicknames).",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            f"Updated {member.mention} → `{nick}`",
            ephemeral=True,
        )

    @app_commands.command(
        name="setautorole",
        description="Role automatically given to new members",
    )
    @app_commands.describe(role="Role to assign on join (omit to clear)")
    async def setautorole(
        self,
        interaction: discord.Interaction,
        role: discord.Role | None = None,
    ) -> None:
        err = self._deny_if_not_manager(interaction)
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return

        self.bot.db.set_auto_role(interaction.guild_id, role.id if role else None)
        if role:
            await interaction.response.send_message(
                f"New members will get {role.mention}.",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message("Auto-role cleared.", ephemeral=True)

    @app_commands.command(
        name="setwelcome",
        description="Channel used for welcome + name prompts",
    )
    @app_commands.describe(channel="Welcome channel (omit to clear)")
    async def setwelcome(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel | None = None,
    ) -> None:
        err = self._deny_if_not_manager(interaction)
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return

        self.bot.db.set_welcome_channel(
            interaction.guild_id,
            channel.id if channel else None,
        )
        if channel:
            await interaction.response.send_message(
                f"Welcome prompts will go to {channel.mention}.",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                "Welcome channel cleared (will try system channel / DMs).",
                ephemeral=True,
            )

    @app_commands.command(
        name="setbirthdaychannel",
        description="Channel for birthday celebration messages",
    )
    @app_commands.describe(channel="Birthday channel (omit to clear)")
    async def setbirthdaychannel(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel | None = None,
    ) -> None:
        err = self._deny_if_not_manager(interaction)
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return

        self.bot.db.set_birthday_channel(
            interaction.guild_id,
            channel.id if channel else None,
        )
        if channel:
            await interaction.response.send_message(
                f"Birthday messages will go to {channel.mention}.",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                "Birthday channel cleared (falls back to welcome / system channel).",
                ephemeral=True,
            )

    @app_commands.command(
        name="setmusicchannel",
        description="Channel for the live now-playing panel (cover art card)",
    )
    @app_commands.describe(channel="Music panel channel (omit to clear)")
    async def setmusicchannel(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel | None = None,
    ) -> None:
        err = self._deny_if_not_manager(interaction)
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return

        if channel is None:
            self.bot.db.set_now_playing_panel(interaction.guild_id, None, None)
            await interaction.response.send_message(
                "Now-playing panel cleared.", ephemeral=True
            )
            return

        self.bot.db.set_now_playing_panel(interaction.guild_id, channel.id, None)
        music = self.bot.get_cog("MusicCog")
        if isinstance(music, MusicCog):
            player = music.get_player(interaction.guild_id)
            await music.refresh_now_playing_panel(
                interaction.guild_id,
                track=player.current,
                queue_len=len(player.queue),
                paused=bool(
                    player._sync_voice() and player._sync_voice().is_paused()
                ),
            )

        await interaction.response.send_message(
            f"Now-playing panel will live in {channel.mention}. "
            "This is the fancy card Discord allows for bots "
            "(profile Rich Presence stays limited).",
            ephemeral=True,
        )

    @app_commands.command(
        name="testpresence",
        description="Force a Rich Presence update (admin) to verify icons/text",
    )
    async def testpresence(self, interaction: discord.Interaction) -> None:
        err = self._deny_if_not_manager(interaction)
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return

        from rich_presence import DiscordRichPresence, update_presence
        import time

        app_id = self.bot.application_id or (
            self.bot.user.id if self.bot.user else None
        )
        presence = DiscordRichPresence(
            name="Dream Team Bot",
            activity_type=discord.ActivityType.playing,
            details="Competitive",
            state="Playing Solo",
            start_timestamp=int(time.time()),
            end_timestamp=int(time.time()) + 15 * 60,
            large_image_key="dreamteam",
            large_image_text="Numbani",
            small_image_key="youtube",
            small_image_text="Rogue - Level 100",
            party_id="ae488379-351d-4a4f-ad32-2b9b01c91657",
            party_size=1,
            party_max=5,
            application_id=int(app_id) if app_id else None,
        )
        await update_presence(self.bot, presence)
        await interaction.response.send_message(
            "Presence forced.\n"
            "• Member list: usually only shows short text for bots\n"
            "• Click the bot profile to check for the activity card/icons\n"
            "• Portal Visualizer only changes when you **select** image keys "
            "in the dropdown (upload alone does nothing)",
            ephemeral=True,
        )

    @app_commands.command(
        name="syncnicks",
        description="Force a nickname sync for this server now",
    )
    async def syncnicks(self, interaction: discord.Interaction) -> None:
        err = self._deny_if_not_manager(interaction)
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        updated = 0
        rows = self.bot.db.all_real_names(guild.id)
        for row in rows:
            member = guild.get_member(row["user_id"])
            if member is None or member.bot or member.id == guild.owner_id:
                continue
            desired = build_nickname(display_base(member), row["real_name"])
            if member.nick == desired:
                continue
            try:
                await member.edit(nick=desired, reason="Manual nickname sync")
                updated += 1
                await asyncio.sleep(0.5)
            except (discord.Forbidden, discord.HTTPException):
                continue

        await interaction.followup.send(f"Synced {updated} nickname(s).", ephemeral=True)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if not config.DISCORD_TOKEN or config.DISCORD_TOKEN == "your_bot_token_here":
        raise SystemExit(
            "Missing DISCORD_TOKEN. Copy .env.example to .env and paste your bot token."
        )

    db = Database(config.DATABASE_PATH)
    log.info("Database ready at %s", config.DATABASE_PATH.resolve())
    bot = DreamTeamBot(db)
    bot.run(config.DISCORD_TOKEN, log_handler=None)


if __name__ == "__main__":
    main()
