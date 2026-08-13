"""Single-message admin hub — navigate by editing one ephemeral panel."""

from __future__ import annotations

import asyncio
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
from names import SetNameModal, WelcomePrivateView, names_list_embed
from overwatch_patches import PATCH_URL, build_patch_embeds, has_hero_balance
from overwatch_tierlist import TIER_URL
from overwatch_meta import META_URL
from ow_forum import OW_CHANNEL_TYPES, is_ow_destination
from nicknames import is_guild_manager
from onboarding import (
    EditOnboardModal,
    default_copy as default_onboard_copy,
    hub_onboard_embed,
    load_copy as load_onboard_copy,
    onboard_embed,
    publish_onboard,
)
from play_together import (
    activity_embed,
    add_play_hub_controls,
    games_embed,
    hub_play_embed,
    manage_embed,
    review_embed,
    hub_search_embed,
)

log = logging.getLogger("dream_team.panel")

BRAND = discord.Color.from_rgb(14, 28, 48)
ACCENT = discord.Color.from_rgb(46, 230, 166)
MUTED = discord.Color.from_rgb(90, 110, 140)

# Short default — public card body; joiner gets Set my name privately
DEFAULT_WELCOME = (
    "Hey {mention} — welcome to **Dream Team**!\n"
    "Glad you're here — make yourself at home."
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
            "`/ask` — free Llama chat (or @mention the bot)\n"
            "`/join` · `/disconnect` — voice AI (say **Dream**, …)\n"
            "`/hero` — one hero’s balance changes across patches\n"
            "`/setbirthday` · `/mybirthday` · `/clearbirthday`\n"
            "`/play` · `/pause` · `/skip` · `/queue` · `/stop` · `/leave`"
        ),
        inline=False,
    )
    if is_admin:
        embed.add_field(
            name="Admins",
            value="`/panel` — channels, Dream AI, names, birthdays, Overwatch, play together, onboarding, anniversary",
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
            f"**Dream voice log** {_ch(guild, settings['voice_log_channel_id'] if settings else None)}\n"
            f"**Auto-role** {_role(guild, settings['auto_role_id'] if settings else None)}"
        ),
        inline=False,
    )
    return embed


