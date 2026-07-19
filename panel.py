"""Single-message admin hub — navigate by editing one ephemeral panel."""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

import config
from anniversary import (
    AnniversaryComposerView,
    anniversary_embed,
    load_copy as load_anniversary_copy,
)
from birthday_signup import (
    SignupComposerView,
    load_copy as load_signup_copy,
    signup_embed,
)
from birthdays import Birthday, celebration_embed
from names import SetNameModal, WelcomeNameView, names_list_embed
from overwatch_patches import PATCH_URL, build_patch_embeds
from overwatch_tierlist import TIER_URL
from nicknames import is_guild_manager

log = logging.getLogger("dream_team.panel")

BRAND = discord.Color.from_rgb(14, 28, 48)
ACCENT = discord.Color.from_rgb(46, 230, 166)
MUTED = discord.Color.from_rgb(90, 110, 140)

# Short default — members tap the button; admins edit names in /panel → Names
DEFAULT_WELCOME = (
    "Hey {mention} — welcome to **Dream Team**!\n"
    "Tap **Set my name** below (example: `Миша`)."
)


def birthdays_list_embed(guild: discord.Guild, bot) -> discord.Embed:
    rows = bot.db.all_birthdays(guild.id)
    embed = discord.Embed(
        title="Saved birthdays",
        color=ACCENT,
        description=f"**{len(rows)}** saved for this server.",
    )
    embed.set_author(name="Dream Team")
    if not rows:
        embed.description = "No birthdays yet."
        return embed

    lines: list[str] = []
    for row in sorted(rows, key=lambda r: (int(r["month"]), int(r["day"]))):
        bday = Birthday(month=row["month"], day=row["day"], year=row["year"])
        member = guild.get_member(row["user_id"])
        real_name = bot.db.get_real_name(guild.id, row["user_id"])
        who = member.mention if member else f"`{row['user_id']}`"
        extra = f" · {real_name}" if real_name else ""
        admin_tag = ""
        try:
            if row["set_by_admin"]:
                admin_tag = " · _admin_"
        except (IndexError, KeyError, TypeError):
            pass
        lines.append(f"**{bday.display()}** — {who}{extra}{admin_tag}")

    chunk: list[str] = []
    size = 0
    field_i = 1
    for line in lines:
        if chunk and size + len(line) + 1 > 1000:
            embed.add_field(
                name="—" if field_i == 1 else f"— ({field_i})",
                value="\n".join(chunk),
                inline=False,
            )
            field_i += 1
            chunk, size = [], 0
        chunk.append(line)
        size += len(line) + 1
    if chunk:
        embed.add_field(
            name="—" if field_i == 1 else f"— ({field_i})",
            value="\n".join(chunk),
            inline=False,
        )
    return embed


def help_embed(*, is_admin: bool) -> discord.Embed:
    embed = discord.Embed(
        title="Dream Team Bot",
        description="Tap a slash command, or open the admin hub if you manage the server.",
        color=BRAND,
    )
    embed.add_field(
        name="Everyone",
        value=(
            "`/help`\n"
            "`/setbirthday` · `/mybirthday` · `/clearbirthday`\n"
            "`/play` · `/pause` · `/skip` · `/queue` · `/stop`"
        ),
        inline=False,
    )
    if is_admin:
        embed.add_field(
            name="Admins",
            value="`/panel` — setup, names, birthdays, Overwatch patches, welcome",
            inline=False,
        )
    return embed


def _ch(guild: discord.Guild, cid) -> str:
    if not cid:
        return "_not set_"
    channel = guild.get_channel(cid)
    return channel.mention if channel else "_missing_"


def _role(guild: discord.Guild, rid) -> str:
    if not rid:
        return "_not set_"
    role = guild.get_role(rid)
    return role.mention if role else "_missing_"


def hub_home_embed(guild: discord.Guild, bot) -> discord.Embed:
    settings = bot.db.get_settings(guild.id)
    stats = bot.db.guild_stats(guild.id)
    embed = discord.Embed(
        title="Control panel",
        description=(
            "Everything stays in **this message**.\n"
            "Pick a section below — no extra pop-ups."
        ),
        color=BRAND,
    )
    embed.set_author(name=guild.name, icon_url=guild.icon.url if guild.icon else None)
    embed.add_field(
        name="Quick status",
        value=(
            f"Birthdays **{stats['birthdays']}** · Names **{stats['real_names']}**\n"
            f"Welcome {_ch(guild, settings['welcome_channel_id'] if settings else None)}\n"
            f"Birthday {_ch(guild, settings['birthday_channel_id'] if settings else None)}"
        ),
        inline=False,
    )
    embed.set_footer(text="Dream Team · Admin hub")
    return embed


