"""Shared helpers for Overwatch posts in forum (or text) channels."""

from __future__ import annotations

import logging
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
OW_TIER_TAG_NAMES = ("Tier List",)
# Bot-created leftovers to strip when cleaning a forum
OW_PLAIN_TAGS_TO_REMOVE = frozenset({"patch", "tier"})

OwDestination = discord.TextChannel | discord.ForumChannel


def is_ow_destination(channel: object) -> bool:
    return isinstance(channel, (discord.TextChannel, discord.ForumChannel))


def forum_thread_name(title: str) -> str:
    """Discord forum post titles max out at 100 characters."""
    raw = " ".join(title.split()).strip()
    if not raw:
        raw = "Overwatch"
    return raw[:100]


def patch_thread_title(*, date: str | None) -> str:
    label = (date or "Latest").strip() or "Latest"
    return forum_thread_name(f"🛠️  Patch Notes  ·  {label}")


def tier_thread_title(*, season: str | None, updated: str | None) -> str:
    season_bit = f"Season {season}" if season else "Current Season"
    date_bit = (updated or "latest").strip() or "latest"
    return forum_thread_name(f"📈  Tier List  ·  {season_bit}  ·  {date_bit}")


async def resolve_forum_tags(
    forum: discord.ForumChannel, names: Sequence[str]
) -> list[discord.ForumTag]:
    """Match existing tags by name only — never create new ones."""
    if not names:
        return []
    available = list(forum.available_tags)
    resolved: list[discord.ForumTag] = []
    for want in names:
        want_cf = want.casefold()
        tag = next((t for t in available if t.name.casefold() == want_cf), None)
        if tag is None:
            # e.g. want "Patch Notes" → match a tag whose name contains that phrase
            tag = next(
                (t for t in available if want_cf in t.name.casefold()),
                None,
            )
        if tag is None:
            log.warning(
                "Forum #%s has no tag matching %r (available: %s)",
                forum.name,
                want,
                [t.name for t in available],
            )
            continue
        if tag not in resolved:
            resolved.append(tag)
    return resolved


async def remove_plain_ow_tags(forum: discord.ForumChannel) -> list[str]:
    """Drop bot-created plain Patch/Tier tags; keep emoji tags like Patch Notes."""
    keep: list[discord.ForumTag] = []
    removed: list[str] = []
    for tag in forum.available_tags:
        if tag.name.casefold() in OW_PLAIN_TAGS_TO_REMOVE and tag.emoji is None:
            removed.append(tag.name)
            continue
        keep.append(tag)
    if not removed:
        return []
    try:
        await forum.edit(
            available_tags=keep,
            reason="Remove plain bot-created OW tags (use Patch Notes / Tier List)",
        )
        log.info("Removed forum tags %s from #%s", removed, forum.name)
    except discord.HTTPException as exc:
        log.warning("Could not remove plain OW tags from #%s: %s", forum.name, exc)
        return []
    return removed


async def lock_thread_for_reactions_only(thread: discord.Thread) -> None:
    """Block further messages; reactions / stickers on existing messages still work."""
    try:
        await thread.edit(locked=True, reason="OW announcement — reactions only")
    except discord.HTTPException as exc:
        log.warning("Could not lock forum thread %s: %s", thread.id, exc)


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
