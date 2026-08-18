"""Per-hero balance history — slash command + forum browse buttons."""

from __future__ import annotations

import asyncio
import logging
import unicodedata
from dataclasses import replace
from typing import TYPE_CHECKING, Literal

import discord
from discord import app_commands
from discord.ext import commands

from ai_ow import (
    HERO_ALIASES,
    HeroPatchHit,
    _hit_from_summary,
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

HISTORY_PER_PAGE = 3
HISTORY_NAV_TIMEOUT = 180.0
HISTORY_MAX_HITS = 25
HISTORY_MAX_MONTHS = 24
# LayoutView allows 4000 display chars; leave room for the header.
HISTORY_PAGE_CHAR_BUDGET = 3600

HISTORY_PROMPT = "Browse a hero’s balance history across patch notes:"
ALERT_PROMPT = (
    "Pick a role, then a hero. I’ll DM you when they’re patched — "
    "this post stays quiet for everyone else."
)
BrowseMode = Literal["history", "alerts"]


def _v2_edit_kwargs(view: discord.ui.LayoutView) -> dict:
    """Kwargs to swap a message to a LayoutView. Do not pass embed and embeds together."""
    return {
        "content": None,
        "embed": None,
        "attachments": [],
        "view": view,
    }

# Display name → role for forum select menus (≤25 options per role)
HEROES_BY_ROLE: dict[str, tuple[str, ...]] = {
    "Tank": (
        "D.Mon",
        "D.Va",
        "Domina",
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
        "Anran",
        "Ashe",
        "Bastion",
        "Cassidy",
        "Echo",
        "Emre",
        "Freja",
        "Genji",
        "Hanzo",
        "Junkrat",
        "Mei",
        "Pharah",
        "Reaper",
        "Shion",
        "Sierra",
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
        "Jetpack Cat",
        "Juno",
        "Kiriko",
        "Lifeweaver",
        "Lúcio",
        "Mercy",
        "Mizuki",
        "Moira",
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


def hero_alert_key(name: str) -> str:
    """Stable key so ‘Lúcio’ in notes still matches a ‘Lucio’ subscription."""
    q = (name or "").lower().strip()
    if not q:
        return ""
    q = HERO_ALIASES.get(q, q)
    folded = unicodedata.normalize("NFKD", q).encode("ascii", "ignore").decode("ascii")
    q = folded or q
    return HERO_ALIASES.get(q, q)


def _alert_on_for(interaction: discord.Interaction, hero: str) -> bool:
    guild = interaction.guild
    if guild is None:
        return False
    try:
        return interaction.client.db.has_hero_alert(
            guild.id, interaction.user.id, hero_alert_key(hero)
        )
    except Exception:
        return False


def _hit_kind_mark(hit: HeroPatchHit) -> str:
    """Glyph only — the card legend already spells out buff / nerf."""
    if hit.buffish and not hit.nerfish:
        return "▲"
    if hit.nerfish and not hit.buffish:
        return "▼"
    if hit.buffish and hit.nerfish:
        return "▲▼"
    return "·"


def _hit_role_colour(hit: HeroPatchHit) -> discord.Color:
    if hit.hero and hit.hero.role in ROLE_COLOR:
        return ROLE_COLOR[hit.hero.role]
    return OW_ORANGE


def _history_hit_body(
    hit: HeroPatchHit,
    *,
    emoji_map: dict[str, discord.Emoji] | None = None,
) -> str:
    """Every change line for this patch — no '+N more' truncation."""
    if hit.hero is not None:
        return _hero_changes_compact(
            hit.hero, max_lines=None, emoji_map=emoji_map
        )
    rows = [f"· {ln}" for ln in hit.lines]
    return "\n".join(rows) or "_No detail lines_"


def _history_hit_block(
    hit: HeroPatchHit,
    *,
    emoji_map: dict[str, discord.Emoji] | None = None,
) -> str:
    date_label = hit.patch_date or "Patch"
    url = hit.patch_url or PATCH_URL
    mark = _hit_kind_mark(hit)
    return (
        f"**[{date_label}]({url})** · {mark}\n"
        f"{_history_hit_body(hit, emoji_map=emoji_map)}"
    )


async def _ability_emoji_map_for_hits(
    bot, hits: list[HeroPatchHit]
) -> dict[str, discord.Emoji]:
    """Ensure utility icons exist as app emojis for these history hits."""
    tier_cog = bot.get_cog("OverwatchTierCog")
    if tier_cog is None:
        return {}
    pairs: list[tuple[str, str, str | None]] = []
    for hit in hits:
        if hit.hero is None:
            continue
        for ch in hit.hero.changes:
            pairs.append((hit.hero.name, ch.ability, ch.icon_url))
            if not ch.icon_url:
                filled = tier_cog.ability_icon_url(hit.hero.name, ch.ability)
                if filled:
                    ch.icon_url = filled
    if not pairs:
        return {}
    try:
        return await tier_cog.ensure_ability_emojis(pairs)
    except Exception as exc:
        log.warning("Hero-history ability emoji sync failed: %s", exc)
        return {}


def _hit_line_count(hit: HeroPatchHit) -> int:
    if hit.hero is not None and hit.hero.changes:
        return len(hit.hero.changes)
    return max(1, len(hit.lines))


def _slice_hit(hit: HeroPatchHit, start: int, end: int) -> HeroPatchHit:
    if hit.hero is not None and hit.hero.changes:
        return replace(
            hit, hero=replace(hit.hero, changes=hit.hero.changes[start:end])
        )
    return replace(hit, lines=hit.lines[start:end])


def _split_hit_to_fit(
    hit: HeroPatchHit,
    budget: int,
    *,
    emoji_map: dict[str, discord.Emoji] | None = None,
) -> list[HeroPatchHit]:
    """Keep a patch on one page when it fits; otherwise split its lines."""
    n = _hit_line_count(hit)
    if n <= 1 or len(_history_hit_block(hit, emoji_map=emoji_map)) <= budget:
        return [hit]
    parts: list[HeroPatchHit] = []
    start = 0
    while start < n:
        lo, hi = start + 1, n
        best = start + 1
        while lo <= hi:
            mid = (lo + hi) // 2
            piece = _slice_hit(hit, start, mid)
            if len(_history_hit_block(piece, emoji_map=emoji_map)) <= budget:
                best = mid
                lo = mid + 1
            else:
                hi = mid - 1
        parts.append(_slice_hit(hit, start, best))
        start = best
    return parts


def _history_pages(
    hits: list[HeroPatchHit],
    *,
    emoji_map: dict[str, discord.Emoji] | None = None,
) -> list[list[HeroPatchHit]]:
    """Pack full patch text onto pages; overflow continues on the next page."""
    if not hits:
        return [[]]
    pieces: list[HeroPatchHit] = []
    for hit in hits:
        pieces.extend(
            _split_hit_to_fit(hit, HISTORY_PAGE_CHAR_BUDGET, emoji_map=emoji_map)
        )
    pages: list[list[HeroPatchHit]] = []
    current: list[HeroPatchHit] = []
    used = 0
    for piece in pieces:
        size = len(_history_hit_block(piece, emoji_map=emoji_map)) + 8
        overflow = current and (
            len(current) >= HISTORY_PER_PAGE or used + size > HISTORY_PAGE_CHAR_BUDGET
        )
        if overflow:
            pages.append(current)
            current = []
            used = 0
        current.append(piece)
        used += size
    if current:
        pages.append(current)
    return pages


def _page_index_for_hit(
    pages: list[list[HeroPatchHit]], hits: list[HeroPatchHit], hit_idx: int
) -> int:
    if not hits or not pages:
        return 0
    idx = max(0, min(hit_idx, len(hits) - 1))
    target = hits[idx]
    key = (target.patch_id or "", target.patch_date or "")
    for page_i, chunk in enumerate(pages):
        for piece in chunk:
            if (piece.patch_id or "", piece.patch_date or "") == key:
                return page_i
    return 0


def _history_nav_widgets(
    hits: list[HeroPatchHit],
    hero_label: str,
    page: int,
    total_pages: int,
    *,
    use_rows: bool,
    emoji_map: dict[str, discord.Emoji] | None = None,
) -> tuple[discord.ui.Button, discord.ui.Button, discord.ui.Select]:
    """Newer / Older + jump select. Clicks edit the same message."""
    prev_kw: dict = {
        "label": "Newer",
        "style": discord.ButtonStyle.secondary,
        "disabled": page <= 0,
    }
    next_kw: dict = {
        "label": "Older",
        "style": discord.ButtonStyle.secondary,
        "disabled": page >= total_pages - 1,
    }
    jump_kw: dict = {
        "placeholder": f"Jump to patch… ({page + 1}/{total_pages})",
        "min_values": 1,
        "max_values": 1,
        "options": _jump_options(hits, page, emoji_map=emoji_map),
    }
    if use_rows:
        prev_kw["row"] = 0
        next_kw["row"] = 0
        jump_kw["row"] = 1
    prev_btn = discord.ui.Button(**prev_kw)
    next_btn = discord.ui.Button(**next_kw)
    jump = discord.ui.Select(**jump_kw)

    async def on_prev(interaction: discord.Interaction) -> None:
        await _replace_hero_history(
            interaction,
            hits,
            hero_label,
            max(0, page - 1),
            emoji_map=emoji_map,
        )

    async def on_next(interaction: discord.Interaction) -> None:
        await _replace_hero_history(
            interaction,
            hits,
            hero_label,
            min(total_pages - 1, page + 1),
            emoji_map=emoji_map,
        )

    async def on_jump(interaction: discord.Interaction) -> None:
        try:
            idx = int(jump.values[0])
        except ValueError:
            idx = 0
        pages = _history_pages(hits, emoji_map=emoji_map)
        await _replace_hero_history(
            interaction,
            hits,
            hero_label,
            _page_index_for_hit(pages, hits, idx),
            emoji_map=emoji_map,
        )

    prev_btn.callback = on_prev
    next_btn.callback = on_next
    jump.callback = on_jump
    return prev_btn, next_btn, jump


def _attach_history_nav(
    view: discord.ui.LayoutView,
    hits: list[HeroPatchHit],
    hero_label: str,
    page: int,
    total_pages: int,
    *,
    emoji_map: dict[str, discord.Emoji] | None = None,
) -> None:
    """Keep results above the nav bar, in the same message."""
    prev_btn, next_btn, jump = _history_nav_widgets(
        hits,
        hero_label,
        page,
        total_pages,
        use_rows=False,
        emoji_map=emoji_map,
    )
    btn_row = discord.ui.ActionRow()
    btn_row.add_item(prev_btn)
    btn_row.add_item(next_btn)
    view.add_item(btn_row)
    if hits:
        jump_row = discord.ui.ActionRow()
        jump_row.add_item(jump)
        view.add_item(jump_row)


def _make_alert_button(
    hits: list[HeroPatchHit],
    hero_label: str,
    page: int,
    *,
    alert_on: bool,
    row: int | None = None,
    emoji_map: dict[str, discord.Emoji] | None = None,
) -> discord.ui.Button:
    kw: dict = {
        "label": "Stop alerts" if alert_on else "Notify me",
        "style": (
            discord.ButtonStyle.secondary
            if alert_on
            else discord.ButtonStyle.primary
        ),
    }
    if row is not None:
        kw["row"] = row
    btn = discord.ui.Button(**kw)

    async def on_alert(interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None:
            msg = "Alerts only work in a server."
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
            return
        on = interaction.client.db.toggle_hero_alert(
            guild.id, interaction.user.id, hero_alert_key(hero_label)
        )
        await _replace_hero_history(
            interaction,
            hits,
            hero_label,
            page,
            alert_on=on,
            emoji_map=emoji_map,
        )
        note = (
            f"Private alerts for **{hero_label}** are on. I’ll DM you when "
            "they’re patched — nothing extra is posted here for everyone else."
            if on
            else f"Stopped alerts for **{hero_label}**."
        )
        if on:
            note += await _dm_open_warning(interaction.user, guild, hero_label)
        try:
            await interaction.followup.send(note, ephemeral=True)
        except discord.HTTPException:
            pass

    btn.callback = on_alert
    return btn


def _attach_history_actions(
    view: discord.ui.LayoutView,
    hits: list[HeroPatchHit],
    hero_label: str,
    page: int,
    total_pages: int,
    *,
    alert_on: bool,
    show_alert: bool,
    emoji_map: dict[str, discord.Emoji] | None = None,
) -> None:
    if total_pages > 1:
        _attach_history_nav(
            view, hits, hero_label, page, total_pages, emoji_map=emoji_map
        )
    if show_alert:
        row = discord.ui.ActionRow()
        row.add_item(
            _make_alert_button(
                hits,
                hero_label,
                page,
                alert_on=alert_on,
                emoji_map=emoji_map,
            )
        )
        view.add_item(row)


def build_hero_history_layouts(
    hits: list[HeroPatchHit],
    *,
    hero_label: str,
    page: int = 0,
    per_page: int = HISTORY_PER_PAGE,
    alert_on: bool = False,
    show_alert: bool = True,
    emoji_map: dict[str, discord.Emoji] | None = None,
) -> tuple[list[discord.ui.LayoutView], int]:
    """
    Compact timeline — few patches per page, full text (overflow → next page).
    Returns (layouts_for_this_page, total_pages).
    """
    pages = _history_pages(hits, emoji_map=emoji_map)
    total = max(1, len(pages))
    page = max(0, min(page, total - 1))
    need_actions = (total > 1) or show_alert
    timeout = HISTORY_NAV_TIMEOUT if need_actions else None

    if not hits:
        view = discord.ui.LayoutView(timeout=timeout)
        empty = discord.ui.Container(accent_colour=OW_ORANGE)
        view.add_item(empty)
        empty.add_item(
            discord.ui.TextDisplay(
                f"**{hero_label}**\n"
                "_No retail balance changes in recent patch notes._"
            )
        )
        _attach_history_actions(
            view,
            hits,
            hero_label,
            page,
            total,
            alert_on=alert_on,
            show_alert=show_alert,
            emoji_map=emoji_map,
        )
        return [view], 1

    chunk = pages[page]
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

    view = discord.ui.LayoutView(timeout=timeout)
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
        container.add_item(
            discord.ui.TextDisplay(
                _history_hit_block(hit, emoji_map=emoji_map)
            )
        )

    _attach_history_actions(
        view,
        hits,
        hero_label,
        page,
        total,
        alert_on=alert_on,
        show_alert=show_alert,
        emoji_map=emoji_map,
    )

    return [view], total


def build_hero_history_embeds(
    hits: list[HeroPatchHit],
    *,
    hero_label: str,
    page: int = 0,
    per_page: int = HISTORY_PER_PAGE,
    emoji_map: dict[str, discord.Emoji] | None = None,
) -> tuple[list[discord.Embed], int]:
    pages = _history_pages(hits, emoji_map=emoji_map)
    total = max(1, len(pages))
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

    chunk = pages[page]
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

    body = "\n\n".join(
        _history_hit_block(h, emoji_map=emoji_map) for h in chunk
    )
    head.description = f"{head.description}\n\n{body}"[:4096]
    return [head], total


async def _replace_hero_history(
    interaction: discord.Interaction,
    hits: list[HeroPatchHit],
    hero_label: str,
    page: int,
    *,
    alert_on: bool | None = None,
    emoji_map: dict[str, discord.Emoji] | None = None,
) -> None:
    """Swap the current history message to another page — never send a new one."""
    if alert_on is None:
        alert_on = _alert_on_for(interaction, hero_label)
    try:
        layouts, _ = build_hero_history_layouts(
            hits,
            hero_label=hero_label,
            page=page,
            alert_on=alert_on,
            emoji_map=emoji_map,
        )
        await interaction.response.edit_message(**_v2_edit_kwargs(layouts[0]))
        return
    except Exception as exc:
        log.warning("Hero history layout edit failed: %s", exc)

    embeds, total = build_hero_history_embeds(
        hits, hero_label=hero_label, page=page, emoji_map=emoji_map
    )
    nav = HeroHistoryNavView(
        hits=hits,
        hero_label=hero_label,
        page=page,
        total_pages=total,
        alert_on=alert_on,
        emoji_map=emoji_map,
    )
    try:
        if interaction.response.is_done():
            await interaction.edit_original_response(
                content=None, embeds=embeds, view=nav
            )
        else:
            await interaction.response.edit_message(
                content=None, embeds=embeds, view=nav
            )
    except Exception as exc:
        log.warning("Hero history embed edit failed: %s", exc)
        if not interaction.response.is_done():
            await interaction.response.defer()


async def send_hero_history(
    interaction: discord.Interaction,
    hits: list[HeroPatchHit],
    *,
    hero_label: str,
    page: int = 0,
    edit_searching: bool = False,
) -> None:
    """One ephemeral history message (results above nav); optionally replace “Searching…”."""
    alert_on = _alert_on_for(interaction, hero_label)
    emoji_map = await _ability_emoji_map_for_hits(interaction.client, hits)
    try:
        layouts, _ = build_hero_history_layouts(
            hits,
            hero_label=hero_label,
            page=page,
            alert_on=alert_on,
            emoji_map=emoji_map,
        )
        if edit_searching:
            try:
                await interaction.edit_original_response(
                    **_v2_edit_kwargs(layouts[0])
                )
                return
            except Exception:
                pass
        await interaction.followup.send(view=layouts[0], ephemeral=True)
        return
    except Exception as exc:
        log.warning("Hero history layout failed: %s", exc)

    embeds, total = build_hero_history_embeds(
        hits, hero_label=hero_label, page=page, emoji_map=emoji_map
    )
    nav = HeroHistoryNavView(
        hits=hits,
        hero_label=hero_label,
        page=page,
        total_pages=total,
        alert_on=alert_on,
        emoji_map=emoji_map,
    )
    if edit_searching:
        try:
            await interaction.edit_original_response(
                content=None, embeds=embeds, view=nav
            )
            return
        except Exception:
            pass
    await interaction.followup.send(embeds=embeds, view=nav, ephemeral=True)


class HeroHistoryNavView(discord.ui.View):
    """Embed fallback: prev / next on the same message as the results."""

    def __init__(
        self,
        *,
        hits: list[HeroPatchHit],
        hero_label: str,
        page: int,
        total_pages: int,
        alert_on: bool = False,
        show_alert: bool = True,
        emoji_map: dict[str, discord.Emoji] | None = None,
    ) -> None:
        super().__init__(timeout=HISTORY_NAV_TIMEOUT)
        self.hits = hits
        self.hero_label = hero_label
        self.page = page
        self.total_pages = max(1, total_pages)
        if self.total_pages > 1:
            prev_btn, next_btn, jump = _history_nav_widgets(
                hits,
                hero_label,
                page,
                self.total_pages,
                use_rows=True,
                emoji_map=emoji_map,
            )
            self.add_item(prev_btn)
            self.add_item(next_btn)
            if hits:
                self.add_item(jump)
        if show_alert:
            self.add_item(
                _make_alert_button(
                    hits,
                    hero_label,
                    page,
                    alert_on=alert_on,
                    row=2,
                    emoji_map=emoji_map,
                )
            )


def _jump_options(
    hits: list[HeroPatchHit],
    current_page: int,
    *,
    emoji_map: dict[str, discord.Emoji] | None = None,
) -> list[discord.SelectOption]:
    pages = _history_pages(hits, emoji_map=emoji_map)
    first_key = None
    if pages and 0 <= current_page < len(pages) and pages[current_page]:
        first = pages[current_page][0]
        first_key = (first.patch_id or "", first.patch_date or "")
    opts: list[discord.SelectOption] = []
    for i, hit in enumerate(hits[:25]):
        label = hit.patch_date or hit.patch_id or f"Patch {i + 1}"
        mark = _hit_kind_mark(hit)
        key = (hit.patch_id or "", hit.patch_date or "")
        opts.append(
            discord.SelectOption(
                label=f"{label} · {mark}"[:100],
                value=str(i),
                description=(hit.lines[0][:80] if hit.lines else None),
                default=key == first_key,
            )
        )
    return opts or [
        discord.SelectOption(label="No patches", value="0", default=True)
    ]


class HeroRoleSelect(discord.ui.Select):
    def __init__(self, mode: BrowseMode = "history") -> None:
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
        self.mode = mode

    async def callback(self, interaction: discord.Interaction) -> None:
        role = self.values[0]
        heroes = HEROES_BY_ROLE.get(role) or ()
        await interaction.response.edit_message(
            content=f"**{role}** — choose a hero:",
            view=HeroPickView(role=role, heroes=heroes, mode=self.mode),
        )


class HeroPickSelect(discord.ui.Select):
    def __init__(
        self, role: str, heroes: tuple[str, ...], *, mode: BrowseMode = "history"
    ) -> None:
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
        self.heroes = heroes
        self.mode = mode

    async def callback(self, interaction: discord.Interaction) -> None:
        name = self.values[0]
        if name == "__none__":
            await interaction.response.send_message(
                "No heroes in that list.", ephemeral=True
            )
            return
        if self.mode == "alerts":
            await _toggle_alert_from_pick(interaction, name, self.role, self.heroes)
            return
        # Same message: Searching… → history (no extra ephemeral)
        await _deliver_history(interaction, name, replace_picker=True)


class HeroPickView(discord.ui.View):
    def __init__(
        self,
        *,
        role: str,
        heroes: tuple[str, ...],
        mode: BrowseMode = "history",
    ) -> None:
        super().__init__(timeout=120)
        self.mode = mode
        self.add_item(HeroPickSelect(role, heroes, mode=mode))
        back = discord.ui.Button(label="Back", style=discord.ButtonStyle.secondary)

        async def on_back(interaction: discord.Interaction) -> None:
            await interaction.response.edit_message(
                content=ALERT_PROMPT if mode == "alerts" else HISTORY_PROMPT,
                view=HeroHistoryBrowseView(mode=mode),
            )

        back.callback = on_back
        self.add_item(back)


class HeroHistoryBrowseView(discord.ui.View):
    """Ephemeral role → hero picker (opened from the patch forum button)."""

    def __init__(self, mode: BrowseMode = "history") -> None:
        super().__init__(timeout=120)
        self.add_item(HeroRoleSelect(mode=mode))


async def _open_private_browse(
    interaction: discord.Interaction, *, mode: BrowseMode
) -> None:
    """One ephemeral message — role → hero edits in place (no chat flood)."""
    prompt = ALERT_PROMPT if mode == "alerts" else HISTORY_PROMPT
    await interaction.response.send_message(
        prompt,
        view=HeroHistoryBrowseView(mode=mode),
        ephemeral=True,
    )


class OwHeroHistoryHubView(discord.ui.View):
    """Public hub for Search Hero Changes (survives restarts)."""

    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Browse heroes",
        style=discord.ButtonStyle.primary,
        custom_id="ow_hero_hist:browse",
        row=0,
    )
    async def browse_heroes(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await _open_private_browse(interaction, mode="history")

    @discord.ui.button(
        label="Notify me",
        style=discord.ButtonStyle.primary,
        custom_id="ow_hero_hist:notify",
        row=0,
    )
    async def notify_me(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await _open_private_browse(interaction, mode="alerts")

    @discord.ui.button(
        label="My alerts",
        style=discord.ButtonStyle.secondary,
        custom_id="ow_hero_hist:my_alerts",
        row=0,
    )
    async def my_alerts(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await _send_my_alerts(interaction)


class OwHubRoleSelect(discord.ui.Select):
    """Older hub posts with Pick a role… — open the same private browse message."""

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
            custom_id="ow_hero_hist:role",
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        # One private message starting at the chosen role (edits in place from there).
        role = self.values[0]
        heroes = HEROES_BY_ROLE.get(role) or ()
        await interaction.response.send_message(
            f"**{role}** — choose a hero:",
            view=HeroPickView(role=role, heroes=heroes, mode="history"),
            ephemeral=True,
        )
        try:
            await interaction.message.edit(view=OwHeroHistoryHubView())
        except discord.HTTPException:
            pass


class OwHubRoleSelectLegacyView(discord.ui.View):
    """Persistent handler for older hubs that still show Pick a role…"""

    def __init__(self) -> None:
        super().__init__(timeout=None)
        self.add_item(OwHubRoleSelect())


class OwLegacyHubHeroSelect(discord.ui.Select):
    """Keeps older 3-bar hub messages working until the post is republished."""

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


class OwLegacyHeroHistoryHubView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)
        self.add_item(OwLegacyHubHeroSelect("Tank", row=0))
        self.add_item(OwLegacyHubHeroSelect("Damage", row=1))
        self.add_item(OwLegacyHubHeroSelect("Support", row=2))


def build_hero_history_hub_layouts() -> list[discord.ui.LayoutView]:
    view = discord.ui.LayoutView(timeout=None)
    container = discord.ui.Container(accent_colour=OW_ORANGE)
    view.add_item(container)
    container.add_item(
        discord.ui.TextDisplay(
            "**Search Hero Changes**\n"
            "**Browse heroes** opens a private picker (role → hero in one message)."
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
            "Results stay private. **Notify me** DMs you when that hero is patched "
            "— this post stays quiet for everyone else."
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
            "Browse heroes opens a private picker (role → hero in one message).\n\n"
            f"{_tone_label('▲')} · {_tone_label('▼')}\n"
            "Results stay private. Notify me DMs you when that hero is patched "
            "— this post stays quiet for everyone else.\n\n"
            f"_Also `/hero` · [patch notes]({PATCH_URL})_"
        ),
        color=OW_ORANGE,
        url=PATCH_URL,
    )
    emb.set_footer(text="Patch Notes")
    return [emb]


async def _toggle_alert_from_pick(
    interaction: discord.Interaction,
    hero: str,
    role: str,
    heroes: tuple[str, ...],
) -> None:
    guild = interaction.guild
    if guild is None:
        await interaction.response.send_message(
            "This only works in a server.", ephemeral=True
        )
        return
    label = display_hero_name(hero)
    on = interaction.client.db.toggle_hero_alert(
        guild.id, interaction.user.id, hero_alert_key(hero)
    )
    if on:
        content = (
            f"Private alerts for **{label}** are on. I’ll DM you when they’re "
            "patched — nothing extra is posted here for everyone else.\n"
            f"**{role}** — pick another hero, or the same one to turn alerts off."
        )
        content += await _dm_open_warning(interaction.user, guild, label)
    else:
        content = (
            f"Stopped alerts for **{label}**.\n"
            f"**{role}** — choose a hero:"
        )
    await interaction.response.edit_message(
        content=content,
        view=HeroPickView(role=role, heroes=heroes, mode="alerts"),
    )


async def _dm_open_warning(
    user: discord.abc.User, guild: discord.Guild, label: str
) -> str:
    """Empty if DMs work; otherwise a note to allow server DMs."""
    try:
        await user.send(
            f"Private **{label}** alerts from **{guild.name}** are on. "
            "I’ll message you here when they’re patched — not in the forum."
        )
        return ""
    except discord.Forbidden:
        return (
            "\n\n⚠️ I couldn’t DM you. Allow DMs from server members "
            "or these alerts can’t reach you."
        )
    except discord.HTTPException:
        return ""


async def _send_my_alerts(interaction: discord.Interaction) -> None:
    guild = interaction.guild
    if guild is None:
        await interaction.response.send_message(
            "This only works in a server.", ephemeral=True
        )
        return
    keys = interaction.client.db.list_user_hero_alerts(guild.id, interaction.user.id)
    if not keys:
        await interaction.response.send_message(
            "You’re not subscribed to any hero alerts yet. Use **Notify me** "
            "or the button on a hero’s history.",
            ephemeral=True,
        )
        return
    names = [display_hero_name(k) for k in keys]
    listed = ", ".join(f"**{n}**" for n in names)
    await interaction.response.send_message(
        f"Private alerts (DM only): {listed}\n"
        "Use **Notify me** and pick a hero again to turn one off.",
        ephemeral=True,
    )


async def _send_hero_alert_dm(
    user: discord.abc.User,
    hit: HeroPatchHit,
    label: str,
    guild: discord.Guild,
    *,
    bot=None,
) -> bool:
    header = (
        f"**{label}** was patched — private alert from **{guild.name}**.\n"
        "_Not posted in Search Hero Changes, so the forum stays quiet._"
    )
    emoji_map: dict[str, discord.Emoji] = {}
    if bot is not None:
        emoji_map = await _ability_emoji_map_for_hits(bot, [hit])
    try:
        layouts, _ = build_hero_history_layouts(
            [hit],
            hero_label=label,
            page=0,
            alert_on=True,
            show_alert=False,
            emoji_map=emoji_map,
        )
        await user.send(content=header, view=layouts[0])
        return True
    except discord.Forbidden:
        log.info("Hero alert DM blocked for user %s", user.id)
        return False
    except Exception:
        try:
            embeds, _ = build_hero_history_embeds(
                [hit], hero_label=label, emoji_map=emoji_map
            )
            await user.send(content=header, embeds=embeds)
            return True
        except Exception as exc:
            log.warning("Hero alert DM failed for %s: %s", user.id, exc)
            return False


async def notify_hero_alert_subscribers(bot, guild: discord.Guild, summary) -> int:
    """DM each subscriber whose hero is in this retail patch. Never posts publicly."""
    from overwatch_patches import is_fun_mode_patch

    if summary is None or is_fun_mode_patch(summary) or not summary.heroes:
        return 0
    patch_id = summary.fingerprint or summary.patch_id or summary.date or ""
    if not patch_id:
        return 0
    sent = 0
    for hero in summary.heroes:
        key = hero_alert_key(hero.name)
        if not key:
            continue
        user_ids = bot.db.list_hero_alert_subscribers(guild.id, key)
        if not user_ids:
            continue
        hit = _hit_from_summary(
            summary, hero, index=0, latest_date=summary.date or ""
        )
        label = display_hero_name(hero.name)
        for uid in user_ids:
            if bot.db.was_hero_alert_sent(guild.id, uid, key, patch_id):
                continue
            user = guild.get_member(uid) or bot.get_user(uid)
            if user is None:
                try:
                    user = await bot.fetch_user(uid)
                except discord.HTTPException:
                    continue
            ok = await _send_hero_alert_dm(user, hit, label, guild, bot=bot)
            if ok:
                bot.db.mark_hero_alert_sent(guild.id, uid, key, patch_id)
                sent += 1
            await asyncio.sleep(0.35)
    if sent:
        log.info(
            "Sent %s private hero alerts in %s for %s",
            sent,
            guild.name,
            patch_id,
        )
    return sent


async def _deliver_history(
    interaction: discord.Interaction,
    hero: str,
    *,
    replace_picker: bool = False,
) -> None:
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
    elif replace_picker:
        # Stay on the same ephemeral (role → hero → results)
        await interaction.response.edit_message(
            content=f"🔍 Searching **{label}**…",
            view=None,
            embed=None,
        )
        edit_searching = True
    else:
        await interaction.response.send_message(
            f"🔍 Searching **{label}**…", ephemeral=True
        )
        edit_searching = True
    try:
        hits, _latest = await lookup_hero_patch_history(
            interaction.client,
            guild.id,
            hero,
            max_hits=HISTORY_MAX_HITS,
            max_months=HISTORY_MAX_MONTHS,
        )
    except Exception as exc:
        log.warning("Hero history lookup failed for %s: %s", hero, exc)
        msg = f"Could not load history for **{label}** right now."
        if edit_searching:
            try:
                await interaction.edit_original_response(content=msg, view=None)
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
        """One Patch Notes forum post with role → hero picker (unlocked for buttons)."""
        guild_id = channel.guild.id
        existing_thread_id = None
        if isinstance(channel, discord.ForumChannel):
            existing_thread_id = self.bot.db.get_ow_hero_history_thread_id(guild_id)

        messages, thread_id, _ = await post_ow_announcement(
            channel,
            thread_name=hero_history_thread_title(),
            layouts=build_hero_history_hub_layouts(),
            embeds_fallback=build_hero_history_hub_embeds,
            tag_names=OW_PATCH_TAG_NAMES,
            trailing_content="Private browse · alerts by DM:",
            trailing_view=OwHeroHistoryHubView(),
            existing_thread_id=existing_thread_id,
        )
        if thread_id is not None:
            self.bot.db.set_ow_hero_history_thread_id(guild_id, thread_id)
        return messages


async def setup(bot: DreamTeamBot) -> None:
    await bot.add_cog(OverwatchHeroHistoryCog(bot))
