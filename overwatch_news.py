"""Overwatch general news from Bluesky — filtered, no source shout-outs."""

from __future__ import annotations

import asyncio
import io
import logging
import re
import ssl
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import aiohttp
import certifi
import discord
from discord.ext import commands, tasks

import config
from ow_forum import (
    OW_NEWS_TAG_NAMES,
    close_forum_post,
    forum_thread_name,
    is_ow_destination,
    lock_thread_for_reactions_only,
    resolve_forum_tags,
)

log = logging.getLogger("dream_team.ow_news")

USER_AGENT = "DreamTeamBot/1.0 (+discord; overwatch news)"
_SSL_CTX = ssl.create_default_context(cafile=certifi.where())
BSKY_FEED = "https://public.api.bsky.app/xrpc/app.bsky.feed.getAuthorFeed"
BSKY_THREAD = "https://public.api.bsky.app/xrpc/app.bsky.feed.getPostThread"
BSKY_POST_URL_RE = re.compile(
    r"""(?xi)
    https?://(?:www\.)?bsky\.app/profile/
    (?P<handle>[^/\s?#]+)/post/(?P<rkey>[A-Za-z0-9]+)
    """
)

# Heavy deny-list: cosmetics, shop, esports, patch dumps, fluff
_DENY = re.compile(
    r"""
    \b(
        shop|merch|merchandise|tumbler|sweatshirt|poster\b|
        skin\s*promotion|legendary\s+skin|weapon\s+skin|new\s+skin|skins?\b|
        frostmourne|cyberhog|partner\s+team\s+skins?|
        patch\s*notes|balance\s+patch|
        owcs|championship|midseason|ewc\b|esports|
        collab|collaboration|survey\b|
        maximilien|vault\b|
        loot\s*box|lootboxes|loot\s*hunt|
        birthday|release\s+schedule|
        eligible\s+purchase|any\s+eligible\s+purchase|
        free\s+loot
    )\b
    """,
    re.I | re.X,
)

# Must match at least one allow signal (interesting gameplay / map / mode news)
_ALLOW = re.compile(
    r"""
    \b(
        map\s+rework|reworked\s+\w+|map\s+changes?|jump\s+pad|
        first\s+look\s+at\s+new\s+\w+\s+map|
        under\s+attack|statue\b|jiggly\s+pig|
        6v6|2-2-2|dynamic\s+queue|quick\s+play|hacked|
        stadium|development\s+update|
        new\s+hero|hero\s+teaser|first\s+look\s+at\s+\w+|
        mech\b|meka\b
    )\b
    |
    first\s+look\s+at\s+new\s+
    """,
    re.I | re.X,
)


@dataclass
class NewsItem:
    uri: str
    cid: str
    created_at: datetime
    text: str
    image_urls: list[str] = field(default_factory=list)
    video_urls: list[str] = field(default_factory=list)

    @property
    def age(self) -> timedelta:
        return datetime.now(timezone.utc) - self.created_at

    @property
    def has_media(self) -> bool:
        return bool(self.image_urls or self.video_urls)

    @property
    def has_video(self) -> bool:
        return bool(self.video_urls)

    @property
    def web_url(self) -> str | None:
        return bsky_web_url_from_at_uri(self.uri)

    @property
    def title(self) -> str:
        line = self.text.strip().split("\n", 1)[0].strip()
        line = re.sub(r"\s+", " ", line)
        line = re.sub(r"#\w+", "", line)
        # Drop trailing decorative emoji / flags / arrows (incl. variation selectors)
        line = re.sub(
            r"(?:[\U0001F300-\U0001FAFF\U00002700-\U000027BF\U0001F1E0-\U0001F1FF"
            r"\U00002B00-\U00002BFF\U00002190-\U000021FF]\ufe0f?)+$",
            "",
            line,
        ).strip()
        line = re.sub(r"(?i)\s+in\s*$", "", line)
        return forum_thread_name(line.strip(" -·|,") or "Overwatch news")


