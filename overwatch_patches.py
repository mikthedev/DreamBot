"""Overwatch patch notes monitor — daily fetch, concise hero cards, styled embeds."""

from __future__ import annotations

import logging
import re
import ssl
from dataclasses import dataclass, field
from html import unescape

import aiohttp
import certifi
import discord
from discord.ext import commands, tasks

import config

log = logging.getLogger("dream_team.overwatch")

PATCH_URL = config.OW_PATCH_URL
OW_ORANGE = discord.Color.from_rgb(249, 158, 26)
OW_BLUE = discord.Color.from_rgb(33, 143, 254)
USER_AGENT = "DreamTeamBot/1.0 (+discord; patch-notes monitor)"
_SSL_CTX = ssl.create_default_context(cafile=certifi.where())

ROLE_ORDER = ("Tank", "Damage", "Support")
ROLE_COLOR = {
    "Tank": discord.Color.from_rgb(242, 166, 50),
    "Damage": discord.Color.from_rgb(232, 84, 84),
    "Support": discord.Color.from_rgb(45, 190, 140),
}
ROLE_LABEL = {
    "Tank": "TANK",
    "Damage": "DAMAGE",
    "Support": "SUPPORT",
}
ROLE_HEADER = {
    "Tank": "───  🛡️  TANK  ───",
    "Damage": "───  ⚔️  DAMAGE  ───",
    "Support": "───  💚  SUPPORT  ───",
}


@dataclass
class ChangeLine:
    """One balance tweak, optionally mode-specific."""

    ability: str
    text: str
    mode: str | None = None  # "5v5" | "6v6" | None
    tone: str = "•"  # ▲ ▼ •
    icon_url: str | None = None  # ability / utility icon from Blizzard


@dataclass
class HeroChange:
    name: str
    role: str
    icon_url: str | None = None
    changes: list[ChangeLine] = field(default_factory=list)


@dataclass
class PatchSummary:
    patch_id: str
    date: str
    title: str
    heroes: list[HeroChange] = field(default_factory=list)
    url: str = PATCH_URL

    @property
    def fingerprint(self) -> str:
        return self.patch_id or self.title


