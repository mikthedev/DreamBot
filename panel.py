"""Help + interactive admin control panel (buttons / selects)."""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

import config
from birthdays import Birthday, celebration_embed
from birthday_signup import (
    BirthdaySignupView,
    post_signup_announcement,
    signup_embed,
)
from nicknames import is_guild_manager

log = logging.getLogger("dream_team.panel")

BRAND = discord.Color.from_rgb(14, 28, 48)
ACCENT = discord.Color.from_rgb(46, 230, 166)


def birthdays_list_embed(guild: discord.Guild, bot) -> discord.Embed:
    rows = bot.db.all_birthdays(guild.id)
    embed = discord.Embed(
        title="Saved birthdays",
        color=ACCENT,
        description=f"**{len(rows)}** birthday(s) in the database for this server.",
    )
    embed.set_author(name="Dream Team")

    if not rows:
        embed.description = (
            "No birthdays saved yet.\n"
            "Members use `/setbirthday`, or admins set one with `/setbirthday` + member."
        )
        return embed

    # Sort by month, day
    def sort_key(row) -> tuple:
        return (int(row["month"]), int(row["day"]), int(row["user_id"]))

    lines: list[str] = []
    for row in sorted(rows, key=sort_key):
        bday = Birthday(month=row["month"], day=row["day"], year=row["year"])
        member = guild.get_member(row["user_id"])
        real_name = bot.db.get_real_name(guild.id, row["user_id"])
        who = member.mention if member else f"`user {row['user_id']}`"
        extra = f" · {real_name}" if real_name else ""
        lines.append(f"**{bday.display()}** — {who}{extra}")

    # Discord field value max 1024; split across fields if needed
    chunk: list[str] = []
    chunk_len = 0
    field_i = 1
    for line in lines:
        add = len(line) + 1
        if chunk and chunk_len + add > 1000:
            embed.add_field(
                name=f"List ({field_i})",
                value="\n".join(chunk),
                inline=False,
            )
            field_i += 1
            chunk = []
            chunk_len = 0
        chunk.append(line)
        chunk_len += add
    if chunk:
        name = "List" if field_i == 1 else f"List ({field_i})"
        embed.add_field(name=name, value="\n".join(chunk), inline=False)

    embed.set_footer(text="Dates shown as DD.MM (or DD.MM.YYYY if year was saved)")
    return embed


def help_embed(*, is_admin: bool) -> discord.Embed:
    embed = discord.Embed(
        title="Dream Team Bot — Help",
        description=(
            "Use slash commands, or admins can open **/panel** for buttons "
            "instead of typing everything."
        ),
        color=BRAND,
    )
    embed.set_author(name="Dream Team")
    embed.add_field(
        name="Everyone",
        value=(
            "`/help` — this guide\n"
            "`/setbirthday` — save your birthday (`DD.MM`)\n"
            "`/mybirthday` — show your birthday\n"
            "`/clearbirthday` — remove your birthday\n"
            "`/play` `/pause` `/resume` `/skip` `/queue` `/nowplaying` `/stop`"
        ),
        inline=False,
    )
    if is_admin:
        embed.add_field(
            name="Admins",
            value=(
                "`/panel` — **control panel** (recommended)\n"
                "`/birthdays` — list all saved birthdays\n"
                "`/birthdayannounce` — post signup panel for members\n"
                "`/setname` `/setwelcome` `/setautorole`\n"
                "`/setbirthdaychannel` `/setmusicchannel`\n"
                "`/syncnicks` `/testpresence`"
            ),
            inline=False,
        )
    embed.set_footer(text="Birthdays post automatically at the configured local hour")
    return embed


def panel_embed(guild: discord.Guild, bot) -> discord.Embed:
    settings = bot.db.get_settings(guild.id)
    stats = bot.db.guild_stats(guild.id)

    def ch(cid) -> str:
        if not cid:
            return "_not set_"
        channel = guild.get_channel(cid)
        return channel.mention if channel else f"`{cid}` (missing)"

    def role(rid) -> str:
        if not rid:
            return "_not set_"
        r = guild.get_role(rid)
        return r.mention if r else f"`{rid}` (missing)"

    bday_ch = settings["birthday_channel_id"] if settings else None
    welcome_ch = settings["welcome_channel_id"] if settings else None
    music_ch = settings["now_playing_channel_id"] if settings else None
    auto_role = settings["auto_role_id"] if settings else None

    embed = discord.Embed(
        title="Dream Team — Control Panel",
        description="Tap a button below. No need to memorize slash commands.",
        color=ACCENT,
    )
    embed.set_author(name=guild.name, icon_url=guild.icon.url if guild.icon else None)
    embed.add_field(name="Birthday channel", value=ch(bday_ch), inline=True)
    embed.add_field(name="Welcome channel", value=ch(welcome_ch), inline=True)
    embed.add_field(name="Music panel", value=ch(music_ch), inline=True)
    embed.add_field(name="Auto-role", value=role(auto_role), inline=True)
    embed.add_field(
        name="Database",
        value=(
            f"`{config.DATABASE_PATH.name}`\n"
            f"Birthdays saved: **{stats['birthdays']}** · "
            f"Names: **{stats['real_names']}**"
        ),
        inline=False,
    )
    embed.add_field(
        name="Birthday schedule",
        value=(
            f"Timezone **{config.BIRTHDAY_TIMEZONE}** · "
            f"announce at **{config.BIRTHDAY_ANNOUNCE_HOUR:02d}:00** local\n"
            "Setting someone’s birthday to **today** also posts immediately."
        ),
        inline=False,
    )
    embed.set_footer(text=f"DB path: {config.DATABASE_PATH}")
    return embed


