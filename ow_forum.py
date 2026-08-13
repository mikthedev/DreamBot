"""Shared helpers for Overwatch posts in forum (or text) channels."""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Sequence

import discord

log = logging.getLogger("dream_team.ow_forum")

# Preferred destinations for OW announcements
OW_CHANNEL_TYPES = (
    discord.ChannelType.forum,
    discord.ChannelType.media,
    discord.ChannelType.text,
)

# Prefer the server's emoji tags; never invent plain "Patch" / "Tier"
OW_PATCH_TAG_NAMES = ("Patch Notes",)
# Tier list + best-to-main both sit under META
OW_TIER_TAG_NAMES = ("META",)
OW_META_TAG_NAMES = ("META",)
OW_NEWS_TAG_NAMES = ("News",)
# Bot-created leftovers / retired tags to strip when cleaning a forum
OW_PLAIN_TAGS_TO_REMOVE = frozenset({"patch", "tier"})
OW_RETIRED_TAG_NAMES = frozenset({"tier list"})

OwDestination = discord.TextChannel | discord.ForumChannel


def is_ow_destination(channel: object) -> bool:
    return isinstance(channel, (discord.TextChannel, discord.ForumChannel))


def forum_thread_name(title: str) -> str:
    """Discord forum post titles max out at 100 characters."""
    raw = " ".join(title.split()).strip()
    if not raw:
        raw = "Overwatch"
    return raw[:100]


def patch_thread_title(*, date: str | None = None, title: str | None = None) -> str:
    """Tag already says Patch Notes — keep a short title with the date for freshness."""
    label = (date or "").strip()
    if not label and title:
        m = re.search(
            r"([A-Za-z]+\s+\d{1,2},\s+\d{4})",
            title,
        )
        if m:
            label = m.group(1)
    if label:
        return forum_thread_name(f"Hero Balance ({label})")
    return forum_thread_name("Hero Balance")


def tier_thread_title(*, season: str | None, updated: str | None = None) -> str:
    """Forum list already shows the date + META tag."""
    if season:
        return forum_thread_name(f"Tierlist for Season {season}")
    return forum_thread_name("Tierlist")


def meta_thread_title(*, season: str | None) -> str:
    if season:
        return forum_thread_name(f"Best to main · Season {season}")
    return forum_thread_name("Best to main")


def hero_history_thread_title() -> str:
    return forum_thread_name("Search Hero Changes")


async def resolve_forum_tags(
    forum: discord.ForumChannel, names: Sequence[str]
) -> list[discord.ForumTag]:
    """Match existing tags by name; create META (🎮) if that tag is requested and missing."""
    if not names:
        return []
    available = {t.name.casefold(): t for t in forum.available_tags}
    # Also index without leading emoji in the name field
    resolved: list[discord.ForumTag] = []
    for want in names:
        want_cf = want.casefold()
        tag = available.get(want_cf)
        if tag is None:
            # strip game-controller emoji prefix variants: "🎮META" / "🎮 META"
            for t in forum.available_tags:
                bare = re.sub(r"^[\W_]+", "", t.name).casefold()
                if bare == want_cf or want_cf in t.name.casefold():
                    tag = t
                    break
        if tag is None and want_cf == "meta":
            try:
                tag = await forum.create_tag(
                    name="META",
                    emoji="🎮",
                    reason="OW META / best one-tricks posts",
                )
                available[tag.name.casefold()] = tag
                log.info("Created forum tag META in #%s", forum.name)
            except discord.HTTPException as exc:
                log.warning("Could not create META tag in #%s: %s", forum.name, exc)
        if tag is None:
            log.warning(
                "Forum #%s has no tag matching %r (available: %s)",
                forum.name,
                want,
                [t.name for t in forum.available_tags],
            )
            continue
        if tag not in resolved:
            resolved.append(tag)
    return resolved


