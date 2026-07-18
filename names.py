"""Real-name helpers: modal UX for members and admins."""

from __future__ import annotations

import logging
import re

import discord

from nicknames import build_nickname, display_base

log = logging.getLogger("dream_team.names")

NAME_PATTERN = re.compile(r"^[\w\s\-'.А-Яа-яЁёІіЇїЄєҐґ]{1,24}$", re.UNICODE)
WELCOME_SET_NAME_ID = "dream:welcome_set_name"


async def apply_real_name(
    bot,
    member: discord.Member,
    real_name: str,
    *,
    reason: str,
) -> tuple[bool, str]:
    """Save name + try nickname. Returns (nick_ok, human message)."""
    real_name = real_name.strip()
    if not NAME_PATTERN.match(real_name):
        return False, "Use 1–24 letters/numbers (spaces and `- ' .` allowed)."

    bot.db.set_real_name(member.guild.id, member.id, real_name)
    nick = build_nickname(display_base(member), real_name)

    try:
        await member.edit(nick=nick, reason=reason)
    except discord.Forbidden:
        return (
            False,
            f"Saved **{real_name}**, but I can't change nicknames "
            "(need **Manage Nicknames** and a higher role).",
        )
    except discord.HTTPException as exc:
        log.warning("Nickname edit failed: %s", exc)
        return False, f"Saved **{real_name}**, but Discord rejected the nickname."

    return True, f"{member.mention} → `{nick}`"


class SetNameModal(discord.ui.Modal, title="Set real name"):
    name_input = discord.ui.TextInput(
        label="Real name",
        placeholder="e.g. Миша",
        min_length=1,
        max_length=24,
        required=True,
    )

    def __init__(
        self,
        bot,
        member: discord.Member,
        *,
        hub=None,
        announce_channel: discord.abc.Messageable | None = None,
    ) -> None:
        super().__init__()
        self.bot = bot
        self.member = member
        self.hub = hub
        self.announce_channel = announce_channel
        current = bot.db.get_real_name(member.guild.id, member.id)
        if current:
            self.name_input.default = current[:24]
        self.title = f"Name for {member.display_name}"[:45]

    async def on_submit(self, interaction: discord.Interaction) -> None:
        ok, detail = await apply_real_name(
            self.bot,
            self.member,
            str(self.name_input.value),
            reason=f"Name set by {interaction.user}",
        )
        if self.hub is not None and interaction.guild is not None:
            embed = names_list_embed(interaction.guild, self.bot)
            embed.add_field(
                name="Updated" if ok else "Saved with a note",
                value=detail,
                inline=False,
            )
            self.hub.page = "names"
            self.hub._rebuild()
            await interaction.response.edit_message(embed=embed, view=self.hub)
            return

        await interaction.response.send_message(detail, ephemeral=True)
        if ok and self.announce_channel is not None:
            try:
                await self.announce_channel.send(
                    f"{self.member.mention} you're in — nick updated.\n"
                    "Birthday? Use `/setbirthday` anytime."
                )
            except discord.HTTPException:
                pass


class WelcomeNameView(discord.ui.View):
    """Persistent join button — no chat reply needed."""

    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Set my name",
        style=discord.ButtonStyle.primary,
        emoji="✏️",
        custom_id=WELCOME_SET_NAME_ID,
    )
    async def set_my_name(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                "Use this in the server.", ephemeral=True
            )
            return
        if interaction.user.bot:
            return

        bot = interaction.client
        channel = interaction.channel if isinstance(interaction.channel, discord.TextChannel) else None
        await interaction.response.send_modal(
            SetNameModal(
                bot,
                interaction.user,
                announce_channel=channel,
            )
        )


def names_list_embed(guild: discord.Guild, bot) -> discord.Embed:
    rows = bot.db.all_real_names(guild.id)
    embed = discord.Embed(
        title="Names",
        description=(
            f"**{len(rows)}** saved.\n"
            "Pick someone below → type their real name. Done."
        ),
        color=discord.Color.from_rgb(46, 230, 166),
    )
    embed.set_author(name="Dream Team")
    if not rows:
        embed.add_field(name="List", value="_Nobody yet — use the menu._", inline=False)
        return embed

    lines: list[str] = []
    for row in sorted(rows, key=lambda r: (r["real_name"] or "").lower()):
        member = guild.get_member(row["user_id"])
        who = member.mention if member else f"`{row['user_id']}`"
        lines.append(f"{who} · **{row['real_name']}**")

    chunk: list[str] = []
    size = 0
    field_i = 1
    for line in lines:
        if chunk and size + len(line) + 1 > 1000:
            embed.add_field(
                name="Members" if field_i == 1 else f"Members ({field_i})",
                value="\n".join(chunk),
                inline=False,
            )
            field_i += 1
            chunk, size = [], 0
        chunk.append(line)
        size += len(line) + 1
    if chunk:
        embed.add_field(
            name="Members" if field_i == 1 else f"Members ({field_i})",
            value="\n".join(chunk),
            inline=False,
        )
    return embed
