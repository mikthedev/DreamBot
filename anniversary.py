"""Dream Team server anniversary — founded 28 June 2017."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import commands, tasks

import config
from nicknames import is_guild_manager

log = logging.getLogger("dream_team.anniversary")

FOUNDED = date(2017, 6, 28)
ACCENT = discord.Color.from_rgb(245, 196, 88)
BRAND = discord.Color.from_rgb(14, 28, 48)

DEFAULT_TITLE = "Happy Anniversary, Dream Team!"
DEFAULT_BODY = (
    "On **28.06.2017** this server was born.\n\n"
    "Today we celebrate **{years} years** of Dream Team — "
    "friendship, chaos, and good vibes.\n\n"
    "Here's to many more."
)
DEFAULT_FOOTER = "Dream Team · Server anniversary"


@dataclass
class AnniversaryCopy:
    title: str
    body: str
    footer: str


def default_copy() -> AnniversaryCopy:
    return AnniversaryCopy(
        title=DEFAULT_TITLE, body=DEFAULT_BODY, footer=DEFAULT_FOOTER
    )


def years_since_founding(on: date | None = None) -> int:
    on = on or date.today()
    return max(0, on.year - FOUNDED.year)


def render_template(text: str, *, on: date | None = None) -> str:
    on = on or date.today()
    return (
        text.replace("{years}", str(years_since_founding(on)))
        .replace("{year}", str(on.year))
        .replace("{founded}", "28.06.2017")
    )


def load_copy(bot, guild_id: int) -> AnniversaryCopy:
    raw = bot.db.get_anniversary_copy(guild_id)
    base = default_copy()
    return AnniversaryCopy(
        title=(raw["title"] or base.title).strip() or base.title,
        body=(raw["body"] or base.body).strip() or base.body,
        footer=(raw["footer"] or base.footer).strip() or base.footer,
    )


def anniversary_embed(
    copy: AnniversaryCopy,
    *,
    guild: discord.Guild | None = None,
    preview: bool = False,
    on: date | None = None,
) -> discord.Embed:
    on = on or date.today()
    years = years_since_founding(on)
    embed = discord.Embed(
        title=render_template(copy.title, on=on),
        description=render_template(copy.body, on=on),
        color=ACCENT,
        timestamp=datetime(on.year, FOUNDED.month, FOUNDED.day),
    )
    if guild is not None:
        embed.set_author(
            name=guild.name,
            icon_url=guild.icon.url if guild.icon else None,
        )
        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
    else:
        embed.set_author(name="Dream Team")

    embed.add_field(name="Founded", value="**28.06.2017**", inline=True)
    embed.add_field(name="Years", value=f"**{years}**", inline=True)
    footer = render_template(copy.footer, on=on)
    if preview:
        footer = f"PREVIEW · {footer}"
    embed.set_footer(text=footer)
    return embed


def is_anniversary_day(today: date) -> bool:
    return today.month == FOUNDED.month and today.day == FOUNDED.day


class EditAnniversaryModal(discord.ui.Modal, title="Edit anniversary message"):
    title_input = discord.ui.TextInput(label="Title", max_length=100, required=True)
    body_input = discord.ui.TextInput(
        label="Body — use {years} {founded} {year}",
        style=discord.TextStyle.paragraph,
        max_length=1800,
        required=True,
    )
    footer_input = discord.ui.TextInput(
        label="Footer (optional)", max_length=100, required=False
    )

    def __init__(self, copy: AnniversaryCopy, composer: "AnniversaryComposerView") -> None:
        super().__init__()
        self.composer = composer
        self.title_input.default = copy.title[:100]
        self.body_input.default = copy.body[:1800]
        self.footer_input.default = (copy.footer or "")[:100]

    async def on_submit(self, interaction: discord.Interaction) -> None:
        copy = AnniversaryCopy(
            title=str(self.title_input.value).strip() or DEFAULT_TITLE,
            body=str(self.body_input.value).strip() or DEFAULT_BODY,
            footer=str(self.footer_input.value).strip() or DEFAULT_FOOTER,
        )
        self.composer.copy = copy
        assert interaction.guild_id is not None
        interaction.client.db.set_anniversary_copy(
            interaction.guild_id,
            title=copy.title,
            body=copy.body,
            footer=copy.footer,
        )
        await interaction.response.edit_message(
            embed=anniversary_embed(
                copy, guild=interaction.guild, preview=True
            ),
            view=self.composer,
        )
        await interaction.followup.send(
            "Anniversary text saved. Placeholders: `{years}` `{founded}` `{year}`.",
            ephemeral=True,
        )


class AnniversaryComposerView(discord.ui.View):
    def __init__(
        self,
        bot,
        copy: AnniversaryCopy,
        *,
        target_channel: discord.TextChannel | None,
        preview_only: bool,
    ) -> None:
        super().__init__(timeout=600)
        self.bot = bot
        self.copy = copy
        self.target_channel = target_channel
        self.preview_only = preview_only

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not isinstance(interaction.user, discord.Member) or not is_guild_manager(
            interaction.user
        ):
            await interaction.response.send_message("Admins only.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Edit text", style=discord.ButtonStyle.primary, row=0)
    async def edit_text(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await interaction.response.send_modal(
            EditAnniversaryModal(self.copy, self)
        )

    @discord.ui.button(label="Refresh preview", style=discord.ButtonStyle.secondary, row=0)
    async def refresh(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await interaction.response.edit_message(
            embed=anniversary_embed(
                self.copy, guild=interaction.guild, preview=True
            ),
            view=self,
        )

    @discord.ui.button(
        label="Post now", style=discord.ButtonStyle.success, row=0
    )
    async def post_now(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if self.preview_only:
            await interaction.response.send_message(
                "Preview mode — use **Post anniversary** / `/anniversarypost` to publish.",
                ephemeral=True,
            )
            return
        channel = self.target_channel
        if channel is None and isinstance(interaction.channel, discord.TextChannel):
            channel = interaction.channel
        if channel is None:
            await interaction.response.send_message(
                "No text channel to post in.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)
        cog = self.bot.get_cog("AnniversaryCog")
        if cog is None:
            await interaction.followup.send("Anniversary cog missing.", ephemeral=True)
            return
        ok, detail = await cog.post_anniversary(
            interaction.guild, channel=channel, force=True
        )
        await interaction.followup.send(
            ("Posted." if ok else "Not posted.") + f" {detail}",
            ephemeral=True,
        )


async def open_anniversary_composer(
    interaction: discord.Interaction,
    bot,
    *,
    channel: discord.TextChannel | None,
    preview_only: bool,
) -> None:
    assert interaction.guild is not None
    copy = load_copy(bot, interaction.guild.id)
    view = AnniversaryComposerView(
        bot, copy, target_channel=channel, preview_only=preview_only
    )
    header = (
        "**Anniversary preview** — founded **28.06.2017**. "
        "Edit text (use `{years}`, `{founded}`, `{year}`). Nothing posted yet.\n"
        if preview_only
        else (
            f"**Post anniversary** — target: "
            f"{channel.mention if channel else 'this channel'}\n"
            "Edit if needed, then **Post now**."
        )
    )
    await interaction.response.send_message(
        content=header,
        embed=anniversary_embed(copy, guild=interaction.guild, preview=True),
        view=view,
        ephemeral=True,
    )


class AnniversaryCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.check_anniversary.start()

    def cog_unload(self) -> None:
        self.check_anniversary.cancel()

    def _local_now(self) -> datetime:
        try:
            tz = ZoneInfo(config.BIRTHDAY_TIMEZONE)
        except Exception:
            tz = ZoneInfo("UTC")
        return datetime.now(tz)

    async def _announce_channel(
        self, guild: discord.Guild
    ) -> discord.TextChannel | None:
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

    async def post_anniversary(
        self,
        guild: discord.Guild,
        *,
        channel: discord.TextChannel | None = None,
        force: bool = False,
    ) -> tuple[bool, str]:
        today = self._local_now().date()
        if not force and not is_anniversary_day(today):
            return False, "Today is not 28 June."

        year = today.year
        if not force and self.bot.db.was_anniversary_announced(guild.id, year):
            return False, f"Already announced for {year}."

        target = channel or await self._announce_channel(guild)
        if target is None:
            return False, "No birthday/welcome channel set (use `/panel`)."

        copy = load_copy(self.bot, guild.id)
        embed = anniversary_embed(copy, guild=guild, on=today)
        try:
            await target.send(embed=embed)
            self.bot.db.mark_anniversary_announced(guild.id, year)
            return True, f"Posted in {target.mention} ({years_since_founding(today)} years)."
        except discord.Forbidden:
            return False, f"Cannot post in {target.mention}."
        except discord.HTTPException as exc:
            return False, f"Discord error: {exc}"

    @tasks.loop(hours=1)
    async def check_anniversary(self) -> None:
        now = self._local_now()
        if now.hour != config.BIRTHDAY_ANNOUNCE_HOUR:
            return
        if not is_anniversary_day(now.date()):
            return
        for guild in self.bot.guilds:
            ok, detail = await self.post_anniversary(guild, force=False)
            if ok:
                log.info("Anniversary in %s: %s", guild.name, detail)

    @check_anniversary.before_loop
    async def before_check(self) -> None:
        await self.bot.wait_until_ready()

    @app_commands.command(
        name="anniversarypreview",
        description="Admin: preview/edit the yearly Dream Team anniversary message",
    )
    async def anniversarypreview(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                "Use this inside the server.", ephemeral=True
            )
            return
        if not is_guild_manager(interaction.user):
            await interaction.response.send_message("Admins only.", ephemeral=True)
            return
        await open_anniversary_composer(
            interaction, self.bot, channel=None, preview_only=True
        )

    @app_commands.command(
        name="anniversarypost",
        description="Admin: compose & post the anniversary announcement (also for testing)",
    )
    @app_commands.describe(channel="Where to post (default: this channel)")
    async def anniversarypost(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel | None = None,
    ) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                "Use this inside the server.", ephemeral=True
            )
            return
        if not is_guild_manager(interaction.user):
            await interaction.response.send_message("Admins only.", ephemeral=True)
            return

        target = channel
        if target is None and isinstance(interaction.channel, discord.TextChannel):
            target = interaction.channel
        await open_anniversary_composer(
            interaction, self.bot, channel=target, preview_only=False
        )