async def remove_plain_ow_tags(forum: discord.ForumChannel) -> list[str]:
    """Drop retired / plain bot tags (e.g. Tier List); keep Patch Notes + META."""
    keep: list[discord.ForumTag] = []
    removed: list[str] = []
    for tag in forum.available_tags:
        name_cf = tag.name.casefold()
        if name_cf in OW_RETIRED_TAG_NAMES:
            removed.append(tag.name)
            continue
        if name_cf in OW_PLAIN_TAGS_TO_REMOVE and tag.emoji is None:
            removed.append(tag.name)
            continue
        keep.append(tag)
    if not removed:
        return []
    try:
        await forum.edit(
            available_tags=keep,
            reason="Remove retired OW tags (use Patch Notes / META)",
        )
        log.info("Removed forum tags %s from #%s", removed, forum.name)
    except discord.HTTPException as exc:
        log.warning("Could not remove OW tags from #%s: %s", forum.name, exc)
        return []
    return removed


async def lock_thread_for_reactions_only(thread: discord.Thread) -> None:
    """Block further messages; reactions / stickers on existing messages still work."""
    try:
        await thread.edit(locked=True, reason="OW announcement — reactions only")
    except discord.HTTPException as exc:
        log.warning("Could not lock forum thread %s: %s", thread.id, exc)


async def unlock_thread_for_buttons(thread: discord.Thread) -> None:
    """Keep Patch Notes posts unlocked so persistent buttons/menus stay clickable."""
    try:
        if thread.locked or thread.archived:
            await thread.edit(
                locked=False,
                archived=False,
                reason="OW Patch Notes — buttons need an unlocked post",
            )
    except discord.HTTPException as exc:
        log.warning("Could not unlock forum thread %s: %s", thread.id, exc)


def _is_patch_notes_post(tag_names: Sequence[str]) -> bool:
    return any(name.casefold() == "patch notes" for name in tag_names)


async def close_forum_post(thread: discord.Thread, *, reason: str | None = None) -> bool:
    """Discord 'Close Post' — archives the forum thread so it leaves the active feed."""
    try:
        kwargs: dict = {"archived": True}
        # Keep locked (reactions-only) if we already set that
        if not thread.locked:
            kwargs["locked"] = True
        await thread.edit(
            **kwargs,
            reason=reason or "OW news — close after delay",
        )
        return True
    except discord.HTTPException as exc:
        log.warning("Could not close forum thread %s: %s", thread.id, exc)
        return False


async def _fetch_forum_thread(
    forum: discord.ForumChannel, thread_id: int | None
) -> discord.Thread | None:
    if not thread_id:
        return None
    thread = forum.guild.get_thread(thread_id)
    if thread is None:
        try:
            fetched = await forum.guild.fetch_channel(thread_id)
        except (discord.NotFound, discord.HTTPException):
            return None
        if not isinstance(fetched, discord.Thread):
            return None
        thread = fetched
    if thread.parent_id != forum.id:
        return None
    return thread


async def _clear_bot_followups(
    thread: discord.Thread, *, starter_id: int, bot_id: int
) -> None:
    """Delete older bot messages inside the post (keep the starter)."""
    try:
        async for msg in thread.history(limit=40, oldest_first=True):
            if msg.id == starter_id:
                continue
            if msg.author.id != bot_id:
                continue
            try:
                await msg.delete()
            except discord.HTTPException:
                pass
    except discord.HTTPException as exc:
        log.warning("Could not clear follow-ups in thread %s: %s", thread.id, exc)


