"""Public birthday signup announcement — editable, fancy embed, mentions."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import discord

from birthdays import parse_birthday, Birthday
from nicknames import is_guild_manager

log = logging.getLogger("dream_team.birthday_signup")

SIGNUP_EMOJI = "🎂"
SIGNUP_BUTTON_ID = "dreamteam:birthday_signup"
ACCENT = discord.Color.from_rgb(46, 230, 166)
GOLD = discord.Color.from_rgb(245, 196, 88)

DEFAULT_TITLE = "Dream Team Birthdays"
DEFAULT_BODY = (
    "We love celebrating our people.\n\n"
    "Add your birthday so the squad can wish you well on your day.\n\n"
    f"**Tap** **Add my birthday** below — or react with {SIGNUP_EMOJI}.\n"
    "Use `DD.MM` (example `15.03`) or `DD.MM.YYYY`."
)
DEFAULT_FOOTER = "Saved only for this server · Dream Team"


@dataclass
class SignupCopy:
    title: str
    body: str
    footer: str


def default_copy() -> SignupCopy:
    return SignupCopy(title=DEFAULT_TITLE, body=DEFAULT_BODY, footer=DEFAULT_FOOTER)


def load_copy(bot, guild_id: int) -> SignupCopy:
    raw = bot.db.get_birthday_signup_copy(guild_id)
    base = default_copy()
    return SignupCopy(
        title=(raw["title"] or base.title).strip() or base.title,
        body=(raw["body"] or base.body).strip() or base.body,
        footer=(raw["footer"] or base.footer).strip() or base.footer,
    )


def signup_embed(
    copy: SignupCopy,
    *,
    preview: bool = False,
    guild: discord.Guild | None = None,
) -> discord.Embed:
    embed = discord.Embed(
        title=f"{SIGNUP_EMOJI}  {copy.title}",
        description=copy.body,
        color=GOLD if preview else ACCENT,
    )
    if guild is not None:
        embed.set_author(
            name=guild.name,
            icon_url=guild.icon.url if guild.icon else None,
        )
    else:
        embed.set_author(name="Dream Team")

    embed.add_field(
        name="How it works",
        value=(
            f"1️⃣ Click **Add my birthday**\n"
            f"2️⃣ Type your date\n"
            f"3️⃣ Done — or react {SIGNUP_EMOJI} for a private form"
        ),
        inline=True,
    )
    embed.add_field(
        name="Format",
        value="`15.03`\n`15.03.2001`",
        inline=True,
    )
    footer = copy.footer
    if preview:
        footer = f"PREVIEW · {footer}"
    embed.set_footer(text=footer)
    if guild is not None and guild.icon:
        embed.set_thumbnail(url=guild.icon.url)
    return embed


class BirthdaySignupModal(discord.ui.Modal, title="Add your birthday"):
    date_input = discord.ui.TextInput(
        label="Your birthday",
        placeholder="15.03  or  15.03.2001",
        min_length=3,
        max_length=12,
        required=True,
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                "Use this inside the server.", ephemeral=True
            )
            return

        bday = parse_birthday(str(self.date_input.value))
        if bday is None:
            await interaction.response.send_message(
                "Invalid date. Use `DD.MM` or `DD.MM.YYYY` (example: `15.03`).",
                ephemeral=True,
            )
            return

        await save_own_birthday(interaction, bday)


def _row_admin_locked(row) -> bool:
    if row is None:
        return False
    try:
        return bool(row["set_by_admin"])
    except (IndexError, KeyError, TypeError):
        return False


def _birthday_from_row(row) -> Birthday:
    return Birthday(month=row["month"], day=row["day"], year=row["year"])


class ConfirmReplaceAdminBirthdayView(discord.ui.View):
    """User confirms replacing an admin-set birthday."""

    def __init__(self, pending: Birthday) -> None:
        super().__init__(timeout=180)
        self.pending = pending

    @discord.ui.button(
        label="Yes, change my birthday",
        style=discord.ButtonStyle.danger,
    )
    async def confirm(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                "Use this inside the server.", ephemeral=True
            )
            return

        bot = interaction.client
        bot.db.set_birthday(
            interaction.guild_id,
            interaction.user.id,
            self.pending.month,
            self.pending.day,
            self.pending.year,
            set_by_admin=False,
        )
        note = None
        birthday_cog = bot.get_cog("BirthdayCog")
        if birthday_cog is not None:
            note = await birthday_cog.announce_member_if_today(
                interaction.user, self.pending
            )
        text = (
            f"Updated — your birthday is now **{self.pending.display()}** "
            "(no longer locked to the admin entry)."
        )
        if note:
            text += f"\n{note}"
        for child in self.children:
            child.disabled = True  # type: ignore[union-attr]
        await interaction.response.edit_message(content=text, view=self)

    @discord.ui.button(label="Keep admin date", style=discord.ButtonStyle.secondary)
    async def cancel(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        for child in self.children:
            child.disabled = True  # type: ignore[union-attr]
        await interaction.response.edit_message(
            content="Okay — kept the birthday set by an admin.",
            view=self,
        )


async def save_own_birthday(interaction: discord.Interaction, bday: Birthday) -> None:
    """Save the interacting user's birthday, respecting admin locks."""
    if interaction.guild is None or not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message(
            "Use this inside the server.", ephemeral=True
        )
        return

    bot = interaction.client
    existing = bot.db.get_birthday(interaction.guild_id, interaction.user.id)
    if _row_admin_locked(existing):
        current = _birthday_from_row(existing)
        if (
            current.month == bday.month
            and current.day == bday.day
            and current.year == bday.year
        ):
            await interaction.response.send_message(
                f"Your birthday is already **{current.display()}** "
                "(set by an admin).",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            (
                f"Your birthday was added by an admin as **{current.display()}**.\n"
                f"You entered **{bday.display()}**.\n\n"
                "If the admin date is wrong, you can change it:"
            ),
            view=ConfirmReplaceAdminBirthdayView(bday),
            ephemeral=True,
        )
        return

    bot.db.set_birthday(
        interaction.guild_id,
        interaction.user.id,
        bday.month,
        bday.day,
        bday.year,
        set_by_admin=False,
    )
    note = None
    birthday_cog = bot.get_cog("BirthdayCog")
    if birthday_cog is not None:
        note = await birthday_cog.announce_member_if_today(interaction.user, bday)

    text = f"Saved — your birthday is **{bday.display()}**. Thanks!"
    if note:
        text += f"\n{note}"
    await interaction.response.send_message(text, ephemeral=True)


