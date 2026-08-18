"""Counterwatch best one-tricks (META) — patch-notes-style role cards."""

from __future__ import annotations

import logging
import re
import ssl
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from html import unescape

import aiohttp
import certifi
import discord
from discord.ext import commands, tasks

import config
from ow_forum import (
    OW_META_TAG_NAMES,
    is_ow_destination,
    meta_thread_title,
    post_ow_announcement,
)

log = logging.getLogger("dream_team.ow_meta")

META_URL = config.OW_META_URL
USER_AGENT = "DreamTeamBot/1.0 (+discord; meta / best-onetricks)"
_SSL_CTX = ssl.create_default_context(cafile=certifi.where())

ROLE_ORDER = ("Tank", "Damage", "Support")
ROLE_COLOR = {
    "Tank": discord.Color.from_rgb(242, 166, 50),
    "Damage": discord.Color.from_rgb(232, 84, 84),
    "Support": discord.Color.from_rgb(45, 190, 140),
}
ROLE_HEADER = {
    "Tank": "🛡️  TANK",
    "Damage": "⚔️  DAMAGE",
    "Support": "💚  SUPPORT",
}
OW_ORANGE = discord.Color.from_rgb(249, 158, 26)


@dataclass
class MetaMention:
    name: str
    win_rate: str
    kind: str  # Rank-specific / Map pool pick / Counter pick
    note: str
    icon_url: str | None = None
    slug: str = ""


@dataclass
class MetaPick:
    name: str
    role: str
    tier: str
    win_rate: str
    why: str
    scores: dict[str, str] = field(default_factory=dict)
    icon_url: str | None = None
    slug: str = ""
    mentions: list[MetaMention] = field(default_factory=list)


@dataclass
class MetaSummary:
    season: str
    updated: str
    url: str = META_URL
    roles: dict[str, MetaPick] = field(default_factory=dict)

    @property
    def fingerprint(self) -> str:
        tops = "-".join(
            (self.roles[r].slug or self.roles[r].name if r in self.roles else "")
            for r in ROLE_ORDER
        )
        base = f"s{self.season}-{self.updated}-{tops}".lower()
        return re.sub(r"[^a-z0-9]+", "-", base).strip("-")

    @property
    def title(self) -> str:
        season = f"Season {self.season}" if self.season else "Overwatch"
        if self.updated:
            return f"{season} · {self.updated}"
        return season