def hub_channels_embed(guild: discord.Guild, bot) -> discord.Embed:
    settings = bot.db.get_settings(guild.id)
    embed = discord.Embed(
        title="Channels & role",
        description="Use the menus under this message to set each one.",
        color=ACCENT,
    )
    embed.add_field(
        name="Current",
        value=(
            f"**Welcome** {_ch(guild, settings['welcome_channel_id'] if settings else None)}\n"
            f"**Birthday** {_ch(guild, settings['birthday_channel_id'] if settings else None)}\n"
            f"**Music** {_ch(guild, settings['now_playing_channel_id'] if settings else None)}\n"
            f"**Auto-role** {_role(guild, settings['auto_role_id'] if settings else None)}"
        ),
        inline=False,
    )
    return embed


def hub_birthdays_embed(guild: discord.Guild, bot) -> discord.Embed:
    stats = bot.db.guild_stats(guild.id)
    embed = discord.Embed(
        title="Birthdays",
        description=(
            f"**{stats['birthdays']}** saved.\n\n"
            "Use the buttons below — results replace this panel."
        ),
        color=ACCENT,
    )
    return embed


def hub_welcome_embed(guild: discord.Guild, bot) -> discord.Embed:
    text = bot.db.get_welcome_message(guild.id) or DEFAULT_WELCOME
    preview = (
        text.replace("{mention}", "@Member")
        .replace("{display}", "DiscordName")
        .replace("{example_nick}", "DiscordName (YourName)")
    )
    embed = discord.Embed(
        title="Welcome message",
        description=(
            "Shown when someone joins, with a **Set my name** button.\n"
            "Use **Try welcome** to see it as a member would — privately or in this channel.\n\n"
            f"**Placeholders:** `{{mention}}` `{{display}}` `{{example_nick}}`"
        ),
        color=MUTED,
    )
    embed.add_field(name="Preview", value=preview[:1024], inline=False)
    return embed


def hub_overwatch_embed(guild: discord.Guild, bot) -> discord.Embed:
    patch_ch = bot.db.get_ow_patch_channel(guild.id)
    last_patch = bot.db.latest_ow_patch_id(guild.id)
    tier_ch = bot.db.get_ow_tier_channel(guild.id)
    last_tier = bot.db.get_ow_tier_last_id(guild.id)
    last_tier_at = bot.db.get_ow_tier_last_posted(guild.id)
    tier_when = (
        last_tier_at.strftime("%Y-%m-%d")
        if last_tier_at
        else "_never_"
    )
    embed = discord.Embed(
        title="Overwatch",
        description=(
            "**Patches** — [official notes]({patch_url}), checked daily. "
            "One live post (old deleted on new patch); **Previous patches** is ephemeral.\n\n"
            "**Tier list** — [Counterwatch]({tier_url}), about every "
            "**{days} days**. Small **app emojis** + win rate / pick rate "
            "(first post may take a minute while icons upload)."
        ).format(
            patch_url=PATCH_URL,
            tier_url=TIER_URL,
            days=config.OW_TIER_INTERVAL_DAYS,
        ),
        color=discord.Color.from_rgb(249, 158, 26),
    )
    embed.add_field(
        name="Patch channel",
        value=f"{_ch(guild, patch_ch)}\nLast: `{last_patch}`" if last_patch else _ch(guild, patch_ch),
        inline=False,
    )
    embed.add_field(
        name="Tier-list channel",
        value=(
            f"{_ch(guild, tier_ch)}\n"
            f"Last post: {tier_when}"
            + (f" · `{last_tier}`" if last_tier else "")
        ),
        inline=False,
    )
    embed.set_footer(
        text=(
            f"Patches every {config.OW_PATCH_CHECK_HOURS}h · "
            f"Tier check every {config.OW_TIER_CHECK_HOURS}h"
        )
    )
    return embed


def hub_status_embed(guild: discord.Guild, bot) -> discord.Embed:
    settings = bot.db.get_settings(guild.id)
    stats = bot.db.guild_stats(guild.id)
    embed = discord.Embed(title="Bot status", color=BRAND)
    embed.add_field(
        name="Database",
        value=(
            f"`{config.DATABASE_PATH.name}`\n"
            f"Birthdays **{stats['birthdays']}** · "
            f"Names **{stats['real_names']}**"
        ),
        inline=False,
    )
    embed.add_field(
        name="Schedule",
        value=(
            f"**{config.BIRTHDAY_TIMEZONE}** · "
            f"hour **{config.BIRTHDAY_ANNOUNCE_HOUR:02d}:00**\n"
            "Anniversary each **28.06** (founded 2017)"
        ),
        inline=False,
    )
    embed.add_field(
        name="Channels",
        value=(
            f"Welcome {_ch(guild, settings['welcome_channel_id'] if settings else None)}\n"
            f"Birthday {_ch(guild, settings['birthday_channel_id'] if settings else None)}\n"
            f"Music {_ch(guild, settings['now_playing_channel_id'] if settings else None)}"
        ),
        inline=False,
    )
    embed.set_footer(text=str(config.DATABASE_PATH))
    return embed