class BirthdaySignupView(discord.ui.View):
    """Persistent view — survives bot restarts (registered in setup_hook)."""

    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Add my birthday",
        style=discord.ButtonStyle.primary,
        emoji=SIGNUP_EMOJI,
        custom_id=SIGNUP_BUTTON_ID,
    )
    async def add_birthday(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await interaction.response.send_modal(BirthdaySignupModal())


class OpenSignupModalView(discord.ui.View):
    """Short-lived DM/channel follow-up after a 🎂 reaction."""

    def __init__(self) -> None:
        super().__init__(timeout=300)

    @discord.ui.button(
        label="Open birthday form",
        style=discord.ButtonStyle.primary,
        emoji=SIGNUP_EMOJI,
    )
    async def open_form(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await interaction.response.send_modal(BirthdaySignupModal())


class EditSignupCopyModal(discord.ui.Modal, title="Edit signup announcement"):
    title_input = discord.ui.TextInput(
        label="Title",
        max_length=100,
        required=True,
    )
    body_input = discord.ui.TextInput(
        label="Message body",
        style=discord.TextStyle.paragraph,
        max_length=1800,
        required=True,
    )
    footer_input = discord.ui.TextInput(
        label="Footer (optional)",
        max_length=100,
        required=False,
    )

    def __init__(self, copy: SignupCopy, composer: "SignupComposerView") -> None:
        super().__init__()
        self.composer = composer
        self.title_input.default = copy.title[:100]
        self.body_input.default = copy.body[:1800]
        self.footer_input.default = (copy.footer or "")[:100]

    async def on_submit(self, interaction: discord.Interaction) -> None:
        copy = SignupCopy(
            title=str(self.title_input.value).strip() or DEFAULT_TITLE,
            body=str(self.body_input.value).strip() or DEFAULT_BODY,
            footer=str(self.footer_input.value).strip() or DEFAULT_FOOTER,
        )
        self.composer.copy = copy
        assert interaction.guild_id is not None
        interaction.client.db.set_birthday_signup_copy(
            interaction.guild_id,
            title=copy.title,
            body=copy.body,
            footer=copy.footer,
        )
        await interaction.response.edit_message(
            embed=signup_embed(
                copy,
                preview=self.composer.preview_only,
                guild=interaction.guild,
            ),
            view=self.composer,
        )
        await interaction.followup.send(
            "Signup text saved for this server.", ephemeral=True
        )


class SignupComposerView(discord.ui.View):
    """Admin composer: edit text, choose ping, preview / post."""

    def __init__(
        self,
        bot,
        copy: SignupCopy,
        *,
        target_channel: discord.TextChannel | None,
        preview_only: bool = False,
    ) -> None:
        super().__init__(timeout=600)
        self.bot = bot
        self.copy = copy
        self.target_channel = target_channel
        self.preview_only = preview_only
        self.mention_everyone = False
        self.mention_here = False
        self.mention_role: discord.Role | None = None

    def _mention_content(self) -> str | None:
        parts: list[str] = []
        if self.mention_everyone:
            parts.append("@everyone")
        if self.mention_here:
            parts.append("@here")
        if self.mention_role is not None:
            parts.append(self.mention_role.mention)
        return " ".join(parts) if parts else None

    def _ping_summary(self) -> str:
        content = self._mention_content()
        return content if content else "_no ping_"

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
        await interaction.response.send_modal(EditSignupCopyModal(self.copy, self))

    @discord.ui.button(label="@everyone", style=discord.ButtonStyle.danger, row=0)
    async def ping_everyone(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        self.mention_everyone = not self.mention_everyone
        if self.mention_everyone:
            self.mention_here = False
        await interaction.response.send_message(
            f"Ping: **{self._ping_summary()}**", ephemeral=True
        )

    @discord.ui.button(label="@here", style=discord.ButtonStyle.secondary, row=0)
    async def ping_here(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        self.mention_here = not self.mention_here
        if self.mention_here:
            self.mention_everyone = False
        await interaction.response.send_message(
            f"Ping: **{self._ping_summary()}**", ephemeral=True
        )

    @discord.ui.select(
        cls=discord.ui.RoleSelect,
        placeholder="Optional: @ a role…",
        min_values=0,
        max_values=1,
        row=1,
    )
    async def pick_role(
        self, interaction: discord.Interaction, select: discord.ui.RoleSelect
    ) -> None:
        self.mention_role = select.values[0] if select.values else None
        await interaction.response.send_message(
            f"Ping: **{self._ping_summary()}**", ephemeral=True
        )

    @discord.ui.button(label="Clear pings", style=discord.ButtonStyle.secondary, row=2)
    async def clear_pings(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        self.mention_everyone = False
        self.mention_here = False
        self.mention_role = None
        await interaction.response.send_message("Pings cleared.", ephemeral=True)

    @discord.ui.button(
        label="Refresh preview", style=discord.ButtonStyle.secondary, row=2
    )
    async def refresh_preview(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await interaction.response.edit_message(
            content=(
                f"**Composer** · ping: {self._ping_summary()}\n"
                + (
                    "_Preview only — nothing posted yet._"
                    if self.preview_only
                    else f"Post target: {self.target_channel.mention if self.target_channel else 'this channel'}"
                )
            ),
            embed=signup_embed(
                self.copy, preview=True, guild=interaction.guild
            ),
            view=self,
        )

    @discord.ui.button(
        label="Post announcement", style=discord.ButtonStyle.success, row=2
    )
    async def post_now(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if self.preview_only:
            await interaction.response.send_message(
                "This is preview mode. Use **Post signup panel** or `/birthdayannounce` to publish.",
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
        try:
            msg = await post_signup_announcement(
                channel,
                copy=self.copy,
                content=self._mention_content(),
            )
        except discord.Forbidden:
            await interaction.followup.send(
                f"I can't post in {channel.mention} "
                "(need Send Messages + Mention permissions).",
                ephemeral=True,
            )
            return
        await interaction.followup.send(
            f"Posted in {channel.mention}: {msg.jump_url}\nPing: {self._ping_summary()}",
            ephemeral=True,
        )


def is_signup_message(message: discord.Message, bot_user_id: int) -> bool:
    if message.author.id != bot_user_id:
        return False
    for row in message.components:
        for child in row.children:
            if getattr(child, "custom_id", None) == SIGNUP_BUTTON_ID:
                return True
    return False


async def post_signup_announcement(
    channel: discord.TextChannel,
    *,
    copy: SignupCopy | None = None,
    content: str | None = None,
) -> discord.Message:
    if copy is None:
        copy = default_copy()
    embed = signup_embed(copy, guild=channel.guild)
    allowed = discord.AllowedMentions(
        everyone=True,
        roles=True,
        users=False,
    )
    msg = await channel.send(
        content=content,
        embed=embed,
        view=BirthdaySignupView(),
        allowed_mentions=allowed,
    )
    try:
        await msg.add_reaction(SIGNUP_EMOJI)
    except (discord.Forbidden, discord.HTTPException) as exc:
        log.warning("Could not add signup reaction: %s", exc)
    return msg


def require_admin(interaction: discord.Interaction) -> str | None:
    if interaction.guild is None or not isinstance(interaction.user, discord.Member):
        return "Use this inside the server."
    if not is_guild_manager(interaction.user):
        return "Only the server owner or admins can do this."
    return None


async def open_signup_composer(
    interaction: discord.Interaction,
    bot,
    *,
    channel: discord.TextChannel | None,
    preview_only: bool,
) -> None:
    assert interaction.guild is not None
    copy = load_copy(bot, interaction.guild.id)
    view = SignupComposerView(
        bot,
        copy,
        target_channel=channel,
        preview_only=preview_only,
    )
    header = (
        "**Preview composer** — edit text & pings. Nothing is posted until you leave preview.\n"
        if preview_only
        else (
            f"**Post composer** — target: "
            f"{channel.mention if channel else 'this channel'}\n"
            "Edit text, choose @everyone / @here / a role, then **Post announcement**."
        )
    )
    await interaction.response.send_message(
        content=header + f"Current ping: {view._ping_summary()}",
        embed=signup_embed(copy, preview=True, guild=interaction.guild),
        view=view,
        ephemeral=True,
    )
