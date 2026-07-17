"""Public birthday signup announcement — button + modal, and 🎂 reaction."""

from __future__ import annotations

import logging

import discord

from birthdays import parse_birthday
from nicknames import is_guild_manager

log = logging.getLogger("dream_team.birthday_signup")

SIGNUP_TITLE = "Add your birthday"
SIGNUP_EMOJI = "🎂"
BRAND = discord.Color.from_rgb(14, 28, 48)
ACCENT = discord.Color.from_rgb(46, 230, 166)


def signup_embed(*, preview: bool = False) -> discord.Embed:
    embed = discord.Embed(
        title=SIGNUP_TITLE,
        description=(
            "Dream Team wants to celebrate you.\n\n"
            "**Easiest way:** tap **Add my birthday** below and enter your date.\n"
            f"**Or** react with {SIGNUP_EMOJI} — we'll send you a private button.\n\n"
            "Format: `DD.MM` (example `15.03`) or `DD.MM.YYYY`."
        ),
        color=ACCENT,
    )
    embed.set_author(name="Dream Team")
    embed.add_field(
        name="Why?",
        value="So we can wish you a happy birthday in the server on your day.",
        inline=False,
    )
    if preview:
        embed.set_footer(text="PREVIEW — only you can see this · not posted yet")
    else:
        embed.set_footer(text="Your date is saved privately in this server")
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

        bot = interaction.client
        bot.db.set_birthday(
            interaction.guild_id,
            interaction.user.id,
            bday.month,
            bday.day,
            bday.year,
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
        custom_id="dreamteam:birthday_signup",
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


def is_signup_message(message: discord.Message, bot_user_id: int) -> bool:
    if message.author.id != bot_user_id or not message.embeds:
        return False
    return (message.embeds[0].title or "") == SIGNUP_TITLE


async def post_signup_announcement(
    channel: discord.TextChannel,
) -> discord.Message:
    msg = await channel.send(embed=signup_embed(), view=BirthdaySignupView())
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