def _strip_tags(html: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", html, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    return unescape(re.sub(r"\s+", " ", text)).strip()


def _thumb_url(url: str | None) -> str | None:
    if not url:
        return None
    if url.startswith("/"):
        url = "https://www.counterwatch.gg" + url
    if "?" in url:
        url = url.split("?", 1)[0]
    if "/full/" in url:
        url = url.replace("/full/", "/thumb/")
    return url


def _first_hero_img(block: str) -> tuple[str | None, str]:
    img = re.search(r'<img[^>]+src="([^"]+)"', block)
    icon = _thumb_url(img.group(1) if img else None)
    slug_m = re.search(r'/stats/overwatch/heroes/([^"/]+)', block)
    return icon, (slug_m.group(1) if slug_m else "")


def parse_meta_page(html: str) -> MetaSummary | None:
    season_m = re.search(r"Season\s+(\d+)", html)
    updated_m = re.search(
        r"(?:updated|Last updated)\s+([A-Za-z]+\s+\d{1,2},\s+\d{4})",
        html,
        re.I,
    )
    season = season_m.group(1) if season_m else ""
    updated = updated_m.group(1) if updated_m else ""

    parts = re.split(r"(?=<h2\b)", html, flags=re.I)
    summary = MetaSummary(season=season, updated=updated)
    score_keys = ("Win rate", "Consistency", "Vs meta", "Synergy", "Confidence")

    for part in parts:
        hm = re.match(r"<h2[^>]*>(.*?)</h2>", part, re.I | re.S)
        if not hm:
            continue
        heading = _strip_tags(hm.group(1))
        role_m = re.match(
            r"Best\s+(Tank|Damage|Support)\s+one\s+trick",
            heading,
            re.I,
        )
        if not role_m:
            continue
        role = role_m.group(1).title()
        if role not in ROLE_ORDER:
            continue

        plain = _strip_tags(part)
        # Top pick: Name Role Tier X … win% Why … scores
        top_m = re.search(
            rf"Best\s+{role}\s+one\s+trick\s+"
            rf"(?P<name>.+?)\s+{role}\s+Tier\s+(?P<tier>[SABCDEF])\s+"
            rf"(?:.+?\s+)?"
            rf"(?P<wr>\d+(?:\.\d+)?)\s*%\s+Win rate",
            plain,
            re.I,
        )
        if not top_m:
            log.warning("META: could not parse top pick for %s", role)
            continue

        why_m = re.search(
            rf"Why\s+{re.escape(top_m.group('name'))}\s+is the best\s+{role}\s+pick:\s*"
            rf"(?P<why>.+?)\s+Win rate\s+\d+",
            plain,
            re.I,
        )
        why = why_m.group("why").strip().rstrip(".") if why_m else ""
        why = re.sub(r"\s+,", ",", why)
        why = re.sub(r"\s+", " ", why).strip()

        scores: dict[str, str] = {}
        score_blob_m = re.search(
            r"Why .+? pick:\s*.+?\.\s*"
            r"Win rate\s+(?P<wr>\d+)\s+"
            r"Consistency\s+(?P<con>\d+)\s+"
            r"Vs meta\s+(?P<vm>\d+)\s+"
            r"Synergy\s+(?P<syn>\d+)\s+"
            r"Confidence\s+(?P<conf>\d+)",
            plain,
            re.I | re.S,
        )
        if score_blob_m:
            scores = {
                "Win rate": score_blob_m.group("wr"),
                "Consistency": score_blob_m.group("con"),
                "Vs meta": score_blob_m.group("vm"),
                "Synergy": score_blob_m.group("syn"),
                "Confidence": score_blob_m.group("conf"),
            }

        icon, slug = _first_hero_img(part)
        pick = MetaPick(
            name=top_m.group("name").strip(),
            role=role,
            tier=top_m.group("tier").upper(),
            win_rate=f"{top_m.group('wr')}%",
            why=why,
            scores=scores,
            icon_url=icon,
            slug=slug,
        )

        # Honourable mentions — parse the <li><a href="/heroes/..."> cards
        hon_html_m = re.search(
            r"Honourable mentions</h3>\s*<ul[^>]*>(.*?)</ul>",
            part,
            re.I | re.S,
        )
        if hon_html_m:
            for li in re.finditer(r"<li>(.*?)</li>", hon_html_m.group(1), re.S):
                card = li.group(1)
                slug_m = re.search(r'/stats/overwatch/heroes/([^"/]+)', card)
                name_m = re.search(r'<img[^>]+alt="([^"]+)"', card)
                if not name_m:
                    name_m = re.search(
                        r'text-white[^>]*>([^<]+)</div>',
                        card,
                    )
                wr_m = re.search(
                    r'tabular-nums[^>]*>\s*(\d+(?:\.\d+)?)\s*(?:<!--.*?-->)?\s*%',
                    card,
                    re.I | re.S,
                )
                kind_m = re.search(
                    r">(Rank-specific|Map pool pick|Counter pick)<",
                    card,
                    re.I,
                )
                note = ""
                note_m = re.search(r"<p[^>]*>(.*?)</p>", card, re.S | re.I)
                if note_m:
                    note = _strip_tags(note_m.group(1)).strip(" .")
                    note = re.sub(r"\s+", " ", note)
                    note = re.sub(r"\s+\.", ".", note)
                img_m = re.search(r'<img[^>]+src="([^"]+)"', card)
                if not name_m or not wr_m or not kind_m:
                    continue
                pick.mentions.append(
                    MetaMention(
                        name=unescape(name_m.group(1)).strip(),
                        win_rate=f"{wr_m.group(1)}%",
                        kind=kind_m.group(1),
                        note=note,
                        icon_url=_thumb_url(img_m.group(1) if img_m else None),
                        slug=slug_m.group(1) if slug_m else "",
                    )
                )

        summary.roles[role] = pick

    if not summary.roles:
        return None
    return summary


async def fetch_meta_html(session: aiohttp.ClientSession | None = None) -> str:
    own = session is None
    if own:
        session = aiohttp.ClientSession(
            headers={"User-Agent": USER_AGENT, "Accept": "text/html"}
        )
    assert session is not None
    try:
        async with session.get(
            META_URL,
            ssl=_SSL_CTX,
            timeout=aiohttp.ClientTimeout(total=45),
        ) as resp:
            resp.raise_for_status()
            return await resp.text()
    finally:
        if own:
            await session.close()


async def fetch_meta_summary(
    session: aiohttp.ClientSession | None = None,
) -> MetaSummary | None:
    html = await fetch_meta_html(session)
    return parse_meta_page(html)


def _pick_card_text(pick: MetaPick, *, role: str) -> str:
    """Featured OTP with role header baked in."""
    lines = [
        f"**{ROLE_HEADER.get(role, role)}**",
        "✦ **Featured one-trick**",
        f"**{pick.name}**",
    ]
    if pick.why:
        lines.append("")
        lines.append(pick.why)
    return "\n".join(lines)


def _emoji_name(name: str, slug: str = "") -> str:
    raw = (slug or name).lower()
    key = re.sub(r"[^a-z0-9]+", "_", raw).strip("_") or "hero"
    return f"ows_{key}"[:32]


def _mention_line(
    m: MetaMention,
    emoji_map: dict[str, discord.Emoji] | None = None,
) -> str:
    kind = {
        "Rank-specific": "Rank pick",
        "Map pool pick": "Map flex",
        "Counter pick": "Counter",
    }.get(m.kind, m.kind)
    em = None
    if emoji_map:
        em = emoji_map.get(_emoji_name(m.name, m.slug))
    prefix = f"{em}  " if em is not None else ""
    head = f"{prefix}**{m.name}** · **{m.win_rate}** · _{kind}_"
    if m.note:
        return f"{head}\n{m.note}"
    return head


def _mentions_block(
    mentions: list[MetaMention],
    emoji_map: dict[str, discord.Emoji] | None = None,
) -> str:
    if not mentions:
        return ""
    lines = ["**Honourable mentions**"]
    for m in mentions:
        lines.append("")
        lines.append(_mention_line(m, emoji_map))
    return "\n".join(lines)


def _meta_intro(summary: MetaSummary, *, preview: bool) -> str:
    date_label = summary.updated or "latest"
    if preview:
        head = f"🧪 **Preview** · **[{date_label}]({summary.url})**"
    else:
        head = f"**[{date_label}]({summary.url})**"
    season = f"Season {summary.season}" if summary.season else "Current season"
    blurb = (
        f"**{season} · Best heroes to main**\n"
        "Picks weigh win rate, consistency across matches, and fit in the current "
        "meta — including the trade-offs. Treat this as a statistical guide to who's "
        "strong right now, not a must-pick list: **skill on the hero matters most.**"
    )
    return f"{head}\n{blurb}"


def build_meta_layouts(
    summary: MetaSummary,
    *,
    preview: bool = False,
    emoji_map: dict[str, discord.Emoji] | None = None,
) -> list[discord.ui.LayoutView]:
    """
    Single starter message.

    Featured pick keeps a portrait thumbnail; honourable mentions use app emojis
    so we can afford a real Separator under the featured card.
    Budget: header(1) + 3×(container(1) + featured(3) + sep(1) + mentions text(1)) = 19.
    """
    view = discord.ui.LayoutView(timeout=None)
    view.add_item(discord.ui.TextDisplay(_meta_intro(summary, preview=preview)))

    for role in ROLE_ORDER:
        pick = summary.roles.get(role)
        if pick is None:
            continue
        colour = ROLE_COLOR.get(role, OW_ORANGE)

        container = discord.ui.Container(accent_colour=colour)
        view.add_item(container)

        card = _pick_card_text(pick, role=role)
        if pick.icon_url:
            container.add_item(
                discord.ui.Section(
                    card,
                    accessory=discord.ui.Thumbnail(pick.icon_url),
                )
            )
        else:
            container.add_item(discord.ui.TextDisplay(card))

        container.add_item(
            discord.ui.Separator(
                visible=True,
                spacing=discord.SeparatorSpacing.large,
            )
        )

        mentions = _mentions_block(pick.mentions, emoji_map)
        if mentions:
            container.add_item(discord.ui.TextDisplay(mentions))

    if view._total_children > 40:
        log.warning(
            "META layout has %s components (cap 40) — content may fail to send",
            view._total_children,
        )
    return [view]


def build_meta_embeds(
    summary: MetaSummary,
    *,
    preview: bool = False,
    emoji_map: dict[str, discord.Emoji] | None = None,
) -> list[discord.Embed]:
    head = discord.Embed(
        title="Best heroes to main",
        description=_meta_intro(summary, preview=preview),
        color=OW_ORANGE,
        url=summary.url,
    )
    embeds = [head]
    for role in ROLE_ORDER:
        pick = summary.roles.get(role)
        if pick is None:
            continue
        body = [_pick_card_text(pick, role=role)]
        mentions = _mentions_block(pick.mentions, emoji_map)
        if mentions:
            body.append(mentions)
        emb = discord.Embed(
            description="\n\n".join(body)[:4096],
            color=ROLE_COLOR.get(role, OW_ORANGE),
        )
        if pick.icon_url:
            emb.set_thumbnail(url=pick.icon_url)
        embeds.append(emb)
    return embeds


class OverwatchMetaCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._session: aiohttp.ClientSession | None = None
        self.check_meta.start()

    def cog_unload(self) -> None:
        self.check_meta.cancel()
        if self._session and not self._session.closed:
            self.bot.loop.create_task(self._session.close())

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={"User-Agent": USER_AGENT, "Accept": "text/html"}
            )
        return self._session

    async def get_summary(self) -> MetaSummary | None:
        session = await self._get_session()
        return await fetch_meta_summary(session)

    def _destination_channel_id(self, guild_id: int) -> int | None:
        return self.bot.db.get_ow_tier_channel(guild_id) or self.bot.db.get_ow_patch_channel(
            guild_id
        )

    def _mention_heroes(self, summary: MetaSummary):
        """TierHero stubs so we can reuse the tier-list app-emoji pipeline."""
        from overwatch_tierlist import TierHero

        heroes: list = []
        for pick in summary.roles.values():
            for m in pick.mentions:
                heroes.append(
                    TierHero(
                        name=m.name,
                        role=pick.role,
                        win_rate=m.win_rate,
                        pick_rate="",
                        slug=m.slug,
                        icon_url=m.icon_url,
                    )
                )
        return heroes

    async def ensure_mention_emojis(
        self, summary: MetaSummary
    ) -> dict[str, discord.Emoji]:
        # Prefer official Blizzard portraits on featured picks + mentions
        try:
            session = await self._get_session()
            from overwatch_tierlist import (
                fetch_blizzard_hero_icons,
                _blizzard_icon_indexes,
                _normalize_hero_token,
            )

            icons = await fetch_blizzard_hero_icons(session)
            by_id, by_name = _blizzard_icon_indexes(icons)

            def _blizz(name: str, slug: str = "") -> str | None:
                for token in (
                    (slug or "").lower(),
                    _normalize_hero_token(slug),
                    _normalize_hero_token(name),
                ):
                    if token and token in by_id:
                        return by_id[token]
                    if token and token in by_name:
                        return by_name[token]
                return None

            for pick in summary.roles.values():
                blizz = _blizz(pick.name, pick.slug)
                if blizz:
                    pick.icon_url = blizz
                for m in pick.mentions:
                    blizz_m = _blizz(m.name, m.slug)
                    if blizz_m:
                        m.icon_url = blizz_m
        except Exception as exc:
            log.warning("META Blizzard icon enrich failed: %s", exc)

        heroes = self._mention_heroes(summary)
        if not heroes:
            return {
                e.name: e for e in await self.bot.fetch_application_emojis()
            }
        tier_cog = self.bot.get_cog("OverwatchTierCog")
        if tier_cog is None:
            return {
                e.name: e for e in await self.bot.fetch_application_emojis()
            }
        from overwatch_tierlist import TierListSummary

        stub = TierListSummary(season="0", updated="")
        stub.tiers = {"S": heroes}
        return await tier_cog.ensure_hero_emojis(stub)

    async def post_to_channel(
        self,
        channel: discord.TextChannel | discord.ForumChannel,
        summary: MetaSummary,
        *,
        preview: bool = False,
        existing_thread_id: int | None = None,
    ) -> tuple[list[discord.Message], int | None]:
        emoji_map = await self.ensure_mention_emojis(summary)
        layouts = build_meta_layouts(
            summary, preview=preview, emoji_map=emoji_map
        )
        return await post_ow_announcement(
            channel,
            thread_name=meta_thread_title(season=summary.season),
            layouts=layouts,
            embeds_fallback=lambda: build_meta_embeds(
                summary, preview=preview, emoji_map=emoji_map
            ),
            tag_names=OW_META_TAG_NAMES,
            existing_thread_id=existing_thread_id,
        )

    async def delete_live_messages(
        self, channel: discord.TextChannel, guild_id: int
    ) -> None:
        for mid in self.bot.db.get_ow_meta_message_ids(guild_id):
            try:
                msg = await channel.fetch_message(mid)
                await msg.delete()
            except discord.NotFound:
                pass
            except discord.HTTPException as exc:
                log.warning("Could not delete old OW META message %s: %s", mid, exc)

    async def publish_live(
        self,
        channel: discord.TextChannel | discord.ForumChannel,
        summary: MetaSummary,
    ) -> list[discord.Message]:
        guild_id = channel.guild.id
        existing_thread_id = None
        if isinstance(channel, discord.TextChannel):
            await self.delete_live_messages(channel, guild_id)
        else:
            existing_thread_id = self.bot.db.get_ow_meta_thread_id(guild_id)

        messages, thread_id, _ = await self.post_to_channel(
            channel,
            summary,
            preview=False,
            existing_thread_id=existing_thread_id,
        )
        if thread_id is not None:
            self.bot.db.set_ow_meta_thread_id(guild_id, thread_id)
        self.bot.db.save_ow_meta_live(
            guild_id,
            summary.fingerprint,
            [m.id for m in messages],
        )
        return messages

    async def send_preview_ephemeral(self, interaction: discord.Interaction) -> None:
        summary = await self.get_summary()
        if summary is None:
            await interaction.followup.send(
                "Could not parse the Counterwatch one-tricks page.",
                ephemeral=True,
            )
            return
        try:
            emoji_map = await self.ensure_mention_emojis(summary)
            layouts = build_meta_layouts(
                summary, preview=True, emoji_map=emoji_map
            )
            await interaction.followup.send(view=layouts[0], ephemeral=True)
            for layout in layouts[1:]:
                await interaction.followup.send(view=layout, ephemeral=True)
        except Exception as exc:
            log.warning("OW META preview failed: %s", exc)
            try:
                emoji_map = await self.ensure_mention_emojis(summary)
            except Exception:
                emoji_map = None
            await interaction.followup.send(
                embeds=build_meta_embeds(
                    summary, preview=True, emoji_map=emoji_map
                ),
                ephemeral=True,
            )

    def _due_for_post(self, guild_id: int) -> bool:
        last = self.bot.db.get_ow_meta_last_posted(guild_id)
        if last is None:
            return True
        interval = timedelta(days=config.OW_META_INTERVAL_DAYS)
        return datetime.now(timezone.utc) - last >= interval

    async def announce_if_due(self, guild: discord.Guild) -> tuple[bool, str]:
        channel_id = self._destination_channel_id(guild.id)
        if not channel_id:
            return False, "No Overwatch forum/channel set for META."
        channel = guild.get_channel(channel_id)
        if not is_ow_destination(channel):
            return False, "META channel missing (set tier or patch forum)."

        if not self._due_for_post(guild.id):
            last = self.bot.db.get_ow_meta_last_posted(guild.id)
            return False, f"Not due yet (last {last})."

        try:
            summary = await self.get_summary()
        except Exception as exc:
            log.warning("OW META fetch failed: %s", exc)
            return False, f"Fetch failed: {exc}"

        if summary is None:
            return False, "Could not parse one-tricks page."

        last_id = self.bot.db.get_ow_meta_last_id(guild.id)
        if last_id and last_id == summary.fingerprint and self.bot.db.get_ow_meta_thread_id(guild.id):
            # Still due by calendar, but content unchanged — refresh stamp, skip spam
            self.bot.db.touch_ow_meta_schedule(guild.id)
            return False, f"Unchanged `{summary.fingerprint}`."

        await self.publish_live(channel, summary)
        return True, summary.title

    @tasks.loop(hours=config.OW_META_CHECK_HOURS)
    async def check_meta(self) -> None:
        for guild in self.bot.guilds:
            try:
                posted, detail = await self.announce_if_due(guild)
                if posted:
                    log.info("OW META posted in %s: %s", guild.name, detail)
            except Exception as exc:
                log.warning("OW META check failed for %s: %s", guild.name, exc)

    @check_meta.before_loop
    async def before_check(self) -> None:
        await self.bot.wait_until_ready()
