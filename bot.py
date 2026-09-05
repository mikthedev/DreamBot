import asyncio
import logging
import subprocess
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
from names import WelcomeNameView, WelcomePrivateView, apply_real_name
from nicknames import build_nickname, display_base, is_guild_manager
from overwatch_patches import OverwatchPatchCog, OwPatchHistoryView
from overwatch_tierlist import OverwatchTierCog
from overwatch_meta import OverwatchMetaCog
from overwatch_news import OverwatchNewsCog
from overwatch_hero_history import (
    OverwatchHeroHistoryCog,
    OwHeroHistoryHubView,
    OwHubRoleSelectLegacyView,
    OwLegacyHeroHistoryHubView,
)
from onboarding import OnboardingView
from panel import PanelCog
from anniversary import AnniversaryCog
from ai import AICog
from voice_ai import VoiceAICog
from play_together import (
    PlayExpandInButton,
    PlayExpandMaybeButton,
    PlayExpandNopeButton,
    PlayRsvpView,
    PlayTogetherCog,
)
from birthday_signup import (
    BirthdaySignupView,
    OpenSignupModalView,
    SIGNUP_EMOJI,
    is_signup_message,
)

log = logging.getLogger("dream_team")


class DreamTeamBot(commands.Bot):
    def __init__(self, db: Database) -> None:
        intents = discord.Intents.default()
        intents.members = True
        intents.message_content = True
        intents.presences = config.PLAY_PRESENCE_INTENT

        # Keep the message cache small — bot-hosting free/starter plans are often 256–512 MB.
        super().__init__(
            command_prefix="!",
            intents=intents,
            max_messages=200,
            chunk_guilds_at_startup=True,
        )
        self.db = db

    async def setup_hook(self) -> None:
        # Persistent buttons (work after restarts)
        self.add_view(BirthdaySignupView())
        self.add_view(WelcomeNameView())
        self.add_view(OwPatchHistoryView())
        self.add_view(OwHeroHistoryHubView())
        # Older Search Hero Changes posts may still have Pick a role… / 3 bars
        self.add_view(OwHubRoleSelectLegacyView())
        self.add_view(OwLegacyHeroHistoryHubView())
        self.add_view(OnboardingView())
        self.add_view(PlayRsvpView())
        self.add_dynamic_items(
            PlayExpandInButton, PlayExpandMaybeButton, PlayExpandNopeButton
        )
        await self.add_cog(WelcomeCog(self))
        await self.add_cog(BirthdayCog(self))
        await self.add_cog(AnniversaryCog(self))
        await self.add_cog(MusicCog(self))
        await self.add_cog(PanelCog(self))
        await self.add_cog(OverwatchPatchCog(self))
        await self.add_cog(OverwatchTierCog(self))
        await self.add_cog(OverwatchMetaCog(self))
        await self.add_cog(OverwatchNewsCog(self))
        await self.add_cog(OverwatchHeroHistoryCog(self))
        await self.add_cog(AICog(self))
        await self.add_cog(VoiceAICog(self))
        await self.add_cog(PlayTogetherCog(self))
        if config.PLAY_PRESENCE_INTENT:
            log.info("Play together: Presence Intent on — tracking game activity")
        else:
            log.warning(
                "Play together: Presence Intent off — bot will start, but game "
                "history stays empty until you enable Presence Intent in the "
                "Developer Portal and set PLAY_PRESENCE_INTENT=1"
            )
        if config.GROQ_API_KEY:
            log.info("Free Groq Llama AI enabled (model=%s)", config.GROQ_MODEL)
        else:
            log.warning("GROQ_API_KEY not set — /ask, @mention, and /join AI disabled")
        try:
            rev = subprocess.check_output(
                ["git", "log", "-1", "--oneline"],
                cwd=config.BASE_DIR,
                text=True,
                stderr=subprocess.DEVNULL,
            ).strip()
            log.info("Running %s", rev)
        except Exception:
            log.info("Running from %s (git revision unknown)", config.BASE_DIR)

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
        from panel import DEFAULT_WELCOME, render_welcome_prompt, welcome_embed

        template = self.bot.db.get_welcome_message(member.guild.id) or DEFAULT_WELCOME
        body = render_welcome_prompt(template, member)
        card = welcome_embed(
            description=body,
            display_name=display_base(member),
            avatar_url=member.display_avatar.url,
        )
        actions = WelcomePrivateView(member.guild.id)

        channel = await self._resolve_welcome_channel(member.guild)
        if channel is not None:
            try:
                await channel.send(content=member.mention, embed=card)
            except discord.Forbidden:
                log.warning(
                    "Cannot send welcome card in guild %s", member.guild.id
                )
            except discord.HTTPException as exc:
                log.warning("Welcome card failed: %s", exc)
            else:
                await self._send_welcome_actions_dm(member, actions)
                return

        # No welcome channel — card + buttons in DM
        try:
            dm = await member.create_dm()
        except discord.HTTPException:
            log.warning("No welcome channel and cannot DM %s", member)
            return

        try:
            await dm.send(embed=card, view=actions)
        except discord.Forbidden:
            log.warning("Cannot DM welcome to %s", member)
            return

        # DM fallback: also accept a typed name reply
        def check(message: discord.Message) -> bool:
            return (
                message.author.id == member.id
                and isinstance(message.channel, discord.DMChannel)
            )

        timeout = config.NAME_PROMPT_TIMEOUT_MINUTES * 60
        try:
            reply = await self.bot.wait_for("message", check=check, timeout=timeout)
        except asyncio.TimeoutError:
            try:
                await dm.send(
                    "No name yet — an admin can set it in `/panel` → Names."
                )
            except discord.HTTPException:
                pass
            return
        await self._apply_real_name(member, reply.content.strip(), announce_in=dm)

    async def _send_welcome_actions_dm(
        self, member: discord.Member, view: WelcomePrivateView
    ) -> None:
        """Buttons only the joiner sees — Discord can't hide channel components."""
        try:
            dm = await member.create_dm()
            await dm.send(
                f"Welcome to **{member.guild.name}**!\n"
                "Only you see this — tap below to set your name "
                "(example: `Миша`).",
                view=view,
            )
        except discord.Forbidden:
            log.warning(
                "Cannot DM welcome actions to %s — "
                "they may need to allow server DMs",
                member,
            )
        except discord.HTTPException as exc:
            log.warning("Welcome actions DM failed: %s", exc)

    async def _apply_real_name(
        self,
        member: discord.Member,
        real_name: str,
        announce_in: discord.abc.Messageable | None = None,
    ) -> bool:
        ok, detail = await apply_real_name(
            self.bot,
            member,
            real_name,
            reason="Dream Team real-name nickname",
        )
        if announce_in:
            await announce_in.send(f"{member.mention} {detail}")
            if ok:
                await announce_in.send("Birthday? Reply `DD.MM`, or `skip`.")
                await self._prompt_for_birthday(member, announce_in)
        return ok

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
            member.guild.id,
            member.id,
            bday.month,
            bday.day,
            bday.year,
            set_by_admin=False,
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

        admin_setting_other = (
            member is not None and member.id != interaction.user.id
        )
        if admin_setting_other:
            self.bot.db.set_birthday(
                interaction.guild_id,
                target.id,
                bday.month,
                bday.day,
                bday.year,
                set_by_admin=True,
            )
            note = await self.announce_member_if_today(target, bday)
            text = (
                f"Saved birthday for {target.mention}: **{bday.display()}** "
                "(locked as admin-set — they can still change it if wrong)."
            )
            if note:
                text += f"\n{note}"
            await interaction.response.send_message(text, ephemeral=True)
            return

        from birthday_signup import save_own_birthday

        await save_own_birthday(interaction, bday)

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
        admin_note = ""
        try:
            if row["set_by_admin"]:
                admin_note = " _(set by an admin)_"
        except (IndexError, KeyError, TypeError):
            pass
        await interaction.response.send_message(
            f"Your birthday: **{bday.display()}**{admin_note}",
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

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        if payload.user_id == (self.bot.user.id if self.bot.user else None):
            return
        if str(payload.emoji) != SIGNUP_EMOJI:
            return
        if payload.guild_id is None:
            return

        channel = self.bot.get_channel(payload.channel_id)
        if not isinstance(channel, discord.TextChannel):
            return
        try:
            message = await channel.fetch_message(payload.message_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return

        if self.bot.user is None or not is_signup_message(message, self.bot.user.id):
            return

        guild = self.bot.get_guild(payload.guild_id)
        member = guild.get_member(payload.user_id) if guild else None
        if member is None or member.bot:
            return

        # Prefer a quiet DM with a form button; fall back to channel mention.
        view = OpenSignupModalView()
        prompt = (
            f"Hey {member.display_name}! Tap the button to add your birthday "
            f"(format `DD.MM`, example `15.03`)."
        )
        try:
            await member.send(prompt, view=view)
        except discord.Forbidden:
            try:
                await channel.send(
                    f"{member.mention} tap below to add your birthday:",
                    view=view,
                    delete_after=120,
                )
            except (discord.Forbidden, discord.HTTPException):
                pass

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


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if not config.DISCORD_TOKEN or config.DISCORD_TOKEN == "your_bot_token_here":
        raise SystemExit(
            "Missing DISCORD_TOKEN. Copy .env.example to .env and paste your bot token."
        )

    try:
        import resource

        rss_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
        log.info(
            "Cold-start RSS ~%.0f MB (host free tier often 256 MB — keep under that)",
            rss_mb,
        )
    except Exception:
        pass

    db = Database(config.DATABASE_PATH)
    log.info("Database ready at %s", config.DATABASE_PATH.resolve())
    bot = DreamTeamBot(db)
    bot.run(config.DISCORD_TOKEN, log_handler=None)


if __name__ == "__main__":
    main()