async def post_ow_announcement(
    channel: OwDestination,
    *,
    thread_name: str,
    layouts: list[discord.ui.LayoutView],
    embeds_fallback: Callable[[], list[discord.Embed]] | list[discord.Embed],
    tag_names: Sequence[str] = (),
    trailing_content: str | None = None,
    trailing_view: discord.ui.View | None = None,
    existing_thread_id: int | None = None,
) -> tuple[list[discord.Message], int | None]:
    """
    Publish Components V2 layouts (embed fallback) to a text channel or forum post.

    Forum: edit the existing live post in place when possible (title + body + tags),
    otherwise create one locked post. Returns (messages, forum_thread_id|None).
    """

    def _embeds() -> list[discord.Embed]:
        if callable(embeds_fallback):
            return embeds_fallback()
        return embeds_fallback

    messages: list[discord.Message] = []

    if isinstance(channel, discord.ForumChannel):
        tags = await resolve_forum_tags(channel, tag_names)
        name = forum_thread_name(thread_name)
        me = channel.guild.me
        bot_id = me.id if me else 0

        thread = await _fetch_forum_thread(channel, existing_thread_id)
        if thread is not None:
            try:
                if thread.archived or thread.locked:
                    await thread.edit(
                        archived=False,
                        locked=False,
                        reason="Updating OW announcement",
                    )
                edit_kwargs: dict = {"name": name, "archived": False}
                if tags:
                    edit_kwargs["applied_tags"] = tags
                await thread.edit(**edit_kwargs, reason="Updating OW announcement")

                starter = thread.starter_message
                if starter is None:
                    starter = await thread.fetch_message(thread.id)

                try:
                    if layouts:
                        await starter.edit(
                            content=None,
                            embeds=[],
                            view=layouts[0],
                            attachments=[],
                        )
                    else:
                        await starter.edit(
                            content=None,
                            embeds=_embeds(),
                            view=None,
                            attachments=[],
                        )
                except Exception as exc:
                    log.warning("OW forum starter LayoutView edit failed: %s", exc)
                    await starter.edit(
                        content=None,
                        embeds=_embeds(),
                        view=None,
                        attachments=[],
                    )
                    layouts = []

                await _clear_bot_followups(
                    thread, starter_id=starter.id, bot_id=bot_id
                )
                messages.append(starter)

                for layout in layouts[1:]:
                    try:
                        messages.append(await thread.send(view=layout))
                    except Exception as exc:
                        log.warning("OW forum follow-up layout failed: %s", exc)

                if trailing_content or trailing_view:
                    messages.append(
                        await thread.send(
                            content=trailing_content or None,
                            view=trailing_view,
                        )
                    )

                if _is_patch_notes_post(tag_names):
                    await unlock_thread_for_buttons(thread)
                else:
                    await lock_thread_for_reactions_only(thread)
                return messages, thread.id
            except Exception as exc:
                log.warning(
                    "OW forum in-place update failed (will create new post): %s",
                    exc,
                )
                thread = None

        # First publish (or previous thread gone)
        tag_kwargs = {"applied_tags": tags} if tags else {}
        try:
            first = layouts[0] if layouts else None
            if first is not None:
                created = await channel.create_thread(
                    name=name, view=first, **tag_kwargs
                )
            else:
                created = await channel.create_thread(
                    name=name, embeds=_embeds(), **tag_kwargs
                )
        except Exception as exc:
            log.warning("OW forum LayoutView failed, using embeds: %s", exc)
            created = await channel.create_thread(
                name=name, embeds=_embeds(), **tag_kwargs
            )
            layouts = []

        thread, starter = created.thread, created.message
        messages.append(starter)

        for layout in layouts[1:]:
            try:
                messages.append(await thread.send(view=layout))
            except Exception as exc:
                log.warning("OW forum follow-up layout failed: %s", exc)

        if trailing_content or trailing_view:
            messages.append(
                await thread.send(
                    content=trailing_content or None,
                    view=trailing_view,
                )
            )

        if _is_patch_notes_post(tag_names):
            await unlock_thread_for_buttons(thread)
        else:
            await lock_thread_for_reactions_only(thread)
        return messages, thread.id

    # Classic text channel (kept for transition / fallback)
    try:
        for layout in layouts:
            messages.append(await channel.send(view=layout))
    except Exception as exc:
        log.warning("OW text LayoutView failed, using embeds: %s", exc)
        messages.append(await channel.send(embeds=_embeds()))

    if trailing_content or trailing_view:
        messages.append(
            await channel.send(
                content=trailing_content or None,
                view=trailing_view,
            )
        )
    return messages, None
