"""Persistent Ukrainian onboarding panel — edit-in-place, survives restarts."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import discord

from birthday_signup import save_own_birthday
from birthdays import parse_birthday
from names import NAME_PATTERN, apply_real_name

log = logging.getLogger("dream_team.onboard")

ONBOARD_BIRTHDAY_ID = "dreamteam:onboard_birthday"
ONBOARD_OW_ID = "dreamteam:onboard_ow"
ONBOARD_NICK_ID = "dreamteam:onboard_nick"

ACCENT = discord.Color.from_rgb(88, 101, 242)

DEFAULT_TITLE = "Ласкаво просимо до Dream Team"
DEFAULT_BODY = (
    "Коротко налаштуй себе — кнопки нижче:\n\n"
    "🎂 **День народження** — щоб ми привітали тебе\n"
    "🎮 **Overwatch** — патчі та тір-лист у спеціальному каналі\n"
    "✏️ **Нікнейм** — Discord-ім'я + твоє справжнє\n\n"
    "_Усе зберігається лише для цього сервера._"
)


@dataclass
class OnboardCopy:
    title: str
    body: str


def default_copy() -> OnboardCopy:
    return OnboardCopy(title=DEFAULT_TITLE, body=DEFAULT_BODY)


def load_copy(bot, guild_id: int) -> OnboardCopy:
    raw = bot.db.get_onboard_copy(guild_id)
    base = default_copy()
    return OnboardCopy(
        title=(raw["title"] or base.title).strip() or base.title,
        body=(raw["body"] or base.body).strip() or base.body,
    )


def onboard_embed(
    copy: OnboardCopy,
    *,
    preview: bool = False,
    guild: discord.Guild | None = None,
) -> discord.Embed:
    embed = discord.Embed(
        title=copy.title,
        description=copy.body,
        color=discord.Color.from_rgb(33, 143, 254) if preview else ACCENT,
    )
    if guild is not None:
        embed.set_author(
            name=guild.name,
            icon_url=guild.icon.url if guild.icon else None,
        )
    else:
        embed.set_author(name="Dream Team")
    footer = "Dream Team · онбординг"
    if preview:
        footer = f"ПЕРЕГЛЯД · {footer}"
    embed.set_footer(text=footer)
    if guild is not None and guild.icon:
        embed.set_thumbnail(url=guild.icon.url)
    return embed


class OnboardBirthdayModal(discord.ui.Modal, title="День народження"):
    date_input = discord.ui.TextInput(
        label="Твоя дата",
        placeholder="15.03  або  15.03.2001",
        min_length=3,
        max_length=12,
        required=True,
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                "Використовуй це на сервері.", ephemeral=True
            )
            return
        bday = parse_birthday(str(self.date_input.value))
        if bday is None:
            await interaction.response.send_message(
                "Невірна дата. Формат: `ДД.ММ` або `ДД.ММ.РРРР` (наприклад `15.03`).",
                ephemeral=True,
            )
            return
        await save_own_birthday(interaction, bday)


class OnboardNameModal(discord.ui.Modal, title="Нікнейм"):
    name_input = discord.ui.TextInput(
        label="Справжнє ім'я",
        placeholder="наприклад Миша",
        min_length=1,
        max_length=24,
        required=True,
    )

    def __init__(self, bot, member: discord.Member) -> None:
        super().__init__()
        self.bot = bot
        self.member = member
        current = bot.db.get_real_name(member.guild.id, member.id)
        if current:
            self.name_input.default = current[:24]

    async def on_submit(self, interaction: discord.Interaction) -> None:
        ok, detail = await apply_real_name(
            self.bot,
            self.member,
            str(self.name_input.value),
            reason=f"Onboard name by {interaction.user}",
        )
        if ok:
            await interaction.response.send_message(
                f"Готово! {detail}", ephemeral=True
            )
        else:
            # apply_real_name returns English validation; keep UA wrapper for nick errors
            raw = str(self.name_input.value).strip()
            if not NAME_PATTERN.match(raw):
                await interaction.response.send_message(
                    "Ім'я: 1–24 символи (літери, цифри, пробіли, `- ' .`).",
                    ephemeral=True,
                )
                return
            await interaction.response.send_message(detail, ephemeral=True)


class OnboardingView(discord.ui.View):
    """Persistent public buttons — registered in setup_hook."""

    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="День народження",
        style=discord.ButtonStyle.success,
        emoji="🎂",
        custom_id=ONBOARD_BIRTHDAY_ID,
        row=0,
    )
    async def set_birthday(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                "Використовуй це на сервері.", ephemeral=True
            )
            return
        if interaction.user.bot:
            return
        await interaction.response.send_modal(OnboardBirthdayModal())

    @discord.ui.button(
        label="Overwatch канал",
        style=discord.ButtonStyle.primary,
        emoji="🎮",
        custom_id=ONBOARD_OW_ID,
        row=0,
    )
    async def join_overwatch(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                "Використовуй це на сервері.", ephemeral=True
            )
            return
        if interaction.user.bot:
            return

        bot = interaction.client
        role_id = bot.db.get_ow_broadcast_role(interaction.guild.id)
        if not role_id:
            await interaction.response.send_message(
                "Адмін ще не налаштував роль для Overwatch. "
                "Попроси їх у **/panel → Онбординг**.",
                ephemeral=True,
            )
            return

        role = interaction.guild.get_role(role_id)
        if role is None:
            await interaction.response.send_message(
                "Роль Overwatch зникла — скажи адміну оновити налаштування.",
                ephemeral=True,
            )
            return

        me = interaction.guild.me
        if me is None or role >= me.top_role or not me.guild_permissions.manage_roles:
            await interaction.response.send_message(
                "Не можу видати цю роль (потрібні **Manage Roles** і роль бота вище).",
                ephemeral=True,
            )
            return

        member = interaction.user
        try:
            if role in member.roles:
                await member.remove_roles(role, reason="Onboard: leave Overwatch")
                await interaction.response.send_message(
                    "Ви вимкнули канал Overwatch (патчі / тір-лист).",
                    ephemeral=True,
                )
            else:
                await member.add_roles(role, reason="Onboard: join Overwatch")
                bits: list[str] = ["Готово! Тепер бачиш Overwatch-трансляції."]
                patch_id = bot.db.get_ow_patch_channel(interaction.guild.id)
                tier_id = bot.db.get_ow_tier_channel(interaction.guild.id)
                chans: list[str] = []
                if patch_id:
                    ch = interaction.guild.get_channel(patch_id)
                    if ch:
                        chans.append(ch.mention)
                if tier_id and tier_id != patch_id:
                    ch = interaction.guild.get_channel(tier_id)
                    if ch:
                        chans.append(ch.mention)
                if chans:
                    bits.append("Канали: " + " · ".join(chans))
                bits.append("_Натисни знову, щоб вимкнути._")
                await interaction.response.send_message(
                    "\n".join(bits), ephemeral=True
                )
        except discord.Forbidden:
            await interaction.response.send_message(
                "Немає прав змінити цю роль.", ephemeral=True
            )
        except discord.HTTPException as exc:
            log.warning("OW role toggle failed: %s", exc)
            await interaction.response.send_message(
                "Discord відхилив зміну ролі. Спробуй пізніше.", ephemeral=True
            )

    @discord.ui.button(
        label="Нікнейм",
        style=discord.ButtonStyle.secondary,
        emoji="✏️",
        custom_id=ONBOARD_NICK_ID,
        row=0,
    )
    async def set_nick(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                "Використовуй це на сервері.", ephemeral=True
            )
            return
        if interaction.user.bot:
            return
        await interaction.response.send_modal(
            OnboardNameModal(interaction.client, interaction.user)
        )


class EditOnboardModal(discord.ui.Modal, title="Редагувати онбординг"):
    title_input = discord.ui.TextInput(
        label="Заголовок",
        max_length=120,
        required=True,
    )
    body_input = discord.ui.TextInput(
        label="Текст",
        style=discord.TextStyle.paragraph,
        max_length=1800,
        required=True,
    )

    def __init__(self, hub, copy: OnboardCopy) -> None:
        super().__init__()
        self.hub = hub
        self.title_input.default = copy.title[:120]
        self.body_input.default = copy.body[:1800]

    async def on_submit(self, interaction: discord.Interaction) -> None:
        title = str(self.title_input.value).strip() or DEFAULT_TITLE
        body = str(self.body_input.value).strip() or DEFAULT_BODY
        self.hub.bot.db.set_onboard_copy(
            self.hub.guild_id, title=title, body=body
        )
        self.hub.page = "onboard"
        self.hub._rebuild()
        await interaction.response.edit_message(
            embed=hub_onboard_embed(interaction.guild, self.hub.bot),
            view=self.hub,
        )


def hub_onboard_embed(guild: discord.Guild, bot) -> discord.Embed:
    copy = load_copy(bot, guild.id)
    ch = bot.db.get_onboard_channel(guild.id)
    mid = bot.db.get_onboard_message_id(guild.id)
    role_id = bot.db.get_ow_broadcast_role(guild.id)
    role = guild.get_role(role_id) if role_id else None
    channel = guild.get_channel(ch) if ch else None

    status = "_ще не опубліковано_"
    if channel and mid:
        status = f"{channel.mention} · повідомлення `{mid}`"
    elif channel:
        status = f"{channel.mention} · _немає збереженого поста_"

    embed = discord.Embed(
        title="Онбординг",
        description=(
            "Одне постійне повідомлення з кнопками. "
            "**Опублікувати / оновити** редагує існуючий пост "
            "(без видалення — без «нового» сповіщення).\n\n"
            "Роль Overwatch має відкривати канали патчів і тір-листа."
        ),
        color=ACCENT,
    )
    embed.add_field(name="Канал / пост", value=status, inline=False)
    embed.add_field(
        name="Роль Overwatch",
        value=role.mention if role else "_не задано_",
        inline=False,
    )
    embed.add_field(name="Заголовок", value=copy.title[:256], inline=False)
    preview = copy.body if len(copy.body) <= 500 else copy.body[:497] + "…"
    embed.add_field(name="Текст", value=preview, inline=False)
    return embed


async def publish_onboard(
    bot,
    guild: discord.Guild,
    *,
    channel: discord.TextChannel | None = None,
) -> tuple[discord.Message | None, str]:
    """
    Post once, or edit the existing onboard message in place.
    Returns (message, status detail).
    """
    copy = load_copy(bot, guild.id)
    channel_id = bot.db.get_onboard_channel(guild.id)
    message_id = bot.db.get_onboard_message_id(guild.id)

    if channel is None:
        if not channel_id:
            return None, "Спочатку обери канал онбордингу."
        found = guild.get_channel(channel_id)
        if not isinstance(found, discord.TextChannel):
            return None, "Канал онбордингу відсутній або не текстовий."
        channel = found

    embed = onboard_embed(copy, guild=guild)
    view = OnboardingView()

    if message_id and channel_id == channel.id:
        try:
            msg = await channel.fetch_message(message_id)
            await msg.edit(content=None, embed=embed, view=view)
            bot.db.set_onboard_panel(guild.id, channel.id, msg.id)
            return msg, f"Оновлено на місці: {msg.jump_url}"
        except discord.NotFound:
            log.info("Onboard message %s missing — posting fresh", message_id)
        except discord.Forbidden:
            return None, f"Немає прав редагувати повідомлення в {channel.mention}."
        except discord.HTTPException as exc:
            log.warning("Onboard edit failed: %s", exc)
            return None, f"Не вдалося оновити пост: {exc}"

    try:
        msg = await channel.send(embed=embed, view=view)
    except discord.Forbidden:
        return None, f"Немає прав писати в {channel.mention}."
    except discord.HTTPException as exc:
        return None, f"Не вдалося опублікувати: {exc}"

    bot.db.set_onboard_panel(guild.id, channel.id, msg.id)
    return msg, f"Опубліковано: {msg.jump_url}"
