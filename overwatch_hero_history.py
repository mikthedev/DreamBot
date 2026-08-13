"""Per-hero balance history — slash command + forum browse buttons."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from ai_ow import (
    HERO_ALIASES,
    HeroPatchHit,
    lookup_hero_patch_history,
)
from overwatch_patches import (
    OW_ORANGE,
    PATCH_URL,
    ROLE_COLOR,
    ROLE_HEADER,
    _hero_changes_compact,
    _tone_label,
)
from ow_forum import (
    OW_PATCH_TAG_NAMES,
    hero_history_thread_title,
    post_ow_announcement,
)

if TYPE_CHECKING:
    from bot import DreamTeamBot

log = logging.getLogger("dream_team.ow_hero_history")

# Display name → role for forum select menus (≤25 options per role)
HEROES_BY_ROLE: dict[str, tuple[str, ...]] = {
    "Tank": (
        "D.Va",
        "D.Mon",
        "Doomfist",
        "Hazard",
        "Junker Queen",
        "Mauga",
        "Orisa",
        "Ramattra",
        "Reinhardt",
        "Roadhog",
        "Sigma",
        "Winston",
        "Wrecking Ball",
        "Zarya",
    ),
    "Damage": (
        "Ashe",
        "Bastion",
        "Cassidy",
        "Echo",
        "Freja",
        "Genji",
        "Hanzo",
        "Junkrat",
        "Mei",
        "Pharah",
        "Reaper",
        "Sojourn",
        "Soldier: 76",
        "Sombra",
        "Symmetra",
        "Torbjörn",
        "Tracer",
        "Vendetta",
        "Venture",
        "Widowmaker",
    ),
    "Support": (
        "Ana",
        "Baptiste",
        "Brigitte",
        "Illari",
        "Juno",
        "Kiriko",
        "Lifeweaver",
        "Lúcio",
        "Mercy",
        "Moira",
        "Shion",
        "Sierra",
        "Wuyang",
        "Zenyatta",
    ),
}

_DISPLAY_BY_QUERY: dict[str, str] = {}
for _heroes in HEROES_BY_ROLE.values():
    for _name in _heroes:
        _DISPLAY_BY_QUERY[_name.lower()] = _name
        _DISPLAY_BY_QUERY[HERO_ALIASES.get(_name.lower(), _name.lower())] = _name


def display_hero_name(query: str) -> str:
    q = HERO_ALIASES.get((query or "").lower().strip(), (query or "").lower().strip())
    return _DISPLAY_BY_QUERY.get(q, query.strip().title() if query else "Hero")


def _hit_kind_mark(hit: HeroPatchHit) -> str:
    if hit.buffish and not hit.nerfish:
        return "▲ buff"
    if hit.nerfish and not hit.buffish:
        return "▼ nerf"
    if hit.buffish and hit.nerfish:
        return "▲▼ mixed"
    return "·"


def _hit_role_colour(hit: HeroPatchHit) -> discord.Color:
    if hit.hero and hit.hero.role in ROLE_COLOR:
        return ROLE_COLOR[hit.hero.role]
    return OW_ORANGE


def _history_hit_body(hit: HeroPatchHit) -> str:
    """Ultra-compact change lines for the timeline."""
    if hit.hero is not None:
        return _hero_changes_compact(hit.hero, max_lines=4)
    rows = [f"· {ln}" for ln in hit.lines[:4]]
    if len(hit.lines) > 4:
        rows.append(f"_+{len(hit.lines) - 4} more…_")
    return "\n".join(rows) or "_No detail lines_"


def _history_hit_block(hit: HeroPatchHit) -> str:
    date_label = hit.patch_date or "Patch"
    url = hit.patch_url or PATCH_URL
    mark = _hit_kind_mark(hit)
    return f"**[{date_label}]({url})** · {mark}\n{_history_hit_body(hit)}"


def build_hero_history_layouts(
    hits: list[HeroPatchHit],
    *,
    hero_label: str,
    page: int = 0,
    per_page: int = 3,
) -> tuple[list[discord.ui.LayoutView], int]:
    """
    Compact timeline — few patches per page, one line per tweak.
    Returns (layouts_for_this_page, total_pages).
    """
    total = max(1, (len(hits) + per_page - 1) // per_page) if hits else 1
    page = max(0, min(page, total - 1))

    if not hits:
        view = discord.ui.LayoutView(timeout=None)
        empty = discord.ui.Container(accent_colour=OW_ORANGE)
        view.add_item(empty)
        empty.add_item(
            discord.ui.TextDisplay(
                f"**{hero_label}**\n"
                "_No retail balance changes in recent patch notes._"
            )
        )
        return [view], 1

    chunk = hits[page * per_page : (page + 1) * per_page]
    pages_note = f" · {page + 1}/{total}" if total > 1 else ""
    colour = _hit_role_colour(chunk[0])
    icon_url = next(
        (h.hero.icon_url for h in chunk if h.hero and h.hero.icon_url),
        None,
    )
    role_label = ""
    if chunk[0].hero and chunk[0].hero.role:
        role_label = ROLE_HEADER.get(
            chunk[0].hero.role, chunk[0].hero.role.upper()
        )

    header = f"**{hero_label}**"
    if role_label:
        header += f" · {role_label}"
    header += (
        f"\n{len(hits)} touch{'es' if len(hits) != 1 else ''}"
        f"{pages_note} · {_tone_label('▲')} · {_tone_label('▼')}"
    )

    view = discord.ui.LayoutView(timeout=None)
    container = discord.ui.Container(accent_colour=colour)
    view.add_item(container)

    if icon_url:
        container.add_item(
            discord.ui.Section(
                header,
                accessory=discord.ui.Thumbnail(icon_url),
            )
        )
    else:
        container.add_item(discord.ui.TextDisplay(header))

    for hit in chunk:
        container.add_item(
            discord.ui.Separator(
                visible=True,
                spacing=discord.SeparatorSpacing.small,
            )
        )
        container.add_item(discord.ui.TextDisplay(_history_hit_block(hit)))

    return [view], total


def build_hero_history_embeds(
    hits: list[HeroPatchHit],
    *,
    hero_label: str,
    page: int = 0,
    per_page: int = 3,
) -> tuple[list[discord.Embed], int]:
    total = max(1, (len(hits) + per_page - 1) // per_page) if hits else 1
    page = max(0, min(page, total - 1))
    colour = _hit_role_colour(hits[0]) if hits else OW_ORANGE
    head = discord.Embed(
        title=hero_label,
        color=colour,
        url=PATCH_URL,
    )
    if not hits:
        head.description = "No retail balance changes in recent patch notes."
        return [head], 1

    chunk = hits[page * per_page : (page + 1) * per_page]
    head.description = (
        f"**{len(hits)}** touch{'es' if len(hits) != 1 else ''}"
        + (f" · page {page + 1}/{total}" if total > 1 else "")
        + f" · {_tone_label('▲')} · {_tone_label('▼')}"
    )
    icon = next(
        (h.hero.icon_url for h in chunk if h.hero and h.hero.icon_url),
        None,
    )
    if icon:
        head.set_thumbnail(url=icon)

    body = "\n\n".join(_history_hit_block(h) for h in chunk)
    head.description = f"{head.description}\n\n{body}"[:4096]
    return [head], total


async def send_hero_history(
    interaction: discord.Interaction,
    hits: list[HeroPatchHit],
    *,
    hero_label: str,
    page: int = 0,
    edit_searching: bool = False,
) -> None:
    """Ephemeral history page; optionally replace a prior “Searching…” message."""
    try:
        layouts, total = build_hero_history_layouts(
            hits, hero_label=hero_label, page=page
        )
        if edit_searching and page == 0:
            try:
                await interaction.edit_original_response(
                    content=None, embeds=[], view=layouts[0]
                )
            except Exception:
                await interaction.followup.send(view=layouts[0], ephemeral=True)
        else:
            await interaction.followup.send(view=layouts[0], ephemeral=True)
        for layout in layouts[1:]:
            await interaction.followup.send(view=layout, ephemeral=True)
    except Exception as exc:
        log.warning("Hero history layout failed: %s", exc)
        embeds, total = build_hero_history_embeds(
            hits, hero_label=hero_label, page=page
        )
        if edit_searching and page == 0:
            try:
                await interaction.edit_original_response(
                    content=None, embeds=embeds, view=None
                )
            except Exception:
                await interaction.followup.send(embeds=embeds, ephemeral=True)
        else:
            await interaction.followup.send(embeds=embeds, ephemeral=True)

    if total > 1:
        await interaction.followup.send(
            view=HeroHistoryNavView(
                hits=hits, hero_label=hero_label, page=page, total_pages=total
            ),
            ephemeral=True,
        )


class HeroHistoryNavView(discord.ui.View):
    """Prev / next through a hero's patch touches."""

    def __init__(
        self,
        *,
        hits: list[HeroPatchHit],
        hero_label: str,
        page: int,
        total_pages: int,
    ) -> None:
        super().__init__(timeout=180)
        self.hits = hits
        self.hero_label = hero_label
        self.page = page
        self.total_pages = max(1, total_pages)

        prev_btn = discord.ui.Button(
            label="Newer",
            style=discord.ButtonStyle.secondary,
            disabled=page <= 0,
            row=0,
        )
        next_btn = discord.ui.Button(
            label="Older",
            style=discord.ButtonStyle.secondary,
            disabled=page >= self.total_pages - 1,
            row=0,
        )
        jump = discord.ui.Select(
            placeholder=f"Jump to patch… ({page + 1}/{self.total_pages})",
            min_values=1,
            max_values=1,
            options=_jump_options(hits, page),
            row=1,
        )

        async def on_prev(interaction: discord.Interaction) -> None:
            await interaction.response.defer(ephemeral=True)
            await send_hero_history(
                interaction,
                self.hits,
                hero_label=self.hero_label,
                page=max(0, self.page - 1),
            )

        async def on_next(interaction: discord.Interaction) -> None:
            await interaction.response.defer(ephemeral=True)
            await send_hero_history(
                interaction,
                self.hits,
                hero_label=self.hero_label,
                page=min(self.total_pages - 1, self.page + 1),
            )

        async def on_jump(interaction: discord.Interaction) -> None:
            await interaction.response.defer(ephemeral=True)
            try:
                idx = int(jump.values[0])
            except ValueError:
                idx = 0
            per_page = 3
            await send_hero_history(
                interaction,
                self.hits,
                hero_label=self.hero_label,
                page=idx // per_page,
            )

        prev_btn.callback = on_prev
        next_btn.callback = on_next
        jump.callback = on_jump
        self.add_item(prev_btn)
        self.add_item(next_btn)
        if hits:
            self.add_item(jump)