class ChannelPickView(discord.ui.View):
    """Ephemeral channel picker used by the panel."""

    def __init__(self, bot, kind: str) -> None:
        super().__init__(timeout=120)
        self.bot = bot
        self.kind = kind  # birthday | welcome | music

    @discord.ui.select(
        cls=discord.ui.ChannelSelect,
        placeholder="Choose a text channel…",
        channel_types=[discord.ChannelType.text],
        min_values=1,
        max_values=1,
    )
    async def pick_channel(
        self, interaction: discord.Interaction, select: discord.ui.ChannelSelect
    ) -> None:
        if not isinstance(interaction.user, discord.Member) or not is_guild_manager(
            interaction.user
        ):
            await interaction.response.send_message("Admins only.", ephemeral=True)
            return

        selected = select.values[0] if select.values else None
        if not isinstance(selected, discord.TextChannel):
            await interaction.response.send_message(
                "Pick a text channel.", ephemeral=True
            )
            return

        guild_id = interaction.guild_id
        assert guild_id is not None

        if self.kind == "birthday":
            self.bot.db.set_birthday_channel(guild_id, selected.id)
            msg = f"Birthday messages → {selected.mention}"
        elif self.kind == "welcome":
            self.bot.db.set_welcome_channel(guild_id, selected.id)
            msg = f"Welcome prompts → {selected.mention}"
        elif self.kind == "music":
            self.bot.db.set_now_playing_panel(guild_id, selected.id, None)
            music = self.bot.get_cog("MusicCog")
            if music is not None:
                player = music.get_player(guild_id)
                await music.refresh_now_playing_panel(
                    guild_id,
                    track=player.current,
                    queue_len=len(player.queue),
                    paused=bool(
                        player._sync_voice() and player._sync_voice().is_paused()
                    ),
                )
            msg = f"Music panel → {selected.mention}"
        else:
            msg = "Unknown setting."

        await interaction.response.send_message(msg, ephemeral=True)


class RolePickView(discord.ui.View):
    def __init__(self, bot) -> None:
        super().__init__(timeout=120)
        self.bot = bot

    @discord.ui.select(
        cls=discord.ui.RoleSelect,
        placeholder="Choose auto-role…",
        min_values=1,
        max_values=1,
    )
    async def pick_role(
        self, interaction: discord.Interaction, select: discord.ui.RoleSelect
    ) -> None:
        if not isinstance(interaction.user, discord.Member) or not is_guild_manager(
            interaction.user
        ):
            await interaction.response.send_message("Admins only.", ephemeral=True)
            return
        role = select.values[0] if select.values else None
        if role is None:
            await interaction.response.send_message("Pick a role.", ephemeral=True)
            return
        self.bot.db.set_auto_role(interaction.guild_id, role.id)
        await interaction.response.send_message(
            f"New members will get {role.mention}.", ephemeral=True
        )


