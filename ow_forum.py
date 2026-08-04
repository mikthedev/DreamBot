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

OwDestination = discord.TextChannel | discord.ForumChannel


def is_ow_destination(channel: object) -> bool:
    return isinstance(channel, (discord.TextChannel, discord.ForumChannel))


def forum_thread_name(title: str, *, prefix: str = "") -> str:
    """Discord forum post titles max out at 100 characters."""
    raw = f"{prefix}{title}".strip() if prefix else title.strip()
    raw = " ".join(raw.split())
    if not raw:
        raw = "Overwatch"
    return raw[:100]


async def resolve_forum_tags(
    forum: discord.ForumChannel, names: Sequence[str]
) -> list[discord.ForumTag]:
    """Match existing tags by name; create missing ones when the bot can."""
    if not names:
        return []
    available = {t.name.casefold(): t for t in forum.available_tags}
    resolved: list[discord.ForumTag] = []
    for name in names:
        key = name.casefold()
        tag = available.get(key)
        if tag is None:
            try:
                tag = await forum.create_tag(name=name[:20])
                available[key] = tag
                log.info("Created forum tag %r in #%s", name, forum.name)
            except discord.HTTPException as exc:
                log.warning(
                    "Could not create forum tag %r in #%s: %s",
                    name,
                    forum.name,
                    exc,
                )
                continue
        if tag not in resolved:
            resolved.append(tag)
    return resolved


async def lock_thread_for_reactions_only(thread: discord.Thread) -> None:
    """Block further messages; reactions / stickers on existing messages still work."""
    try:
        await thread.edit(locked=True, reason="OW announcement — reactions only")
    except discord.HTTPException as exc:
        log.warning("Could not lock forum thread %s: %s", thread.id, exc)


async def post_ow_announcement(
    channel: OwDestination,
    *,
    thread_name: str,
    layouts: list[discord.ui.LayoutView],
    embeds_fallback: Callable[[], list[discord.Embed]] | list[discord.Embed],
    tag_names: Sequence[str] = (),
    trailing_content: str | None = None,
    trailing_view: discord.ui.View | None = None,
) -> list[discord.Message]:
    """
    Publish Components V2 layouts (embed fallback) to a text channel or forum post.

    Forum posts are locked afterward so members can react but not comment.
    Extra layout pages and the trailing history row are sent inside the thread.
    """

    def _embeds() -> list[discord.Embed]:
        if callable(embeds_fallback):
            return embeds_fallback()
        return embeds_fallback

    messages: list[discord.Message] = []

    if isinstance(channel, discord.ForumChannel):
        tags = await resolve_forum_tags(channel, tag_names)
        tag_kwargs = {"applied_tags": tags} if tags else {}
        name = forum_thread_name(thread_name)

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

        # Remaining layout pages (large patches) go inside the thread
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
        return messages

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
    return messages