def _jump_options(
    hits: list[HeroPatchHit], current_page: int
) -> list[discord.SelectOption]:
    opts: list[discord.SelectOption] = []
    per_page = 3
    for i, hit in enumerate(hits[:25]):
        label = hit.patch_date or hit.patch_id or f"Patch {i + 1}"
        mark = _hit_kind_mark(hit)
        opts.append(
            discord.SelectOption(
                label=f"{label} · {mark}"[:100],
                value=str(i),
                description=(hit.lines[0][:80] if hit.lines else None),
                default=(i // per_page) == current_page and i % per_page == 0,
            )
        )
    return opts or [
        discord.SelectOption(label="No patches", value="0", default=True)
    ]


class HeroRoleSelect(discord.ui.Select):
    def __init__(self) -> None:
        super().__init__(
            placeholder="Pick a role…",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(label="Tank", value="Tank", emoji="🛡️"),
                discord.SelectOption(label="Damage", value="Damage", emoji="⚔️"),
                discord.SelectOption(label="Support", value="Support", emoji="💚"),
            ],
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        role = self.values[0]
        heroes = HEROES_BY_ROLE.get(role) or ()
        await interaction.response.edit_message(
            content=f"**{role}** — choose a hero:",
            view=HeroPickView(role=role, heroes=heroes),
        )


class HeroPickSelect(discord.ui.Select):
    def __init__(self, role: str, heroes: tuple[str, ...]) -> None:
        options = [
            discord.SelectOption(label=name, value=name) for name in heroes[:25]
        ]
        super().__init__(
            placeholder=f"{role} heroes…",
            min_values=1,
            max_values=1,
            options=options
            or [discord.SelectOption(label="None", value="__none__")],
        )
        self.role = role

    async def callback(self, interaction: discord.Interaction) -> None:
        name = self.values[0]
        if name == "__none__":
            await interaction.response.send_message(
                "No heroes in that list.", ephemeral=True
            )
            return
        await _deliver_history(interaction, name)


class HeroPickView(discord.ui.View):
    def __init__(self, *, role: str, heroes: tuple[str, ...]) -> None:
        super().__init__(timeout=120)
        self.add_item(HeroPickSelect(role, heroes))
        back = discord.ui.Button(label="Back", style=discord.ButtonStyle.secondary)

        async def on_back(interaction: discord.Interaction) -> None:
            await interaction.response.edit_message(
                content="Browse a hero’s balance history across patch notes:",
                view=HeroHistoryBrowseView(),
            )

        back.callback = on_back
        self.add_item(back)


class HeroHistoryBrowseView(discord.ui.View):
    """Ephemeral role → hero picker (opened from the patch forum button)."""

    def __init__(self) -> None:
        super().__init__(timeout=120)
        self.add_item(HeroRoleSelect())


class OwHubHeroSelect(discord.ui.Select):
    """Persistent per-role hero picker on the public Hero History forum post."""

    def __init__(self, role: str, *, row: int) -> None:
        heroes = HEROES_BY_ROLE.get(role) or ()
        emoji = {"Tank": "🛡️", "Damage": "⚔️", "Support": "💚"}.get(role)
        options = [
            discord.SelectOption(label=name, value=name, emoji=emoji)
            for name in heroes[:25]
        ]
        super().__init__(
            placeholder=f"{emoji + ' ' if emoji else ''}{role}…",
            min_values=1,
            max_values=1,
            options=options
            or [discord.SelectOption(label="None", value="__none__")],
            custom_id=f"ow_hero_hist:{role.lower()}",
            row=row,
        )
        self.role = role

    async def callback(self, interaction: discord.Interaction) -> None:
        name = self.values[0]
        if name == "__none__":
            await interaction.response.send_message(
                "No heroes in that list.", ephemeral=True
            )
            return
        await _deliver_history(interaction, name)


class OwHeroHistoryHubView(discord.ui.View):
    """Public hub for Search Hero Changes (survives restarts)."""

    def __init__(self) -> None:
        super().__init__(timeout=None)
        self.add_item(OwHubHeroSelect("Tank", row=0))
        self.add_item(OwHubHeroSelect("Damage", row=1))
        self.add_item(OwHubHeroSelect("Support", row=2))


def build_hero_history_hub_layouts() -> list[discord.ui.LayoutView]:
    view = discord.ui.LayoutView(timeout=None)
    container = discord.ui.Container(accent_colour=OW_ORANGE)
    view.add_item(container)
    container.add_item(
        discord.ui.TextDisplay(
            "**Search Hero Changes**\n"
            "Pick a hero · see recent buffs & nerfs · newest first."
        )
    )
    container.add_item(
        discord.ui.Separator(
            visible=True, spacing=discord.SeparatorSpacing.small
        )
    )
    container.add_item(
        discord.ui.TextDisplay(
            f"{_tone_label('▲')}   {_tone_label('▼')}\n"
            "🛡️ Tank · ⚔️ Damage · 💚 Support — results are private."
        )
    )
    container.add_item(
        discord.ui.Separator(
            visible=True, spacing=discord.SeparatorSpacing.small
        )
    )
    container.add_item(
        discord.ui.TextDisplay(
            f"_Also `/hero` · [patch notes]({PATCH_URL})_"
        )
    )
    return [view]


def build_hero_history_hub_embeds() -> list[discord.Embed]:
    emb = discord.Embed(
        title="Search Hero Changes",
        description=(
            "Pick a hero · see recent buffs & nerfs · newest first.\n\n"
            f"{_tone_label('▲')} · {_tone_label('▼')}\n"
            "🛡️ Tank · ⚔️ Damage · 💚 Support — private results.\n\n"
            f"_Also `/hero` · [patch notes]({PATCH_URL})_"
        ),
        color=OW_ORANGE,
        url=PATCH_URL,
    )
    emb.set_footer(text="Patch Notes")
    return [emb]


async def _deliver_history(interaction: discord.Interaction, hero: str) -> None:
    guild = interaction.guild
    if guild is None:
        if interaction.response.is_done():
            await interaction.followup.send(
                "This only works in a server.", ephemeral=True
            )
        else:
            await interaction.response.send_message(
                "This only works in a server.", ephemeral=True
            )
        return
    label = display_hero_name(hero)
    # Immediate feedback while Blizzard notes are fetched
    if interaction.response.is_done():
        await interaction.followup.send(
            f"🔍 Searching **{label}**…", ephemeral=True
        )
        edit_searching = False
    else:
        await interaction.response.send_message(
            f"🔍 Searching **{label}**…", ephemeral=True
        )
        edit_searching = True
    try:
        hits, _latest = await lookup_hero_patch_history(
            interaction.client, guild.id, hero, max_hits=25, max_months=12
        )
    except Exception as exc:
        log.warning("Hero history lookup failed for %s: %s", hero, exc)
        msg = f"Could not load history for **{label}** right now."
        if edit_searching:
            try:
                await interaction.edit_original_response(content=msg)
                return
            except Exception:
                pass
        await interaction.followup.send(msg, ephemeral=True)
        return
    await send_hero_history(
        interaction,
        hits,
        hero_label=label,
        page=0,
        edit_searching=edit_searching,
    )


async def hero_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    needle = (current or "").lower().strip()
    names: list[str] = []
    for heroes in HEROES_BY_ROLE.values():
        names.extend(heroes)
    # Stable unique, prefer prefix matches
    scored: list[tuple[int, str]] = []
    for name in names:
        low = name.lower()
        if not needle:
            scored.append((0, name))
        elif low.startswith(needle):
            scored.append((0, name))
        elif needle in low:
            scored.append((1, name))
        elif needle in HERO_ALIASES and HERO_ALIASES[needle] in low:
            scored.append((0, name))
    scored.sort(key=lambda t: (t[0], t[1]))
    out: list[app_commands.Choice[str]] = []
    seen: set[str] = set()
    for _, name in scored:
        if name in seen:
            continue
        seen.add(name)
        out.append(app_commands.Choice(name=name, value=name))
        if len(out) >= 25:
            break
    return out


class OverwatchHeroHistoryCog(commands.Cog):
    """Slash `/hero` + panel-posted Hero Balance History hub."""

    def __init__(self, bot: DreamTeamBot) -> None:
        self.bot = bot

    @app_commands.command(
        name="hero",
        description="Show every recent balance change for one Overwatch hero",
    )
    @app_commands.describe(hero="Hero to look up (e.g. Moira, D.Mon)")
    @app_commands.autocomplete(hero=hero_autocomplete)
    async def hero(
        self, interaction: discord.Interaction, hero: str
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                "This only works in a server.", ephemeral=True
            )
            return
        await _deliver_history(interaction, hero)

    async def publish_hub(
        self,
        channel: discord.TextChannel | discord.ForumChannel,
    ) -> list[discord.Message]:
        """One locked Patch Notes forum post with role → hero menus."""
        guild_id = channel.guild.id
        existing_thread_id = None
        if isinstance(channel, discord.ForumChannel):
            existing_thread_id = self.bot.db.get_ow_hero_history_thread_id(guild_id)

        messages, thread_id = await post_ow_announcement(
            channel,
            thread_name=hero_history_thread_title(),
            layouts=build_hero_history_hub_layouts(),
            embeds_fallback=build_hero_history_hub_embeds,
            tag_names=OW_PATCH_TAG_NAMES,
            trailing_content=None,
            trailing_view=OwHeroHistoryHubView(),
            existing_thread_id=existing_thread_id,
        )
        if thread_id is not None:
            self.bot.db.set_ow_hero_history_thread_id(guild_id, thread_id)
        return messages


async def setup(bot: DreamTeamBot) -> None:
    await bot.add_cog(OverwatchHeroHistoryCog(bot))