class AdminPanelView(discord.ui.View):
    def __init__(self, bot) -> None:
        super().__init__(timeout=300)
        self.bot = bot

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not isinstance(interaction.user, discord.Member) or not is_guild_manager(
            interaction.user
        ):
            await interaction.response.send_message(
                "Only admins can use this panel.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="Preview birthday", style=discord.ButtonStyle.primary, row=0)
    async def preview_birthday(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        member = interaction.user
        assert isinstance(member, discord.Member)
        real_name = self.bot.db.get_real_name(interaction.guild_id, member.id)
        embed = celebration_embed(
            mention=member.mention,
            real_name=real_name,
            avatar_url=member.display_avatar.url,
        )
        embed.set_footer(text="Preview only — not posted to the birthday channel")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="View birthdays", style=discord.ButtonStyle.primary, row=0)
    async def view_birthdays(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        assert interaction.guild is not None
        await interaction.response.send_message(
            embed=birthdays_list_embed(interaction.guild, self.bot),
            ephemeral=True,
        )

    @discord.ui.button(label="Preview signup", style=discord.ButtonStyle.primary, row=0)
    async def preview_signup(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await interaction.response.send_message(
            content=(
                "**Preview** — members will see this panel. "
                "Try the button to test the form."
            ),
            embed=signup_embed(preview=True),
            view=BirthdaySignupView(),
            ephemeral=True,
        )

    @discord.ui.button(
        label="Announce today's birthdays", style=discord.ButtonStyle.success, row=1
    )
    async def announce_today(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        birthday_cog = self.bot.get_cog("BirthdayCog")
        if birthday_cog is None:
            await interaction.followup.send("Birthday cog missing.", ephemeral=True)
            return
        posted, detail = await birthday_cog.announce_todays_birthdays(
            interaction.guild, force=False
        )
        await interaction.followup.send(
            f"Posted **{posted}** birthday message(s).\n{detail}",
            ephemeral=True,
        )

    @discord.ui.button(
        label="Post signup panel", style=discord.ButtonStyle.success, row=1
    )
    async def post_signup(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        assert isinstance(interaction.channel, discord.TextChannel)
        await interaction.response.defer(ephemeral=True)
        try:
            msg = await post_signup_announcement(interaction.channel)
        except discord.Forbidden:
            await interaction.followup.send(
                "I can't post in this channel.", ephemeral=True
            )
            return
        await interaction.followup.send(
            f"Posted here: {msg.jump_url}",
            ephemeral=True,
        )

    @discord.ui.button(label="Bot status", style=discord.ButtonStyle.secondary, row=1)
    async def bot_status(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        assert interaction.guild is not None
        embed = panel_embed(interaction.guild, self.bot)
        embed.title = "Dream Team — Bot status"
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="Birthday channel", style=discord.ButtonStyle.secondary, row=2)
    async def set_bday_channel(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await interaction.response.send_message(
            "Pick the channel for birthday celebrations:",
            view=ChannelPickView(self.bot, "birthday"),
            ephemeral=True,
        )

    @discord.ui.button(label="Welcome channel", style=discord.ButtonStyle.secondary, row=2)
    async def set_welcome_channel(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await interaction.response.send_message(
            "Pick the welcome / name-prompt channel:",
            view=ChannelPickView(self.bot, "welcome"),
            ephemeral=True,
        )

    @discord.ui.button(label="Music channel", style=discord.ButtonStyle.secondary, row=2)
    async def set_music_channel(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await interaction.response.send_message(
            "Pick the now-playing panel channel:",
            view=ChannelPickView(self.bot, "music"),
            ephemeral=True,
        )

    @discord.ui.button(label="Auto-role", style=discord.ButtonStyle.secondary, row=3)
    async def set_auto_role(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await interaction.response.send_message(
            "Pick the role given to new members:",
            view=RolePickView(self.bot),
            ephemeral=True,
        )


class PanelCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="help", description="How to use Dream Team Bot")
    async def help_cmd(self, interaction: discord.Interaction) -> None:
        is_admin = isinstance(interaction.user, discord.Member) and is_guild_manager(
            interaction.user
        )
        view = None
        if is_admin:
            view = discord.ui.View(timeout=120)

            async def open_panel(btn_interaction: discord.Interaction) -> None:
                if not isinstance(btn_interaction.user, discord.Member) or not is_guild_manager(
                    btn_interaction.user
                ):
                    await btn_interaction.response.send_message(
                        "Admins only.", ephemeral=True
                    )
                    return
                assert btn_interaction.guild is not None
                await btn_interaction.response.send_message(
                    embed=panel_embed(btn_interaction.guild, self.bot),
                    view=AdminPanelView(self.bot),
                    ephemeral=True,
                )

            button = discord.ui.Button(
                label="Open admin panel",
                style=discord.ButtonStyle.primary,
            )
            button.callback = open_panel
            view.add_item(button)

        await interaction.response.send_message(
            embed=help_embed(is_admin=is_admin),
            view=view,
            ephemeral=True,
        )

    @app_commands.command(
        name="panel",
        description="Admin control panel — buttons for setup & birthday testing",
    )
    async def panel_cmd(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                "Use this inside the server.", ephemeral=True
            )
            return
        if not is_guild_manager(interaction.user):
            await interaction.response.send_message(
                "Only the server owner or admins can open the panel.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            embed=panel_embed(interaction.guild, self.bot),
            view=AdminPanelView(self.bot),
            ephemeral=True,
        )

    @app_commands.command(
        name="birthdays",
        description="Admin: list all saved birthdays in this server",
    )
    async def birthdays_cmd(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                "Use this inside the server.", ephemeral=True
            )
            return
        if not is_guild_manager(interaction.user):
            await interaction.response.send_message(
                "Only the server owner or admins can view all birthdays.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            embed=birthdays_list_embed(interaction.guild, self.bot),
            ephemeral=True,
        )