def hub_dream_embed(guild: discord.Guild, bot) -> discord.Embed:
    settings = bot.db.get_settings(guild.id)
    embed = discord.Embed(
        title="Dream AI",
        description=(
            "Voice replies are also posted as text embeds.\n"
            "Pick where those go — the bot deletes them after **24 hours**.\n"
            "If unset, they post in the channel where `/join` was used."
        ),
        color=ACCENT,
    )
    embed.add_field(
        name="Voice transcript channel",
        value=_ch(guild, settings["voice_log_channel_id"] if settings else None),
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


def welcome_embed(
    *,
    description: str,
    display_name: str,
    avatar_url: str | None = None,
) -> discord.Embed:
    """Public welcome card — same layout language as birthday celebrations."""
    embed = discord.Embed(
        title="Welcome!",
        description=description,
        color=ACCENT,
    )
    embed.set_author(name="Dream Team")
    embed.add_field(name="New member", value=f"**{display_name}**", inline=True)
    if avatar_url:
        embed.set_thumbnail(url=avatar_url)
    embed.set_footer(text="Dream Team · Welcome")
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
            "Channel gets a birthday-style **Welcome!** card (no buttons).\n"
            "The joiner gets **Set my name** in a private DM.\n"
            "Use **Try welcome** to preview.\n\n"
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
    last_meta = bot.db.get_ow_meta_last_id(guild.id)
    last_meta_at = bot.db.get_ow_meta_last_posted(guild.id)
    tier_when = (
        last_tier_at.strftime("%Y-%m-%d")
        if last_tier_at
        else "_never_"
    )
    meta_when = (
        last_meta_at.strftime("%Y-%m-%d")
        if last_meta_at
        else "_never_"
    )
    news_configured = bot.db.get_ow_news_channel_configured(guild.id)
    news_ch = bot.db.get_ow_news_channel(guild.id)
    news_seeded = bot.db.is_ow_news_seeded(guild.id)
    embed = discord.Embed(
        title="Overwatch",
        description=(
            "**Patches** — [official notes]({patch_url}), checked daily. "
            "One **forum post** is updated in place (title + body) when a new "
            "patch drops. Posts tagged **Patch Notes** stay **unlocked** so "
            "history buttons work. "
            "**Previous patches** still opens archives privately.\n\n"
            "**Hero history** — one **Patch Notes** forum hub with Tank / Damage / Support "
            "menus. Pick a hero to browse every recent balance touch (`/hero` works anywhere).\n\n"
            "**Tier list** — [Counterwatch]({tier_url}), about every "
            "**{days} days**. Same single-post overwrite with hero emojis + win / pick "
            "rates (tag **META**).\n\n"
            "**Best to main** — [one-tricks]({meta_url}), same cadence. Patch-notes style "
            "cards per role with honourable mentions (tag **META**).\n\n"
            "**News / custom** — filtered Bluesky feed (auto) + **Custom post**. "
            "Set the forum below (tag **News**). Paste a **bsky.app post** link — "
            "videos are shared as links (no download); images still attach.\n\n"
            "_Pick forums below. Tags: **Patch Notes** / **META** / **News**._"
        ).format(
            patch_url=PATCH_URL,
            tier_url=TIER_URL,
            meta_url=META_URL,
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
        name="Tier / META channel",
        value=(
            f"{_ch(guild, tier_ch)}\n"
            f"Tier: {tier_when}"
            + (f" · `{last_tier}`" if last_tier else "")
            + f"\nMETA: {meta_when}"
            + (f" · `{last_meta}`" if last_meta else "")
        ),
        inline=False,
    )
    if news_configured:
        news_value = (
            f"{_ch(guild, news_configured)}\n"
            f"Seeded: {'yes' if news_seeded else 'no'} · "
            f"check every {config.OW_NEWS_CHECK_HOURS}h"
        )
    else:
        news_value = (
            f"_not set_ — custom/news fall back to {_ch(guild, news_ch)}\n"
            f"Seeded: {'yes' if news_seeded else 'no'} · "
            f"check every {config.OW_NEWS_CHECK_HOURS}h"
        )
    embed.add_field(name="News / custom channel", value=news_value, inline=False)
    embed.set_footer(
        text=(
            f"Patches every {config.OW_PATCH_CHECK_HOURS}h · "
            f"Tier/META check every {config.OW_TIER_CHECK_HOURS}h · "
            f"News every {config.OW_NEWS_CHECK_HOURS}h"
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


class CustomNewsModal(discord.ui.Modal, title="Custom news post"):
    post_title = discord.ui.TextInput(
        label="Title",
        style=discord.TextStyle.short,
        max_length=100,
        required=True,
        placeholder="First Look at New Map Rework",
    )
    body = discord.ui.TextInput(
        label="Body (optional)",
        style=discord.TextStyle.paragraph,
        max_length=1800,
        required=False,
        placeholder="Optional detail — leave empty for image/video-only.",
    )
    media_url = discord.ui.TextInput(
        label="Media URL (optional)",
        style=discord.TextStyle.short,
        max_length=300,
        required=False,
        placeholder="Bluesky post or image URL",
    )

    def __init__(self, hub: "AdminHubView") -> None:
        super().__init__()
        self.hub = hub

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("Guild only.", ephemeral=True)
            return
        cog = self.hub.bot.get_cog("OverwatchNewsCog")
        if cog is None:
            await interaction.response.send_message(
                "News cog not loaded.", ephemeral=True
            )
            return

        channel_id = (
            self.hub.bot.db.get_ow_news_channel(self.hub.guild_id)
            or self.hub.bot.db.get_ow_tier_channel(self.hub.guild_id)
            or self.hub.bot.db.get_ow_patch_channel(self.hub.guild_id)
        )
        if not channel_id:
            await interaction.response.send_message(
                "Set a news / custom forum first.", ephemeral=True
            )
            return
        channel = interaction.guild.get_channel(channel_id)
        if channel is None:
            try:
                channel = await interaction.guild.fetch_channel(channel_id)
            except discord.HTTPException:
                channel = None
        if not is_ow_destination(channel):
            await interaction.response.send_message(
                "News / custom forum missing — set it in the Overwatch panel.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        title = str(self.post_title.value).strip()
        body = str(self.body.value or "").strip()
        media = str(self.media_url.value or "").strip() or None
        try:
            result = await cog.publish_custom(
                channel,
                title=title,
                body=body,
                media_url=media,
                auto_close=True,
            )
        except Exception as exc:
            await interaction.followup.send(f"Custom post failed: {exc}", ephemeral=True)
            return
        if result is None:
            await interaction.followup.send(
                "Nothing to post — add a title/body and/or a working media URL.",
                ephemeral=True,
            )
            return
        mention = (
            result.mention
            if isinstance(result, discord.Thread)
            else channel.mention
        )
        await interaction.followup.send(
            f"Posted **{title}** → {mention}",
            ephemeral=True,
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
        elif self.page == "dream":
            self._add_dream_controls()
        elif self.page == "birthdays":
            self._add_birthday_controls()
        elif self.page == "names":
            self._add_names_controls()
        elif self.page == "welcome":
            self._add_welcome_controls()
        elif self.page == "overwatch":
            self._add_overwatch_controls()
        elif self.page in (
            "play",
            "play_games",
            "play_game_search",
            "play_activity",
            "play_review",
            "play_manage",
        ):
            add_play_hub_controls(self)
        elif self.page == "onboard":
            self._add_onboard_controls()
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
                    label="Dream AI",
                    value="dream",
                    description="Voice transcript channel (24h auto-delete)",
                    emoji="🎤",
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
                    label="Play together",
                    value="play",
                    description="Game nights from shared activity",
                    emoji="🎲",
                ),
                discord.SelectOption(
                    label="Онбординг",
                    value="onboard",
                    description="UA welcome panel with buttons",
                    emoji="🚪",
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
        if self.page == "dream":
            return hub_dream_embed(guild, self.bot)
        if self.page == "birthdays":
            return hub_birthdays_embed(guild, self.bot)
        if self.page == "names":
            return names_list_embed(guild, self.bot)
        if self.page == "welcome":
            return hub_welcome_embed(guild, self.bot)
        if self.page == "overwatch":
            return hub_overwatch_embed(guild, self.bot)
        if self.page == "play":
            return hub_play_embed(guild, self.bot)
        if self.page == "play_games":
            return games_embed(
                guild, self.bot, selected=getattr(self, "play_game_key", None)
            )
        if self.page == "play_game_search":
            return hub_search_embed(self)
        if self.page == "play_activity":
            return activity_embed(guild, self.bot)
        if self.page == "play_review":
            return review_embed(guild, self.bot)
        if self.page == "play_manage":
            sid = getattr(self, "play_suggestion_id", None)
            if sid:
                return manage_embed(guild, self.bot, sid)
            return hub_play_embed(guild, self.bot)
        if self.page == "onboard":
            return hub_onboard_embed(guild, self.bot)
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

    def _add_dream_controls(self) -> None:
        select = discord.ui.ChannelSelect(
            placeholder="Set Dream voice transcript channel…",
            channel_types=[discord.ChannelType.text],
            min_values=1,
            max_values=1,
            row=1,
        )

        async def on_pick(interaction: discord.Interaction) -> None:
            if not await self._admin_ok(interaction):
                return
            selected = select.values[0]
            channel_id = getattr(selected, "id", None)
            if channel_id is None:
                await interaction.response.send_message(
                    "Could not read that channel.", ephemeral=True
                )
                return
            self.bot.db.set_voice_log_channel(self.guild_id, int(channel_id))
            self._rebuild()
            await interaction.response.edit_message(
                embed=self.embed_for(interaction.guild),
                view=self,
            )

        select.callback = on_pick
        self.add_item(select)

    def _add_birthday_controls(self) -> None:
        async def view_list(interaction: discord.Interaction) -> None:
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

        async def preview_card(interaction: discord.Interaction) -> None:
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

        async def announce(interaction: discord.Interaction) -> None:
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

        async def signup_composer(interaction: discord.Interaction) -> None:
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

        sync_btn = discord.ui.Button(
            label="Sync nicknames", style=discord.ButtonStyle.primary, row=2
        )

        async def on_sync_nicks(interaction: discord.Interaction) -> None:
            if not await self._admin_ok(interaction):
                return
            await interaction.response.defer(ephemeral=True)
            guild = interaction.guild
            assert guild is not None
            from nicknames import build_nickname, display_base

            updated = 0
            for row in self.bot.db.all_real_names(guild.id):
                member = guild.get_member(row["user_id"])
                if member is None or member.bot or member.id == guild.owner_id:
                    continue
                desired = build_nickname(display_base(member), row["real_name"])
                if member.nick == desired:
                    continue
                try:
                    await member.edit(nick=desired, reason="Panel nickname sync")
                    updated += 1
                    await asyncio.sleep(0.5)
                except (discord.Forbidden, discord.HTTPException):
                    continue
            await interaction.followup.send(
                f"Synced **{updated}** nickname(s).", ephemeral=True
            )

        sync_btn.callback = on_sync_nicks
        self.add_item(sync_btn)

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

        def _welcome_card(member: discord.Member) -> discord.Embed:
            from nicknames import display_base

            template = self.bot.db.get_welcome_message(self.guild_id) or DEFAULT_WELCOME
            body = render_welcome_prompt(template, member)
            return welcome_embed(
                description=body,
                display_name=display_base(member),
                avatar_url=member.display_avatar.url,
            )

        async def on_try_private(interaction: discord.Interaction) -> None:
            if not await self._admin_ok(interaction):
                return
            assert isinstance(interaction.user, discord.Member)
            card = _welcome_card(interaction.user)
            await interaction.response.send_message(
                content=(
                    "**Test welcome** — only you see this.\n"
                    "Public channel would show the card only; "
                    "the joiner gets **Set my name** in a DM."
                ),
                embed=card,
                view=WelcomePrivateView(self.guild_id),
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
            card = _welcome_card(interaction.user)
            try:
                await channel.send(
                    content=interaction.user.mention,
                    embed=card,
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
                value=(
                    f"Public card in {channel.mention} (no buttons). "
                    "A real join also DMs **Set my name** to the member."
                ),
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
            placeholder="Set patch notes forum…",
            channel_types=list(OW_CHANNEL_TYPES),
            min_values=1,
            max_values=1,
            row=1,
        )
        tier_pick = discord.ui.ChannelSelect(
            placeholder="Set tier / META forum…",
            channel_types=list(OW_CHANNEL_TYPES),
            min_values=1,
            max_values=1,
            row=2,
        )
        news_pick = discord.ui.ChannelSelect(
            placeholder="Set news / custom forum…",
            channel_types=list(OW_CHANNEL_TYPES),
            min_values=1,
            max_values=1,
            row=3,
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
            self.bot.db.set_ow_patch_thread_id(self.guild_id, None)
            self.bot.db.set_ow_hero_history_thread_id(self.guild_id, None)
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
            self.bot.db.set_ow_tier_thread_id(self.guild_id, None)
            self.bot.db.set_ow_meta_thread_id(self.guild_id, None)
            # Start the 2-week clock so enabling doesn't instantly dump a post
            self.bot.db.touch_ow_tier_schedule(self.guild_id)
            self.bot.db.touch_ow_meta_schedule(self.guild_id)
            embed = hub_overwatch_embed(interaction.guild, self.bot)
            embed.add_field(
                name="Done",
                value=(
                    f"Tier / META channel set to {mention}.\n"
                    f"Auto-posts about every **{config.OW_TIER_INTERVAL_DAYS} days** "
                    "from now. Use **Post tier list** / **Post META** immediately."
                ),
                inline=False,
            )
            self._rebuild()
            await interaction.response.edit_message(embed=embed, view=self)

        async def on_news_channel(interaction: discord.Interaction) -> None:
            if not await self._admin_ok(interaction):
                return
            selected = news_pick.values[0]
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
            self.bot.db.set_ow_news_channel(self.guild_id, int(channel_id))
            embed = hub_overwatch_embed(interaction.guild, self.bot)
            embed.add_field(
                name="Done",
                value=(
                    f"News / custom forum set to {mention}.\n"
                    "Bluesky news and **Custom post** go here (tag **News**)."
                ),
                inline=False,
            )
            self._rebuild()
            await interaction.response.edit_message(embed=embed, view=self)

        patch_pick.callback = on_patch_channel
        tier_pick.callback = on_tier_channel
        news_pick.callback = on_news_channel
        self.add_item(patch_pick)
        self.add_item(tier_pick)
        self.add_item(news_pick)

        # Row 4: five publish buttons (select can't share a row with buttons)
        post_patch = discord.ui.Button(
            label="Post patch",
            style=discord.ButtonStyle.success,
            row=4,
        )
        post_hero = discord.ui.Button(
            label="Hero history",
            style=discord.ButtonStyle.success,
            row=4,
        )
        post_tier = discord.ui.Button(
            label="Post tier",
            style=discord.ButtonStyle.success,
            row=4,
        )
        post_meta = discord.ui.Button(
            label="Post META",
            style=discord.ButtonStyle.success,
            row=4,
        )
        custom_news = discord.ui.Button(
            label="Custom post",
            style=discord.ButtonStyle.primary,
            row=4,
        )

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
            if not is_ow_destination(channel):
                await interaction.response.send_message(
                    "Patch channel missing — pick a forum (or text) channel again.",
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
            if not has_hero_balance(summary):
                await interaction.followup.send(
                    "Latest notes have no retail hero balance — nothing posted.",
                    ephemeral=True,
                )
                return
            await cog.publish_live(channel, summary)
            kind = (
                "forum post (edited in place)"
                if isinstance(channel, discord.ForumChannel)
                else "channel"
            )
            embed = hub_overwatch_embed(interaction.guild, self.bot)
            embed.add_field(
                name="Posted",
                value=(
                    f"**{summary.title}** → {channel.mention}\n"
                    f"_(Live {kind}; reactions allowed, no comments.)_"
                ),
                inline=False,
            )
            self._rebuild()
            await interaction.edit_original_response(embed=embed, view=self)

        async def on_post_hero_history(interaction: discord.Interaction) -> None:
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
            if not is_ow_destination(channel):
                await interaction.response.send_message(
                    "Patch channel missing — pick a forum (or text) channel again.",
                    ephemeral=True,
                )
                return
            cog = self.bot.get_cog("OverwatchHeroHistoryCog")
            if cog is None:
                await interaction.response.send_message(
                    "Hero history cog not loaded.", ephemeral=True
                )
                return
            await interaction.response.defer()
            try:
                await cog.publish_hub(channel)
            except Exception as exc:
                await interaction.followup.send(
                    f"Hero history post failed: {exc}", ephemeral=True
                )
                return
            kind = (
                "forum post (edited in place)"
                if isinstance(channel, discord.ForumChannel)
                else "channel"
            )
            embed = hub_overwatch_embed(interaction.guild, self.bot)
            embed.add_field(
                name="Posted",
                value=(
                    f"**Search Hero Changes** → {channel.mention}\n"
                    f"_(Live {kind}; tag **Patch Notes**; pick a role, then a hero.)_"
                ),
                inline=False,
            )
            self._rebuild()
            await interaction.edit_original_response(embed=embed, view=self)

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
            if not is_ow_destination(channel):
                await interaction.response.send_message(
                    "Tier-list channel missing — pick a forum (or text) channel again.",
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
            kind = (
                "forum post (edited in place)"
                if isinstance(channel, discord.ForumChannel)
                else "channel"
            )
            embed = hub_overwatch_embed(interaction.guild, self.bot)
            embed.add_field(
                name="Posted",
                value=(
                    f"**{summary.title}** → {channel.mention}\n"
                    f"_(Live {kind}; reactions allowed, no comments.)_"
                ),
                inline=False,
            )
            self._rebuild()
            await interaction.edit_original_response(embed=embed, view=self)

        async def on_post_meta(interaction: discord.Interaction) -> None:
            if not await self._admin_ok(interaction):
                return
            channel_id = (
                self.bot.db.get_ow_tier_channel(self.guild_id)
                or self.bot.db.get_ow_patch_channel(self.guild_id)
            )
            if not channel_id:
                await interaction.response.send_message(
                    "Set a tier / META forum first.", ephemeral=True
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
            if not is_ow_destination(channel):
                await interaction.response.send_message(
                    "META channel missing — pick the tier forum again.",
                    ephemeral=True,
                )
                return
            cog = self.bot.get_cog("OverwatchMetaCog")
            if cog is None:
                await interaction.response.send_message(
                    "META cog not loaded.", ephemeral=True
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
                    "Could not parse the Counterwatch one-tricks page.",
                    ephemeral=True,
                )
                return
            await cog.publish_live(channel, summary)
            kind = (
                "forum post (edited in place)"
                if isinstance(channel, discord.ForumChannel)
                else "channel"
            )
            embed = hub_overwatch_embed(interaction.guild, self.bot)
            embed.add_field(
                name="Posted",
                value=(
                    f"**{summary.title}** → {channel.mention}\n"
                    f"_(Live META {kind}; tag META / 🎮.)_"
                ),
                inline=False,
            )
            self._rebuild()
            await interaction.edit_original_response(embed=embed, view=self)

        async def on_post_news(interaction: discord.Interaction) -> None:
            if not await self._admin_ok(interaction):
                return
            cog = self.bot.get_cog("OverwatchNewsCog")
            if cog is None:
                await interaction.response.send_message(
                    "News cog not loaded.", ephemeral=True
                )
                return
            if interaction.guild is None:
                await interaction.response.send_message(
                    "Guild only.", ephemeral=True
                )
                return
            if not self.bot.db.get_ow_news_channel(self.guild_id):
                await interaction.response.send_message(
                    "Set a news / custom forum first.", ephemeral=True
                )
                return
            await interaction.response.defer()
            self.bot.db.set_ow_news_seeded(self.guild_id, False)
            try:
                _n, detail = await cog.seed_day_old(interaction.guild)
            except Exception as exc:
                await interaction.followup.send(f"News failed: {exc}", ephemeral=True)
                return
            embed = hub_overwatch_embed(interaction.guild, self.bot)
            embed.add_field(name="News", value=detail, inline=False)
            self._rebuild()
            await interaction.edit_original_response(embed=embed, view=self)

        async def on_custom_news(interaction: discord.Interaction) -> None:
            if not await self._admin_ok(interaction):
                return
            if not self.bot.db.get_ow_news_channel(self.guild_id):
                await interaction.response.send_message(
                    "Set a news / custom forum first.", ephemeral=True
                )
                return
            await interaction.response.send_modal(CustomNewsModal(self))

        post_patch.callback = on_post_patch
        post_hero.callback = on_post_hero_history
        post_tier.callback = on_post_tier
        post_meta.callback = on_post_meta
        custom_news.callback = on_custom_news
        self.add_item(post_patch)
        self.add_item(post_hero)
        self.add_item(post_tier)
        self.add_item(post_meta)
        self.add_item(custom_news)

    def _add_onboard_controls(self) -> None:
        ch_select = discord.ui.ChannelSelect(
            placeholder="Канал онбордингу…",
            channel_types=[discord.ChannelType.text],
            min_values=1,
            max_values=1,
            row=1,
        )

        async def on_channel(interaction: discord.Interaction) -> None:
            if not await self._admin_ok(interaction):
                return
            selected = ch_select.values[0]
            channel_id = getattr(selected, "id", None)
            if channel_id is None:
                await interaction.response.send_message(
                    "Could not read that channel.", ephemeral=True
                )
                return
            self.bot.db.set_onboard_channel(self.guild_id, int(channel_id))
            self._rebuild()
            await interaction.response.edit_message(
                embed=self.embed_for(interaction.guild),
                view=self,
            )

        ch_select.callback = on_channel
        self.add_item(ch_select)

        role_select = discord.ui.RoleSelect(
            placeholder="Роль Overwatch (патчі / тір)…",
            min_values=1,
            max_values=1,
            row=2,
        )

        async def on_role(interaction: discord.Interaction) -> None:
            if not await self._admin_ok(interaction):
                return
            role = role_select.values[0]
            self.bot.db.set_ow_broadcast_role(self.guild_id, role.id)
            self._rebuild()
            await interaction.response.edit_message(
                embed=self.embed_for(interaction.guild),
                view=self,
            )

        role_select.callback = on_role
        self.add_item(role_select)

        async def edit_text(interaction: discord.Interaction) -> None:
            if not await self._admin_ok(interaction):
                return
            copy = load_onboard_copy(self.bot, self.guild_id)
            await interaction.response.send_modal(EditOnboardModal(self, copy))

        async def preview(interaction: discord.Interaction) -> None:
            if not await self._admin_ok(interaction):
                return
            copy = load_onboard_copy(self.bot, self.guild_id)
            await interaction.response.send_message(
                embed=onboard_embed(copy, preview=True, guild=interaction.guild),
                ephemeral=True,
            )

        async def publish(interaction: discord.Interaction) -> None:
            if not await self._admin_ok(interaction):
                return
            await interaction.response.defer(ephemeral=True)
            msg, detail = await publish_onboard(self.bot, interaction.guild)
            embed = hub_onboard_embed(interaction.guild, self.bot)
            embed.add_field(name="Результат", value=detail, inline=False)
            self.page = "onboard"
            self._rebuild()
            await interaction.edit_original_response(embed=embed, view=self)
            if msg is None:
                await interaction.followup.send(detail, ephemeral=True)

        async def reset_text(interaction: discord.Interaction) -> None:
            if not await self._admin_ok(interaction):
                return
            base = default_onboard_copy()
            self.bot.db.set_onboard_copy(
                self.guild_id, title=base.title, body=base.body
            )
            self._rebuild()
            await interaction.response.edit_message(
                embed=self.embed_for(interaction.guild),
                view=self,
            )

        for label, style, cb, row in (
            ("Редагувати текст", discord.ButtonStyle.primary, edit_text, 3),
            ("Прев’ю", discord.ButtonStyle.secondary, preview, 3),
            ("Опублікувати / оновити", discord.ButtonStyle.success, publish, 4),
            ("Скинути текст", discord.ButtonStyle.danger, reset_text, 4),
        ):
            btn = discord.ui.Button(label=label, style=style, row=row)
            btn.callback = cb
            self.add_item(btn)

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
        description="Admin hub — setup, birthdays, play together, Overwatch",
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


# Keep old name used nowhere critical
panel_embed = hub_home_embed