def passes_news_filter(text: str, *, has_media: bool = True) -> bool:
    """Return True only for interesting OW news — deny-list first, then allow-list.

    News posts without image/video attachments are skipped (avoids bare text dupes).
    """
    if not has_media:
        return False
    raw = (text or "").strip()
    if len(raw) < 8:
        return False
    if _DENY.search(raw):
        return False
    if not _ALLOW.search(raw):
        return False
    return True


_ENGAGEMENT_LINE = re.compile(
    r"""(?ix)^\s*(
        which\ one\ is\ your\ favorite|
        what\ map\ do\ you\ think|
        are\ you\ excited|
        follow\ us
    ).*$"""
)

_MAP_CATCHUP = re.compile(
    r"""(?ix)\b(
        map\s+rework|map\s+changes?|jump\s+pad|under\s+attack|
        statue|jiggly\s+pig|
        busan|eichenwalde|para[ií]so
    )\b"""
)


def clean_news_text(text: str) -> str:
    """Discord body: keep valuable detail only. Title-only Bluesky blurbs → empty."""
    out = (text or "").strip()
    if not out:
        return ""

    out = re.sub(
        r"(?im)^\s*(follow us\s*)?@?owcavalry\b.*$",
        "",
        out,
    )
    out = re.sub(r"(?i)\bvia:\s*owcavalry\b", "", out)
    out = re.sub(r"(?i)\boverwatch cavalry\b", "", out)
    out = re.sub(r"#\w+", "", out)

    kept_lines: list[str] = []
    for line in out.splitlines():
        if _ENGAGEMENT_LINE.search(line):
            continue
        cleaned = re.sub(r"[ \t]{2,}", " ", line).rstrip()
        kept_lines.append(cleaned)
    out = "\n".join(kept_lines)
    out = re.sub(r"\n{3,}", "\n\n", out).strip()

    blocks = [b.strip() for b in re.split(r"\n\s*\n", out) if b.strip()]
    if not blocks:
        return ""

    # First block is the Bluesky headline — already used as the forum title
    detail = [b for b in blocks[1:] if b]
    if not detail:
        return ""

    # Drop leftover emoji-only / punctuation-only blocks
    valuable: list[str] = []
    for block in detail:
        plain = re.sub(
            r"[\U0001F300-\U0001FAFF\U00002700-\U000027BF\U0001F1E0-\U0001F1FF]",
            "",
            block,
        )
        plain = re.sub(r"\s+", " ", plain).strip(" -·|,")
        if len(plain) < 12:
            continue
        valuable.append(block.strip())

    return "\n\n".join(valuable).strip()