def _strip_tags(html: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", html, flags=re.I)
    text = re.sub(r"</p>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    return unescape(re.sub(r"\s+", " ", text)).strip()


def _first(pattern: str, text: str, flags: int = 0) -> str:
    m = re.search(pattern, text, flags)
    return m.group(1).strip() if m else ""


def _tone_from(text: str) -> str:
    low = text.lower()
    buff = any(
        w in low
        for w in (
            "increased",
            "up from",
            "buff",
            "now grants",
            "added",
            "restores",
        )
    )
    nerf = any(
        w in low
        for w in ("reduced", "down from", "nerf", "decreased", "removed", "lower")
    )
    if buff and not nerf:
        return "▲"
    if nerf and not buff:
        return "▼"
    return "•"


def _compact_value(raw: str) -> tuple[str, str, str | None]:
    """Turn verbose Blizzard phrasing into a short readable line."""
    text = raw.strip().rstrip(".")
    mode = None
    m = re.search(r"\((5v5|6v6)\)\s*$", text, re.I)
    if m:
        mode = m.group(1).lower()
        text = text[: m.start()].strip().rstrip(".")

    def _sec(v: str) -> str:
        return re.sub(r"\s*seconds?$", "s", v.strip(), flags=re.I)

    # Cooldown reduced from 12 to 10 seconds
    m = re.search(
        r"^(?P<label>.+?)\s+(?:reduced|increased)\s+from\s+(?P<a>.+?)\s+to\s+(?P<b>.+)$",
        text,
        re.I,
    )
    if m:
        label = m.group("label").strip()
        a, b = m.group("a").strip(), m.group("b").strip()
        if re.search(r"seconds?$", b, re.I) or re.search(r"seconds?$", a, re.I):
            a = re.sub(r"\s*seconds?$", "", a, flags=re.I) + "s"
            b = re.sub(r"\s*seconds?$", "", b, flags=re.I) + "s"
        else:
            a, b = _sec(a), _sec(b)
        return label, f"{a} → {b}", mode

    m = re.search(
        r"^(?P<label>.+?)\s+reduced\s+(?P<a>\d+%?)\s+to\s+(?P<b>\d+%?)",
        text,
        re.I,
    )
    if m:
        return m.group("label").strip(), f"{m.group('a')} → {m.group('b')}", mode

    m = re.search(
        r"reduced to (?P<b>.+?) \(Down from (?P<a>.+?)\)",
        text,
        re.I,
    )
    if m:
        label = re.split(r"\s+reduced\s+to\s+", text, maxsplit=1, flags=re.I)[0].strip()
        return label or "Value", f"{_sec(m.group('a'))} → {_sec(m.group('b'))}", mode

    m = re.search(
        r"increased to (?P<b>.+?) \(Up from (?P<a>.+?)\)",
        text,
        re.I,
    )
    if m:
        label = re.split(r"\s+increased\s+to\s+", text, maxsplit=1, flags=re.I)[0].strip()
        return label or "Value", f"{_sec(m.group('a'))} → {_sec(m.group('b'))}", mode

    if len(text) > 90:
        text = text[:89].rstrip() + "…"
    return "", text, mode


def _make_change(
    ability: str, raw_li: str, *, icon_url: str | None = None
) -> ChangeLine | None:
    clean = _strip_tags(raw_li)
    if not clean:
        return None
    label, value, mode = _compact_value(clean)
    tone = _tone_from(clean)
    if label and "→" in value:
        text = f"**{label}** `{value}`"
    elif "→" in value:
        text = f"`{value}`"
    else:
        text = value
    return ChangeLine(
        ability=ability, text=text, mode=mode, tone=tone, icon_url=icon_url
    )



def _format_ability_block(ability: str, lines: list[ChangeLine]) -> str:
    """Ability name + each buff/nerf on its own line (no · clutter)."""
    shared = [c for c in lines if not c.mode]
    v5 = [c for c in lines if c.mode == "5v5"]
    v6 = [c for c in lines if c.mode == "6v6"]

    rows: list[str] = [f"**{ability}**"]
    for c in shared:
        rows.append(f"{c.tone}  {c.text}")

    if v5 or v6:
        if v5 and v6 and len(v5) == len(v6):
            for a, b in zip(v5, v6):
                rows.append(f"┣ **5v5**  {a.text}")
                rows.append(f"┗ **6v6**  {b.text}")
        else:
            for c in v5:
                rows.append(f"┣ **5v5**  {c.text}")
            for c in v6:
                rows.append(f"┗ **6v6**  {c.text}")

    return "\n".join(rows)


def _hero_changes_text(hero: HeroChange) -> str:
    """Patch details only (sits under the name + portrait row)."""
    by_ability: dict[str, list[ChangeLine]] = {}
    for ch in hero.changes:
        by_ability.setdefault(ch.ability, []).append(ch)
    return "\n\n".join(
        _format_ability_block(ability, lines)
        for ability, lines in by_ability.items()
    )


def _hero_section_text(hero: HeroChange) -> str:
    """Fallback single block (embeds): name + changes."""
    changes = _hero_changes_text(hero)
    return f"**{hero.name}**\n\n{changes}" if changes else f"**{hero.name}**"


def parse_latest_patch(html: str) -> PatchSummary | None:
    m = re.search(
        r'<div class="PatchNotes-patch PatchNotes-live">(.*?)<div class="PatchNotes-patch ',
        html,
        re.S,
    )
    if not m:
        m = re.search(
            r'<div class="PatchNotes-patch PatchNotes-live">(.*)$',
            html,
            re.S,
        )
    if not m:
        return None

    block = m.group(1)
    patch_id = _first(r'id="(patch-[^"]+)"', block) or _first(
        r'PatchNotes-date">([^<]+)', block
    )
    date = _first(r'PatchNotes-date">([^<]+)', block)
    title = _strip_tags(_first(r'PatchNotes-patchTitle"[^>]*>(.*?)</h3>', block, re.S))
    if not title:
        title = f"Overwatch Patch Notes – {date}" if date else "Overwatch Patch Notes"

    summary = PatchSummary(patch_id=patch_id or title, date=date, title=title)

    sections = re.split(r'<div class="PatchNotes-section ', block)[1:]
    mode = "retail"
    current_role = ""

    for sec in sections:
        sec_title = _strip_tags(_first(r'PatchNotes-sectionTitle"[^>]*>(.*?)</h4>', sec, re.S))
        low = sec_title.lower()

        if low in ("stadium updates", "bug fixes", "custom game updates"):
            mode = "skip"
            continue
        if mode == "skip":
            continue

        is_hero_section = sec.startswith("PatchNotes-section-hero_update") or (
            "hero_update" in sec[:60]
        )
        if sec_title in ROLE_ORDER:
            current_role = sec_title

        if not (is_hero_section or sec_title in ROLE_ORDER):
            continue

        heroes = re.finditer(
            r'<div class="PatchNotesHeroUpdate">'
            r'.*?<img class="PatchNotesHeroUpdate-icon" src="([^"]+)"[^>]*>'
            r'.*?<h5 class="PatchNotesHeroUpdate-name">([^<]+)</h5>'
            r"(.*?)</div>\s*</div>\s*(?=<div class=\"PatchNotesHeroUpdate\">|<div class=\"PatchNotes-section|$)",
            sec,
            re.S,
        )
        for hm in heroes:
            icon_url = hm.group(1).strip()
            name = hm.group(2).strip()
            body = hm.group(3)
            changes: list[ChangeLine] = []

            for ab_html in re.split(r'<div class="PatchNotesAbilityUpdate">', body)[1:]:
                ability_icon = _first(
                    r'PatchNotesAbilityUpdate-icon" src="([^"]+)"', ab_html
                ) or None
                ability_name = _first(
                    r'PatchNotesAbilityUpdate-name">([^<]+)', ab_html
                )
                if not ability_name:
                    continue
                ability_name = re.sub(
                    r"\s*[–—-]\s*(Major|Minor)\s+Perk\s*$",
                    "",
                    ability_name,
                    flags=re.I,
                )
                detail = _first(
                    r'PatchNotesAbilityUpdate-detailList">(.*?)</div>', ab_html, re.S
                )
                for li in re.findall(r"<li>(.*?)</li>", detail or "", re.S):
                    ch = _make_change(ability_name, li, icon_url=ability_icon)
                    if ch:
                        changes.append(ch)
                    if len(changes) >= 6:
                        break
                if len(changes) >= 6:
                    break

            if not changes:
                for li in re.findall(r"<li>(.*?)</li>", body, re.S)[:4]:
                    ch = _make_change("General", li, icon_url=None)
                    if ch:
                        changes.append(ch)

            if changes:
                summary.heroes.append(
                    HeroChange(
                        name=name,
                        role=current_role or "Hero",
                        icon_url=icon_url or None,
                        changes=changes,
                    )
                )

    # Retail only (first occurrence per hero name in role)
    seen: set[str] = set()
    unique: list[HeroChange] = []
    for h in summary.heroes:
        key = f"{h.role}:{h.name}"
        if key in seen:
            continue
        seen.add(key)
        unique.append(h)
    summary.heroes = unique
    return summary


async def fetch_patch_html(session: aiohttp.ClientSession | None = None) -> str:
    close = False
    if session is None:
        session = aiohttp.ClientSession(
            headers={"User-Agent": USER_AGENT, "Accept": "text/html"}
        )
        close = True
    try:
        async with session.get(
            PATCH_URL,
            timeout=aiohttp.ClientTimeout(total=45),
            ssl=_SSL_CTX,
        ) as resp:
            resp.raise_for_status()
            return await resp.text()
    finally:
        if close:
            await session.close()


async def fetch_latest_summary() -> PatchSummary | None:
    html = await fetch_patch_html()
    return parse_latest_patch(html)



def build_patch_layouts(
    summary: PatchSummary, *, preview: bool = False
) -> list[discord.ui.LayoutView]:
    """
    Text layout with a portrait thumbnail per hero and breathing room between them.
    Discord's 40-component cap may split a large patch into 2 messages.
    """
    by_role: dict[str, list[HeroChange]] = {r: [] for r in ROLE_ORDER}
    for h in summary.heroes:
        by_role.setdefault(h.role, []).append(h)

    date_label = summary.date or "Patch"
    if preview:
        header = f"🧪 **Preview**\n**[{date_label}]({summary.url})**"
    else:
        header = f"**[{date_label}]({summary.url})**"

    if not summary.heroes:
        view = discord.ui.LayoutView(timeout=None)
        view.add_item(
            discord.ui.TextDisplay(header + "\n_No retail hero balance in this drop._")
        )
        return [view]

    BUDGET = 38
    views: list[discord.ui.LayoutView] = []
    view = discord.ui.LayoutView(timeout=None)
    view.add_item(discord.ui.TextDisplay(header))

    def flush() -> None:
        nonlocal view
        views.append(view)
        view = discord.ui.LayoutView(timeout=None)
        view.add_item(
            discord.ui.TextDisplay(f"**[{date_label}]({summary.url})** · continued")
        )

    first_role = True
    for role in ROLE_ORDER:
        heroes = by_role.get(role, [])
        if not heroes:
            continue

        # Clear break between roles (divider + space)
        role_overhead = 3 if not first_role else 1  # seps + header
        if view._total_children + role_overhead + 3 > BUDGET and view._total_children > 2:
            flush()
            first_role = True

        if not first_role:
            if view._total_children + 2 > BUDGET:
                flush()
                first_role = True
            else:
                view.add_item(
                    discord.ui.Separator(
                        visible=True,
                        spacing=discord.SeparatorSpacing.large,
                    )
                )
                view.add_item(
                    discord.ui.Separator(
                        visible=False,
                        spacing=discord.SeparatorSpacing.large,
                    )
                )

        if view._total_children + 1 > BUDGET:
            flush()
            first_role = True

        view.add_item(
            discord.ui.TextDisplay(f"**{ROLE_HEADER.get(role, role)}**")
        )
        first_role = False

        for i, hero in enumerate(heroes):
            # Portrait aligns with name only; patch text sits underneath
            # Section(3) + TextDisplay(1) + optional Separator(1)
            want_sep = i < len(heroes) - 1
            cost = 4 + (1 if want_sep else 0)
            if view._total_children + cost > BUDGET:
                flush()
                view.add_item(
                    discord.ui.TextDisplay(f"**{ROLE_HEADER.get(role, role)}**")
                )

            if hero.icon_url:
                view.add_item(
                    discord.ui.Section(
                        f"**{hero.name}**",
                        accessory=discord.ui.Thumbnail(hero.icon_url),
                    )
                )
            else:
                view.add_item(discord.ui.TextDisplay(f"**{hero.name}**"))

            changes = _hero_changes_text(hero)
            if changes:
                view.add_item(discord.ui.TextDisplay(changes))

            if want_sep and view._total_children + 1 <= BUDGET:
                view.add_item(
                    discord.ui.Separator(
                        visible=False,
                        spacing=discord.SeparatorSpacing.large,
                    )
                )
            elif want_sep:
                flush()
                view.add_item(
                    discord.ui.TextDisplay(f"**{ROLE_HEADER.get(role, role)}**")
                )

    views.append(view)
    return views


def build_patch_embeds(summary: PatchSummary, *, preview: bool = False) -> list[discord.Embed]:
    """Fallback embeds if Components V2 layout is unavailable."""
    color = OW_BLUE if preview else OW_ORANGE
    head = discord.Embed(
        title=summary.date or "Overwatch patch",
        description="🧪 **Preview**" if preview else None,
        color=color,
        url=summary.url,
    )
    head.set_author(name="Overwatch")
    if not summary.heroes:
        return [head]

    by_role: dict[str, list[HeroChange]] = {r: [] for r in ROLE_ORDER}
    for h in summary.heroes:
        by_role.setdefault(h.role, []).append(h)

    embeds: list[discord.Embed] = [head]
    for role in ROLE_ORDER:
        heroes = by_role.get(role, [])
        if not heroes:
            continue
        emb = discord.Embed(color=ROLE_COLOR.get(role, color))
        emb.set_author(name=ROLE_HEADER.get(role, role))
        emb.description = "\n\n".join(_hero_section_text(h) for h in heroes)[:4096]
        embeds.append(emb)
    return embeds


class OverwatchPatchCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._session: aiohttp.ClientSession | None = None
        self.check_patches.start()

    def cog_unload(self) -> None:
        self.check_patches.cancel()
        if self._session and not self._session.closed:
            self.bot.loop.create_task(self._session.close())

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={"User-Agent": USER_AGENT, "Accept": "text/html"}
            )
        return self._session

    async def get_summary(self) -> PatchSummary | None:
        session = await self._get_session()
        html = await fetch_patch_html(session)
        return parse_latest_patch(html)

    async def post_to_channel(
        self,
        channel: discord.TextChannel,
        summary: PatchSummary,
        *,
        preview: bool = False,
    ) -> discord.Message:
        try:
            layouts = build_patch_layouts(summary, preview=preview)
            first: discord.Message | None = None
            for layout in layouts:
                msg = await channel.send(view=layout)
                if first is None:
                    first = msg
            assert first is not None
            return first
        except Exception as exc:
            log.warning("OW layout failed, using embeds: %s", exc)
            return await channel.send(
                embeds=build_patch_embeds(summary, preview=preview)
            )

    async def send_preview_ephemeral(self, interaction: discord.Interaction) -> None:
        summary = await self.get_summary()
        if summary is None:
            await interaction.followup.send(
                "Could not parse the patch notes page.", ephemeral=True
            )
            return
        try:
            layouts = build_patch_layouts(summary, preview=True)
            await interaction.followup.send(view=layouts[0], ephemeral=True)
            for layout in layouts[1:]:
                await interaction.followup.send(view=layout, ephemeral=True)
        except Exception as exc:
            log.warning("OW layout preview failed: %s", exc)
            await interaction.followup.send(
                embeds=build_patch_embeds(summary, preview=True),
                ephemeral=True,
            )

    async def announce_if_new(self, guild: discord.Guild) -> tuple[bool, str]:
        channel_id = self.bot.db.get_ow_patch_channel(guild.id)
        if not channel_id:
            return False, "No Overwatch patch channel set."
        channel = guild.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            return False, "Patch channel missing."

        try:
            summary = await self.get_summary()
        except Exception as exc:
            log.warning("OW patch fetch failed: %s", exc)
            return False, f"Fetch failed: {exc}"

        if summary is None:
            return False, "Could not parse patch notes page."

        if self.bot.db.was_ow_patch_announced(guild.id, summary.fingerprint):
            return False, f"Already posted `{summary.fingerprint}`."

        await self.post_to_channel(channel, summary, preview=False)
        self.bot.db.mark_ow_patch_announced(guild.id, summary.fingerprint)
        return True, summary.title

    @tasks.loop(hours=config.OW_PATCH_CHECK_HOURS)
    async def check_patches(self) -> None:
        for guild in self.bot.guilds:
            try:
                posted, detail = await self.announce_if_new(guild)
                if posted:
                    log.info("OW patch posted in %s: %s", guild.name, detail)
            except Exception as exc:
                log.warning("OW patch check failed for %s: %s", guild.name, exc)

    @check_patches.before_loop
    async def before_check(self) -> None:
        await self.bot.wait_until_ready()