def render_welcome_prompt(template: str, member: discord.Member) -> str:
    from nicknames import display_base

    display = display_base(member)
    return (
        template.replace("{mention}", member.mention)
        .replace("{display}", display)
        .replace("{example_nick}", f"{display} (YourName)")
    )


class EditWelcomeModal(discord.ui.Modal, title="Edit welcome message"):
    body = discord.ui.TextInput(
        label="Welcome text",
        style=discord.TextStyle.paragraph,
        max_length=800,
        required=True,
    )

    def __init__(self, hub: "AdminHubView") -> None:
        super().__init__()
        self.hub = hub
        current = hub.bot.db.get_welcome_message(hub.guild_id) or DEFAULT_WELCOME
        self.body.default = current[:800]

    async def on_submit(self, interaction: discord.Interaction) -> None:
        text = str(self.body.value).strip() or DEFAULT_WELCOME
        self.hub.bot.db.set_welcome_message(self.hub.guild_id, text)
        self.hub.page = "welcome"
        await interaction.response.edit_message(
            embed=hub_welcome_embed(interaction.guild, self.hub.bot),
            view=self.hub,
        )


class AdminHubView(discord.ui.View):
    """One ephemeral message; sections swap via edit_message."""

    def __init__(self, bot, guild_id: int, page: str = "home") -> None:
        super().__init__(timeout=600)
        self.bot = bot
        self.guild_id = guild_id
        self.page = page
        self._rebuild()

    def _rebuild(self) -> None:
        self.clear_items()
        self.add_item(self._nav_select())
        if self.page == "channels":
            self._add_channel_controls()
        elif self.page == "birthdays":
            self._add_birthday_controls()
        elif self.page == "names":
            self._add_names_controls()
        elif self.page == "welcome":
            self._add_welcome_controls()
        elif self.page == "overwatch":
            self._add_overwatch_controls()
        elif self.page == "anniversary":
            self._add_anniversary_controls()

    def _nav_select(self) -> discord.ui.Select:
        select = discord.ui.Select(
            placeholder="Go to section…",
            options=[
                discord.SelectOption(
                    label="Home", value="home", description="Overview", emoji="🏠"
                ),
                discord.SelectOption(
                    label="Channels & role",
                    value="channels",
                    description="Welcome, birthday, music, auto-role",
                    emoji="#️⃣",
                ),
                discord.SelectOption(
                    label="Birthdays",
                    value="birthdays",
                    description="List, announce, signup panel",
                    emoji="🎂",
                ),
                discord.SelectOption(
                    label="Names",
                    value="names",
                    description="Set someone's real name quickly",
                    emoji="✏️",
                ),
                discord.SelectOption(
                    label="Welcome text",
                    value="welcome",
                    description="Edit the join prompt",
                    emoji="👋",
                ),
                discord.SelectOption(
                    label="Overwatch",
                    value="overwatch",
                    description="Patches & Counterwatch tier list",
                    emoji="🎯",
                ),
                discord.SelectOption(
                    label="Anniversary",
                    value="anniversary",
                    description="28 June server anniversary",
                    emoji="🎉",
                ),
                discord.SelectOption(
                    label="Status",
                    value="status",
                    description="Database & schedule",
                    emoji="ℹ️",
                ),
            ],
            row=0,
        )

        async def on_nav(interaction: discord.Interaction) -> None:
            if not await self._admin_ok(interaction):
                return
            self.page = select.values[0]
            self._rebuild()
            await interaction.response.edit_message(
                content=None,
                embed=self.embed_for(interaction.guild),
                view=self,
            )

        select.callback = on_nav
        return select

    def embed_for(self, guild: discord.Guild) -> discord.Embed:
        if self.page == "channels":
            return hub_channels_embed(guild, self.bot)
        if self.page == "birthdays":
            return hub_birthdays_embed(guild, self.bot)
        if self.page == "names":
            return names_list_embed(guild, self.bot)
        if self.page == "welcome":
            return hub_welcome_embed(guild, self.bot)
        if self.page == "overwatch":
            return hub_overwatch_embed(guild, self.bot)
        if self.page == "anniversary":
            copy = load_anniversary_copy(self.bot, guild.id)
            embed = anniversary_embed(copy, guild=guild, preview=True)
            embed.title = "Anniversary"
            embed.description = (
                (embed.description or "")
                + "\n\n_Edit or post with the buttons below._"
            )
            return embed
        if self.page == "status":
            return hub_status_embed(guild, self.bot)
        if self.page == "bday_list":
            return birthdays_list_embed(guild, self.bot)
        return hub_home_embed(guild, self.bot)

    async def _admin_ok(self, interaction: discord.Interaction) -> bool:
        if not isinstance(interaction.user, discord.Member) or not is_guild_manager(
            interaction.user
        ):
            await interaction.response.send_message("Admins only.", ephemeral=True)
            return False
        return True

    def _add_channel_controls(self) -> None:
        for kind, placeholder, row in (
            ("welcome", "Set welcome channel…", 1),
            ("birthday", "Set birthday channel…", 2),
            ("music", "Set music panel channel…", 3),
        ):
            select = discord.ui.ChannelSelect(
                placeholder=placeholder,
                channel_types=[discord.ChannelType.text],
                min_values=1,
                max_values=1,
                row=row,
            )

            async def on_pick(
                interaction: discord.Interaction,
                select=select,
                kind=kind,
            ) -> None:
                if not await self._admin_ok(interaction):
                    return
                selected = select.values[0]
                channel_id = getattr(selected, "id", None)
                if channel_id is None:
                    await interaction.response.send_message(
                        "Could not read that channel.", ephemeral=True
                    )
                    return
                channel_id = int(channel_id)
                if kind == "welcome":
                    self.bot.db.set_welcome_channel(self.guild_id, channel_id)
                elif kind == "birthday":
                    self.bot.db.set_birthday_channel(self.guild_id, channel_id)
                else:
                    self.bot.db.set_now_playing_panel(self.guild_id, channel_id, None)
                    music = self.bot.get_cog("MusicCog")
                    if music is not None:
                        player = music.get_player(self.guild_id)
                        await music.refresh_now_playing_panel(
                            self.guild_id,
                            track=player.current,
                            queue_len=len(player.queue),
                            paused=bool(
                                player._sync_voice()
                                and player._sync_voice().is_paused()
                            ),
                        )
                self._rebuild()
                await interaction.response.edit_message(
                    embed=self.embed_for(interaction.guild),
                    view=self,
                )

            select.callback = on_pick
            self.add_item(select)

        role_select = discord.ui.RoleSelect(
            placeholder="Set auto-role…",
            min_values=1,
            max_values=1,
            row=4,
        )

        async def on_role(interaction: discord.Interaction) -> None:
            if not await self._admin_ok(interaction):
                return
            role = role_select.values[0]
            self.bot.db.set_auto_role(self.guild_id, role.id)
            self._rebuild()
            await interaction.response.edit_message(
                embed=self.embed_for(interaction.guild),
                view=self,
            )

        role_select.callback = on_role
        self.add_item(role_select)

    def _add_birthday_controls(self) -> None:
        async def view_list(
            interaction: discord.Interaction, button: discord.ui.Button
        ) -> None:
            if not await self._admin_ok(interaction):
                return
            self.page = "bday_list"
            self._rebuild()
            # list page only has nav + back via nav home
            self.clear_items()
            self.add_item(self._nav_select())
            back = discord.ui.Button(label="Back to birthdays", style=discord.ButtonStyle.secondary, row=1)

            async def go_back(inter: discord.Interaction) -> None:
                if not await self._admin_ok(inter):
                    return
                self.page = "birthdays"
                self._rebuild()
                await inter.response.edit_message(
                    embed=self.embed_for(inter.guild), view=self
                )

            back.callback = go_back
            self.add_item(back)
            await interaction.response.edit_message(
                embed=birthdays_list_embed(interaction.guild, self.bot),
                view=self,
            )

        async def preview_card(
            interaction: discord.Interaction, button: discord.ui.Button
        ) -> None:
            if not await self._admin_ok(interaction):
                return
            member = interaction.user
            assert isinstance(member, discord.Member)
            real_name = self.bot.db.get_real_name(self.guild_id, member.id)
            embed = celebration_embed(
                mention=member.mention,
                real_name=real_name,
                avatar_url=member.display_avatar.url,
            )
            embed.set_footer(text="Preview · use the menu to go back")
            self.clear_items()
            self.add_item(self._nav_select())
            await interaction.response.edit_message(embed=embed, view=self)

        async def announce(
            interaction: discord.Interaction, button: discord.ui.Button
        ) -> None:
            if not await self._admin_ok(interaction):
                return
            await interaction.response.defer()
            cog = self.bot.get_cog("BirthdayCog")
            if cog is None:
                return
            posted, detail = await cog.announce_todays_birthdays(
                interaction.guild, force=False
            )
            embed = hub_birthdays_embed(interaction.guild, self.bot)
            embed.add_field(
                name="Result",
                value=f"Posted **{posted}**.\n{detail}",
                inline=False,
            )
            self.page = "birthdays"
            self._rebuild()
            await interaction.edit_original_response(embed=embed, view=self)

        async def signup_composer(
            interaction: discord.Interaction, button: discord.ui.Button
        ) -> None:
            if not await self._admin_ok(interaction):
                return
            channel = (
                interaction.channel
                if isinstance(interaction.channel, discord.TextChannel)
                else None
            )
            copy = load_signup_copy(self.bot, self.guild_id)
            view = SignupComposerView(
                self.bot, copy, target_channel=channel, preview_only=False
            )
            # Add back into hub
            back = discord.ui.Button(label="← Hub", style=discord.ButtonStyle.secondary, row=3)

            async def to_hub(inter: discord.Interaction) -> None:
                if not await self._admin_ok(inter):
                    return
                hub = AdminHubView(self.bot, self.guild_id, page="birthdays")
                await inter.response.edit_message(
                    content=None,
                    embed=hub.embed_for(inter.guild),
                    view=hub,
                )

            back.callback = to_hub
            view.add_item(back)
            await interaction.response.edit_message(
                content=(
                    f"**Signup composer** · target: "
                    f"{channel.mention if channel else '_pick a text channel_'}\n"
                    "Edit text & pings here, then **Post announcement**."
                ),
                embed=signup_embed(copy, preview=True, guild=interaction.guild),
                view=view,
            )

        for label, style, cb, row in (
            ("View list", discord.ButtonStyle.primary, view_list, 1),
            ("Preview card", discord.ButtonStyle.secondary, preview_card, 1),
            ("Announce today", discord.ButtonStyle.success, announce, 2),
            ("Signup panel…", discord.ButtonStyle.primary, signup_composer, 2),
        ):
            btn = discord.ui.Button(label=label, style=style, row=row)
            btn.callback = cb
            self.add_item(btn)

    def _add_names_controls(self) -> None:
        picker = discord.ui.UserSelect(
            placeholder="Pick a member to set their name…",
            min_values=1,
            max_values=1,
            row=1,
        )

        async def on_pick(interaction: discord.Interaction) -> None:
            if not await self._admin_ok(interaction):
                return
            user = picker.values[0]
            if not isinstance(user, discord.Member):
                member = interaction.guild.get_member(user.id) if interaction.guild else None
            else:
                member = user
            if member is None or member.bot:
                await interaction.response.send_message(
                    "Pick a real server member (not a bot).",
                    ephemeral=True,
                )
                return
            await interaction.response.send_modal(
                SetNameModal(self.bot, member, hub=self)
            )

        picker.callback = on_pick
        self.add_item(picker)

        refresh = discord.ui.Button(
            label="Refresh list", style=discord.ButtonStyle.secondary, row=2
        )

        async def on_refresh(interaction: discord.Interaction) -> None:
            if not await self._admin_ok(interaction):
                return
            await interaction.response.edit_message(
                embed=names_list_embed(interaction.guild, self.bot),
                view=self,
            )

        refresh.callback = on_refresh
        self.add_item(refresh)

    def _add_welcome_controls(self) -> None:
        edit = discord.ui.Button(
            label="Edit text", style=discord.ButtonStyle.primary, row=1
        )
        reset = discord.ui.Button(
            label="Reset default", style=discord.ButtonStyle.secondary, row=1
        )
        try_private = discord.ui.Button(
            label="Try welcome (only you)",
            style=discord.ButtonStyle.success,
            row=2,
        )
        try_channel = discord.ui.Button(
            label="Post test in channel",
            style=discord.ButtonStyle.secondary,
            row=2,
        )

        async def on_edit(interaction: discord.Interaction) -> None:
            if not await self._admin_ok(interaction):
                return
            await interaction.response.send_modal(EditWelcomeModal(self))

        async def on_reset(interaction: discord.Interaction) -> None:
            if not await self._admin_ok(interaction):
                return
            self.bot.db.set_welcome_message(self.guild_id, DEFAULT_WELCOME)
            await interaction.response.edit_message(
                embed=hub_welcome_embed(interaction.guild, self.bot),
                view=self,
            )

        async def _welcome_body(member: discord.Member) -> str:
            template = self.bot.db.get_welcome_message(self.guild_id) or DEFAULT_WELCOME
            return render_welcome_prompt(template, member)

        async def on_try_private(interaction: discord.Interaction) -> None:
            if not await self._admin_ok(interaction):
                return
            assert isinstance(interaction.user, discord.Member)
            body = await _welcome_body(interaction.user)
            await interaction.response.send_message(
                content=(
                    "**Test welcome** — only you see this.\n"
                    "Tap **Set my name** to try the full flow.\n\n"
                    f"{body}"
                ),
                view=WelcomeNameView(),
                ephemeral=True,
            )

        async def on_try_channel(interaction: discord.Interaction) -> None:
            if not await self._admin_ok(interaction):
                return
            assert isinstance(interaction.user, discord.Member)
            channel = interaction.channel
            if not isinstance(channel, discord.TextChannel):
                await interaction.response.send_message(
                    "Run `/panel` in a text channel to post a test there.",
                    ephemeral=True,
                )
                return
            body = await _welcome_body(interaction.user)
            try:
                await channel.send(
                    content=(
                        f"🧪 **Test welcome** (by {interaction.user.mention})\n\n"
                        f"{body}"
                    ),
                    view=WelcomeNameView(),
                )
            except discord.Forbidden:
                await interaction.response.send_message(
                    "I can't post in this channel.",
                    ephemeral=True,
                )
                return

            embed = hub_welcome_embed(interaction.guild, self.bot)
            embed.add_field(
                name="Test posted",
                value=f"Check {channel.mention} — same look as a real join.",
                inline=False,
            )
            await interaction.response.edit_message(embed=embed, view=self)

        edit.callback = on_edit
        reset.callback = on_reset
        try_private.callback = on_try_private
        try_channel.callback = on_try_channel
        self.add_item(edit)
        self.add_item(reset)
        self.add_item(try_private)
        self.add_item(try_channel)

    def _add_overwatch_controls(self) -> None:
        patch_pick = discord.ui.ChannelSelect(
            placeholder="Set patch notes channel…",
            channel_types=[discord.ChannelType.text],
            min_values=1,
            max_values=1,
            row=1,
        )
        tier_pick = discord.ui.ChannelSelect(
            placeholder="Set tier-list channel…",
            channel_types=[discord.ChannelType.text],
            min_values=1,
            max_values=1,
            row=2,
        )

        async def on_patch_channel(interaction: discord.Interaction) -> None:
            if not await self._admin_ok(interaction):
                return
            selected = patch_pick.values[0]
            channel_id = getattr(selected, "id", None)
            if channel_id is None:
                await interaction.response.send_message(
                    "Could not read that channel.", ephemeral=True
                )
                return
            channel = (
                interaction.guild.get_channel(channel_id)
                if interaction.guild
                else None
            )
            mention = (
                channel.mention
                if isinstance(channel, discord.abc.GuildChannel)
                else f"<#{channel_id}>"
            )
            self.bot.db.set_ow_patch_channel(self.guild_id, int(channel_id))
            cog = self.bot.get_cog("OverwatchPatchCog")
            note = f"Patch channel set to {mention}."
            if cog is not None:
                try:
                    await interaction.response.defer()
                    summary = await cog.get_summary()
                    if summary is not None:
                        self.bot.db.mark_ow_patch_announced(
                            self.guild_id, summary.fingerprint
                        )
                        note += (
                            f"\nCurrent patch `{summary.fingerprint}` marked as seen "
                            "(won't auto-post). Use **Post patch** to announce it."
                        )
                    embed = hub_overwatch_embed(interaction.guild, self.bot)
                    embed.add_field(name="Done", value=note, inline=False)
                    self._rebuild()
                    await interaction.edit_original_response(embed=embed, view=self)
                    return
                except Exception as exc:
                    note += f"\n_(Could not seed current patch: {exc})_"
            embed = hub_overwatch_embed(interaction.guild, self.bot)
            embed.add_field(name="Done", value=note, inline=False)
            self._rebuild()
            if interaction.response.is_done():
                await interaction.edit_original_response(embed=embed, view=self)
            else:
                await interaction.response.edit_message(embed=embed, view=self)

        async def on_tier_channel(interaction: discord.Interaction) -> None:
            if not await self._admin_ok(interaction):
                return
            selected = tier_pick.values[0]
            channel_id = getattr(selected, "id", None)
            if channel_id is None:
                await interaction.response.send_message(
                    "Could not read that channel.", ephemeral=True
                )
                return
            channel = (
                interaction.guild.get_channel(channel_id)
                if interaction.guild
                else None
            )
            mention = (
                channel.mention
                if isinstance(channel, discord.abc.GuildChannel)
                else f"<#{channel_id}>"
            )
            self.bot.db.set_ow_tier_channel(self.guild_id, int(channel_id))
            # Start the 2-week clock so enabling doesn't instantly dump a post
            self.bot.db.touch_ow_tier_schedule(self.guild_id)
            embed = hub_overwatch_embed(interaction.guild, self.bot)
            embed.add_field(
                name="Done",
                value=(
                    f"Tier-list channel set to {mention}.\n"
                    f"Auto-posts about every **{config.OW_TIER_INTERVAL_DAYS} days** "
                    "from now. Use **Post tier list** to announce immediately."
                ),
                inline=False,
            )
            self._rebuild()
            await interaction.response.edit_message(embed=embed, view=self)

        patch_pick.callback = on_patch_channel
        tier_pick.callback = on_tier_channel
        self.add_item(patch_pick)
        self.add_item(tier_pick)

        preview_patch = discord.ui.Button(
            label="Preview patch",
            style=discord.ButtonStyle.primary,
            row=3,
        )
        post_patch = discord.ui.Button(
            label="Post patch",
            style=discord.ButtonStyle.success,
            row=3,
        )
        preview_tier = discord.ui.Button(
            label="Preview tier list",
            style=discord.ButtonStyle.primary,
            row=4,
        )
        post_tier = discord.ui.Button(
            label="Post tier list",
            style=discord.ButtonStyle.success,
            row=4,
        )

        async def on_preview_patch(interaction: discord.Interaction) -> None:
            if not await self._admin_ok(interaction):
                return
            cog = self.bot.get_cog("OverwatchPatchCog")
            if cog is None:
                await interaction.response.send_message(
                    "Overwatch patch cog not loaded.", ephemeral=True
                )
                return
            await interaction.response.defer(ephemeral=True)
            try:
                await cog.send_preview_ephemeral(interaction)
            except Exception as exc:
                await interaction.followup.send(f"Fetch failed: {exc}", ephemeral=True)

        async def on_post_patch(interaction: discord.Interaction) -> None:
            if not await self._admin_ok(interaction):
                return
            channel_id = self.bot.db.get_ow_patch_channel(self.guild_id)
            if not channel_id:
                await interaction.response.send_message(
                    "Set a patch channel first.", ephemeral=True
                )
                return
            channel = (
                interaction.guild.get_channel(channel_id) if interaction.guild else None
            )
            if channel is None and interaction.guild is not None:
                try:
                    channel = await interaction.guild.fetch_channel(channel_id)
                except discord.HTTPException:
                    channel = None
            if not isinstance(channel, discord.TextChannel):
                await interaction.response.send_message(
                    "Patch channel missing — pick it again in the dropdown.",
                    ephemeral=True,
                )
                return
            cog = self.bot.get_cog("OverwatchPatchCog")
            if cog is None:
                await interaction.response.send_message(
                    "Overwatch patch cog not loaded.", ephemeral=True
                )
                return
            await interaction.response.defer()
            try:
                summary = await cog.get_summary()
            except Exception as exc:
                await interaction.followup.send(f"Fetch failed: {exc}", ephemeral=True)
                return
            if summary is None:
                await interaction.followup.send(
                    "Could not parse the patch notes page.", ephemeral=True
                )
                return
            await cog.publish_live(channel, summary)
            embed = hub_overwatch_embed(interaction.guild, self.bot)
            embed.add_field(
                name="Posted",
                value=(
                    f"**{summary.title}** → {channel.mention}\n"
                    "_(Replaced any previous live patch post.)_"
                ),
                inline=False,
            )
            self._rebuild()
            await interaction.edit_original_response(embed=embed, view=self)

        async def on_preview_tier(interaction: discord.Interaction) -> None:
            if not await self._admin_ok(interaction):
                return
            cog = self.bot.get_cog("OverwatchTierCog")
            if cog is None:
                await interaction.response.send_message(
                    "Tier-list cog not loaded.", ephemeral=True
                )
                return
            await interaction.response.defer(ephemeral=True)
            try:
                await cog.send_preview_ephemeral(interaction)
            except Exception as exc:
                await interaction.followup.send(f"Fetch failed: {exc}", ephemeral=True)

        async def on_post_tier(interaction: discord.Interaction) -> None:
            if not await self._admin_ok(interaction):
                return
            channel_id = self.bot.db.get_ow_tier_channel(self.guild_id)
            if not channel_id:
                await interaction.response.send_message(
                    "Set a tier-list channel first.", ephemeral=True
                )
                return
            channel = (
                interaction.guild.get_channel(channel_id) if interaction.guild else None
            )
            if channel is None and interaction.guild is not None:
                try:
                    channel = await interaction.guild.fetch_channel(channel_id)
                except discord.HTTPException:
                    channel = None
            if not isinstance(channel, discord.TextChannel):
                await interaction.response.send_message(
                    "Tier-list channel missing — pick it again in the dropdown.",
                    ephemeral=True,
                )
                return
            cog = self.bot.get_cog("OverwatchTierCog")
            if cog is None:
                await interaction.response.send_message(
                    "Tier-list cog not loaded.", ephemeral=True
                )
                return
            await interaction.response.defer()
            try:
                summary = await cog.get_summary()
            except Exception as exc:
                await interaction.followup.send(f"Fetch failed: {exc}", ephemeral=True)
                return
            if summary is None:
                await interaction.followup.send(
                    "Could not parse the Counterwatch tier list.", ephemeral=True
                )
                return
            await cog.publish_live(channel, summary)
            embed = hub_overwatch_embed(interaction.guild, self.bot)
            embed.add_field(
                name="Posted",
                value=(
                    f"**{summary.title}** → {channel.mention}\n"
                    "_(Replaced any previous live tier-list post.)_"
                ),
                inline=False,
            )
            self._rebuild()
            await interaction.edit_original_response(embed=embed, view=self)

        preview_patch.callback = on_preview_patch
        post_patch.callback = on_post_patch
        preview_tier.callback = on_preview_tier
        post_tier.callback = on_post_tier
        self.add_item(preview_patch)
        self.add_item(post_patch)
        self.add_item(preview_tier)
        self.add_item(post_tier)

    def _add_anniversary_controls(self) -> None:
        async def open_composer(
            interaction: discord.Interaction,
            *,
            preview_only: bool,
        ) -> None:
            if not await self._admin_ok(interaction):
                return
            channel = (
                interaction.channel
                if isinstance(interaction.channel, discord.TextChannel)
                else None
            )
            copy = load_anniversary_copy(self.bot, self.guild_id)
            view = AnniversaryComposerView(
                self.bot,
                copy,
                target_channel=channel,
                preview_only=preview_only,
            )
            back = discord.ui.Button(label="← Hub", style=discord.ButtonStyle.secondary, row=1)

            async def to_hub(inter: discord.Interaction) -> None:
                if not await self._admin_ok(inter):
                    return
                hub = AdminHubView(self.bot, self.guild_id, page="anniversary")
                await inter.response.edit_message(
                    content=None,
                    embed=hub.embed_for(inter.guild),
                    view=hub,
                )

            back.callback = to_hub
            view.add_item(back)
            await interaction.response.edit_message(
                content=(
                    "**Anniversary** · founded **28.06.2017**\n"
                    "Placeholders: `{years}` `{founded}` `{year}`"
                ),
                embed=anniversary_embed(copy, guild=interaction.guild, preview=True),
                view=view,
            )

        preview_btn = discord.ui.Button(
            label="Edit / preview", style=discord.ButtonStyle.primary, row=1
        )
        post_btn = discord.ui.Button(
            label="Compose & post", style=discord.ButtonStyle.success, row=1
        )

        async def p(inter: discord.Interaction) -> None:
            await open_composer(inter, preview_only=True)

        async def post(inter: discord.Interaction) -> None:
            await open_composer(inter, preview_only=False)

        preview_btn.callback = p
        post_btn.callback = post
        self.add_item(preview_btn)
        self.add_item(post_btn)


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
            button = discord.ui.Button(
                label="Open control panel",
                style=discord.ButtonStyle.primary,
            )

            async def open_panel(btn_interaction: discord.Interaction) -> None:
                if not isinstance(btn_interaction.user, discord.Member) or not is_guild_manager(
                    btn_interaction.user
                ):
                    await btn_interaction.response.send_message(
                        "Admins only.", ephemeral=True
                    )
                    return
                assert btn_interaction.guild is not None
                hub = AdminHubView(self.bot, btn_interaction.guild.id)
                await btn_interaction.response.send_message(
                    embed=hub.embed_for(btn_interaction.guild),
                    view=hub,
                    ephemeral=True,
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
        description="Admin hub — setup, birthdays, welcome text, anniversary",
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

        hub = AdminHubView(self.bot, interaction.guild.id)
        await interaction.response.send_message(
            embed=hub.embed_for(interaction.guild),
            view=hub,
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


# Keep old name used nowhere critical
panel_embed = hub_home_embed