def _parse_created(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _image_urls_from_post(post: dict) -> list[str]:
    urls: list[str] = []
    emb = post.get("embed") or {}
    et = emb.get("$type") or ""

    def add_images(images: list) -> None:
        for im in images or []:
            u = im.get("fullsize") or im.get("thumb")
            if u:
                urls.append(u)

    if "app.bsky.embed.images" in et:
        add_images(emb.get("images") or [])
    elif "app.bsky.embed.recordWithMedia" in et:
        media = emb.get("media") or {}
        mt = media.get("$type") or ""
        if "images" in mt:
            add_images(media.get("images") or [])
        elif "video" in mt:
            thumb = media.get("thumbnail")
            if thumb:
                urls.append(thumb)
    elif "app.bsky.embed.video" in et:
        thumb = emb.get("thumbnail")
        if thumb:
            urls.append(thumb)
    elif "app.bsky.embed.external" in et:
        # Prefer downloading linked direct video; keep thumb only as image fallback
        external = emb.get("external") or {}
        uri = (external.get("uri") or "").strip()
        from media_attach import extract_direct_video_urls, is_youtube_url

        if uri and not is_youtube_url(uri) and not extract_direct_video_urls(uri):
            thumb = external.get("thumb")
            if thumb:
                urls.append(thumb)
    return urls[:10]


def _video_urls_from_post(post: dict) -> list[str]:
    """Bluesky native video playlists + direct video links (no YouTube)."""
    from media_attach import collect_video_candidates, is_youtube_url

    urls: list[str] = []
    emb = post.get("embed") or {}
    et = emb.get("$type") or ""
    rec = post.get("record") or {}
    text = (rec.get("text") or "").strip()

    def add_video_embed(blob: dict) -> None:
        playlist = (blob.get("playlist") or "").strip()
        if playlist and playlist not in urls:
            urls.append(playlist)

    if "app.bsky.embed.video" in et:
        add_video_embed(emb)
    elif "app.bsky.embed.recordWithMedia" in et:
        media = emb.get("media") or {}
        if "video" in (media.get("$type") or ""):
            add_video_embed(media)
    elif "app.bsky.embed.external" in et:
        uri = ((emb.get("external") or {}).get("uri") or "").strip()
        if uri and not is_youtube_url(uri):
            urls.append(uri)

    return collect_video_candidates(text=text, explicit_urls=urls)


def extract_bsky_post_urls(text: str) -> list[str]:
    return list(dict.fromkeys(m.group(0) for m in BSKY_POST_URL_RE.finditer(text or "")))


def bsky_web_url_from_at_uri(uri: str) -> str | None:
    """at://actor/app.bsky.feed.post/rkey → https://bsky.app/profile/.../post/..."""
    match = re.match(
        r"^at://([^/\s]+)/app\.bsky\.feed\.post/([A-Za-z0-9]+)$",
        (uri or "").strip(),
    )
    if not match:
        return None
    return f"https://bsky.app/profile/{match.group(1)}/post/{match.group(2)}"


def _append_link(body: str, link: str) -> str:
    link = (link or "").strip()
    if not link:
        return body or ""
    if link in (body or ""):
        return body or ""
    return f"{body}\n\n{link}".strip() if body else link


def bsky_post_at_uri(url: str) -> str | None:
    match = BSKY_POST_URL_RE.search(url or "")
    if not match:
        return None
    return (
        f"at://{match.group('handle')}/app.bsky.feed.post/{match.group('rkey')}"
    )


async def resolve_bsky_post_url(
    session: aiohttp.ClientSession, url: str
) -> NewsItem | None:
    """
    Turn a public bsky.app/profile/.../post/... link into media URLs via the API.
    Custom posts paste these links; they are not downloadable as files themselves.
    """
    at_uri = bsky_post_at_uri(url)
    if not at_uri:
        return None
    try:
        async with session.get(
            BSKY_THREAD,
            params={"uri": at_uri, "depth": "0"},
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            ssl=_SSL_CTX,
            timeout=aiohttp.ClientTimeout(total=20),
        ) as resp:
            if resp.status != 200:
                log.warning(
                    "Bluesky getPostThread %s → HTTP %s", at_uri, resp.status
                )
                return None
            data = await resp.json()
    except Exception as exc:
        log.warning("Bluesky resolve failed for %s: %s", url[:80], exc)
        return None

    thread = data.get("thread") or {}
    post = thread.get("post")
    if not isinstance(post, dict):
        return None
    item = parse_feed_item({"post": post})
    if item is None:
        return None
    log.info(
        "Resolved Bluesky post → videos=%s images=%s",
        len(item.video_urls),
        len(item.image_urls),
    )
    return item


def parse_feed_item(item: dict) -> NewsItem | None:
    post = item.get("post") or {}
    rec = post.get("record") or {}
    uri = post.get("uri") or ""
    if not uri:
        return None
    created = _parse_created(rec.get("createdAt") or post.get("indexedAt"))
    if created is None:
        return None
    text = (rec.get("text") or "").strip()
    return NewsItem(
        uri=uri,
        cid=post.get("cid") or "",
        created_at=created,
        text=text,
        image_urls=_image_urls_from_post(post),
        video_urls=_video_urls_from_post(post),
    )


async def fetch_author_feed(
    session: aiohttp.ClientSession,
    *,
    actor: str,
    limit: int = 40,
) -> list[NewsItem]:
    params = {"actor": actor, "limit": str(limit)}
    async with session.get(
        BSKY_FEED,
        params=params,
        ssl=_SSL_CTX,
        timeout=aiohttp.ClientTimeout(total=45),
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    ) as resp:
        resp.raise_for_status()
        data = await resp.json()
    items: list[NewsItem] = []
    for raw in data.get("feed") or []:
        parsed = parse_feed_item(raw)
        if parsed is not None:
            items.append(parsed)
    return items


async def download_images(
    session: aiohttp.ClientSession, urls: list[str]
) -> list[discord.File]:
    files: list[discord.File] = []
    for i, url in enumerate(urls):
        try:
            async with session.get(
                url,
                ssl=_SSL_CTX,
                timeout=aiohttp.ClientTimeout(total=30),
                headers={"User-Agent": USER_AGENT},
            ) as resp:
                if resp.status != 200:
                    continue
                ctype = (resp.headers.get("Content-Type") or "").lower()
                data = await resp.read()
            ext = ".jpg"
            if "png" in ctype:
                ext = ".png"
            elif "webp" in ctype:
                ext = ".webp"
            elif "gif" in ctype:
                ext = ".gif"
            files.append(
                discord.File(io.BytesIO(data), filename=f"ow_news_{i}{ext}")
            )
        except Exception as exc:
            log.warning("News image download failed: %s", exc)
    return files


class OverwatchNewsCog(commands.Cog):
    """Forum news posts under 📰 News — filtered Bluesky feed, hourly checks."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._session: aiohttp.ClientSession | None = None
        self.check_news.start()
        self.close_due_news.start()

    def cog_unload(self) -> None:
        self.check_news.cancel()
        self.close_due_news.cancel()
        if self._session and not self._session.closed:
            self.bot.loop.create_task(self._session.close())

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={"User-Agent": USER_AGENT, "Accept": "application/json"}
            )
        return self._session

    def _destination_channel_id(self, guild_id: int) -> int | None:
        return (
            self.bot.db.get_ow_news_channel(guild_id)
            or self.bot.db.get_ow_tier_channel(guild_id)
            or self.bot.db.get_ow_patch_channel(guild_id)
        )

    async def fetch_items(self) -> list[NewsItem]:
        session = await self._get_session()
        return await fetch_author_feed(
            session, actor=config.OW_NEWS_BSKY_ACTOR, limit=50
        )

    def filter_items(self, items: list[NewsItem]) -> list[NewsItem]:
        return [
            i for i in items if passes_news_filter(i.text, has_media=i.has_media)
        ]

    async def publish_item(
        self,
        channel: discord.ForumChannel | discord.TextChannel,
        item: NewsItem,
    ) -> discord.Thread | discord.Message | None:
        body = clean_news_text(item.text)
        if not item.has_media:
            log.info("Skipping news without media: %s", item.title)
            return None

        session = await self._get_session()
        files: list[discord.File] = []

        if item.has_video:
            # Don't download/transcode video — just share a watchable Bluesky link.
            link = item.web_url
            if not link and item.video_urls:
                # Prefer non-playlist URLs if any; otherwise skip raw m3u8
                for u in item.video_urls:
                    if "playlist.m3u8" not in u and not u.startswith(
                        "https://video.bsky.app/"
                    ):
                        link = u
                        break
            if link:
                body = _append_link(body, link)
            log.info("News video → link only: %s", link or item.uri)
        else:
            files = await download_images(session, item.image_urls)

        if not files and not body:
            log.warning("Skipping news — nothing to post: %s", item.title)
            return None
        if item.has_video and not body:
            return None
        if not item.has_video and not files:
            log.warning("Skipping news — image download failed: %s", item.title)
            return None

        return await self._send_news_post(
            channel,
            title=item.title,
            body=body,
            files=files,
            track_uri=item.uri,
            auto_close=True,
        )

    async def publish_custom(
        self,
        channel: discord.ForumChannel | discord.TextChannel,
        *,
        title: str,
        body: str,
        media_url: str | None = None,
        auto_close: bool = True,
    ) -> discord.Thread | discord.Message | None:
        """Admin custom news post — Bluesky/video links are shared, not downloaded."""
        from uuid import uuid4

        from media_attach import (
            extract_direct_video_urls,
            is_youtube_url,
        )

        title = forum_thread_name((title or "").strip() or "News")
        body = (body or "").strip()
        media_url = (media_url or "").strip() or None

        session = await self._get_session()
        image_urls: list[str] = []
        has_video = False
        bsky_links = extract_bsky_post_urls(
            "\n".join(x for x in (media_url, body) if x)
        )

        for link in bsky_links:
            resolved = await resolve_bsky_post_url(session, link)
            if resolved is None:
                # Still keep the public post URL even if resolve fails
                body = _append_link(body, link)
                has_video = True
                continue
            if not body and resolved.text:
                body = clean_news_text(resolved.text)
            if resolved.has_video:
                has_video = True
                body = _append_link(body, link)
            else:
                for u in resolved.image_urls:
                    if u not in image_urls:
                        image_urls.append(u)

        if media_url and media_url not in bsky_links:
            if is_youtube_url(media_url) or extract_direct_video_urls(media_url):
                has_video = True
                body = _append_link(body, media_url)
            elif media_url.startswith("http"):
                if media_url not in image_urls:
                    image_urls.append(media_url)

        files: list[discord.File] = []
        if has_video:
            log.info("Custom news video → link only (no download)")
        elif image_urls:
            files = await download_images(session, image_urls)

        if not files and not body:
            return None

        return await self._send_news_post(
            channel,
            title=title,
            body=body,
            files=files,
            track_uri=f"custom:{uuid4()}",
            auto_close=auto_close,
        )

    async def _send_news_post(
        self,
        channel: discord.ForumChannel | discord.TextChannel,
        *,
        title: str,
        body: str,
        files: list[discord.File],
        track_uri: str,
        auto_close: bool,
    ) -> discord.Thread | discord.Message | None:
        if isinstance(channel, discord.ForumChannel):
            tags = await resolve_forum_tags(channel, OW_NEWS_TAG_NAMES)
            kwargs: dict = {
                "name": title,
                "content": body or "\u200b",
            }
            if files:
                kwargs["files"] = files
            if tags:
                kwargs["applied_tags"] = tags
            created = await channel.create_thread(**kwargs)
            thread = created.thread
            await lock_thread_for_reactions_only(thread)
            self.bot.db.mark_ow_news_posted(
                channel.guild.id, track_uri, thread.id, auto_close=auto_close
            )
            return thread

        send_kwargs: dict = {"content": body or "\u200b"}
        if files:
            send_kwargs["files"] = files
        msg = await channel.send(**send_kwargs)
        self.bot.db.mark_ow_news_posted(
            channel.guild.id, track_uri, msg.id, auto_close=False
        )
        return msg

    async def seed_day_old(self, guild: discord.Guild) -> tuple[int, str]:
        """
        First-run: post filtered items about a day old (~24h, with a wide window
        so slightly aged '1d' posts still land).
        """
        channel_id = self._destination_channel_id(guild.id)
        if not channel_id:
            return 0, "No OW news/forum channel set."
        channel = guild.get_channel(channel_id)
        if not is_ow_destination(channel):
            return 0, "News channel missing."

        try:
            items = self.filter_items(await self.fetch_items())
        except Exception as exc:
            return 0, f"Fetch failed: {exc}"

        lo = timedelta(hours=config.OW_NEWS_SEED_MIN_HOURS)
        hi = timedelta(hours=config.OW_NEWS_SEED_MAX_HOURS)
        candidates = [
            i
            for i in items
            if lo <= i.age <= hi and not self.bot.db.was_ow_news_posted(guild.id, i.uri)
        ]
        # Oldest-first so the forum reads chronologically
        candidates.sort(key=lambda i: i.created_at)

        posted = 0
        for item in candidates[: config.OW_NEWS_SEED_MAX_POSTS]:
            try:
                await self.publish_item(channel, item)
                posted += 1
                log.info("OW news seeded: %s", item.title)
            except Exception as exc:
                log.warning("OW news seed failed for %s: %s", item.uri, exc)

        self.bot.db.set_ow_news_seeded(guild.id, True)
        return posted, f"Seeded {posted} day-old news post(s)."

    async def post_map_catchup(
        self, guild: discord.Guild, *, max_age_hours: int = 72
    ) -> tuple[int, str]:
        """One-shot: post recent filtered map / rework stories not yet in the forum."""
        channel_id = self._destination_channel_id(guild.id)
        if not channel_id:
            return 0, "No OW news/forum channel set."
        channel = guild.get_channel(channel_id)
        if not is_ow_destination(channel):
            return 0, "News channel missing."

        try:
            items = self.filter_items(await self.fetch_items())
        except Exception as exc:
            return 0, f"Fetch failed: {exc}"

        max_age = timedelta(hours=max_age_hours)
        candidates = [
            i
            for i in items
            if i.age <= max_age
            and _MAP_CATCHUP.search(i.text)
            and not self.bot.db.was_ow_news_posted(guild.id, i.uri)
        ]
        candidates.sort(key=lambda i: i.created_at)

        posted = 0
        for item in candidates:
            try:
                result = await self.publish_item(channel, item)
                if result is not None:
                    posted += 1
                    log.info("OW map news posted: %s", item.title)
            except Exception as exc:
                log.warning("OW map news failed for %s: %s", item.uri, exc)

        return posted, f"Posted {posted} map news post(s)."

    async def announce_new(self, guild: discord.Guild) -> tuple[int, str]:
        """Hourly: post brand-new filtered items shortly after they appear."""
        channel_id = self._destination_channel_id(guild.id)
        if not channel_id:
            return 0, "No OW news/forum channel set."
        channel = guild.get_channel(channel_id)
        if not is_ow_destination(channel):
            return 0, "News channel missing."

        if not self.bot.db.is_ow_news_seeded(guild.id):
            return await self.seed_day_old(guild)

        try:
            items = self.filter_items(await self.fetch_items())
        except Exception as exc:
            return 0, f"Fetch failed: {exc}"

        max_age = timedelta(hours=config.OW_NEWS_FRESH_MAX_HOURS)
        fresh = [
            i
            for i in items
            if i.age <= max_age
            and not self.bot.db.was_ow_news_posted(guild.id, i.uri)
        ]
        fresh.sort(key=lambda i: i.created_at)

        posted = 0
        for item in fresh:
            try:
                await self.publish_item(channel, item)
                posted += 1
                log.info("OW news posted: %s", item.title)
            except Exception as exc:
                log.warning("OW news post failed for %s: %s", item.uri, exc)

        return posted, f"Posted {posted} new item(s)."

    @tasks.loop(hours=config.OW_NEWS_CHECK_HOURS)
    async def check_news(self) -> None:
        for guild in self.bot.guilds:
            try:
                n, detail = await self.announce_new(guild)
                if n:
                    log.info("OW news in %s: %s", guild.name, detail)
            except Exception as exc:
                log.warning("OW news check failed for %s: %s", guild.name, exc)

    @check_news.before_loop
    async def before_check(self) -> None:
        await self.bot.wait_until_ready()
        # Small delay so forum channel IDs from other cogs are ready
        await asyncio.sleep(8)

    @tasks.loop(minutes=config.OW_NEWS_CLOSE_CHECK_MINUTES)
    async def close_due_news(self) -> None:
        """Archive (Close Post) news threads after OW_NEWS_CLOSE_HOURS — new posts only."""
        rows = self.bot.db.list_ow_news_due_to_close(
            older_than_hours=config.OW_NEWS_CLOSE_HOURS
        )
        for row in rows:
            thread_id = row["thread_id"]
            guild_id = int(row["guild_id"])
            uri = row["bsky_uri"]
            try:
                thread = self.bot.get_channel(int(thread_id))
                if thread is None:
                    thread = await self.bot.fetch_channel(int(thread_id))
                if not isinstance(thread, discord.Thread):
                    self.bot.db.mark_ow_news_closed(guild_id, uri)
                    continue
                if thread.archived:
                    self.bot.db.mark_ow_news_closed(guild_id, uri)
                    continue
                ok = await close_forum_post(
                    thread,
                    reason=f"OW news — auto-close after {config.OW_NEWS_CLOSE_HOURS}h",
                )
                if ok:
                    self.bot.db.mark_ow_news_closed(guild_id, uri)
                    log.info("Closed OW news thread %s (%s)", thread.name, thread_id)
            except discord.NotFound:
                self.bot.db.mark_ow_news_closed(guild_id, uri)
            except Exception as exc:
                log.warning("OW news auto-close failed for %s: %s", thread_id, exc)

    @close_due_news.before_loop
    async def before_close_due(self) -> None:
        await self.bot.wait_until_ready()
        await asyncio.sleep(12)
