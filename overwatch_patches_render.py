"""Render compact Overwatch patch cards (small hero + ability icons)."""

from __future__ import annotations

import hashlib
import io
import logging
from pathlib import Path

import aiohttp
from PIL import Image, ImageDraw, ImageFont

import config

log = logging.getLogger("dream_team.ow_render")

CACHE_DIR = config.DATA_DIR / "ow_icon_cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

CARD_W = 580
PAD = 14
HERO_PX = 40
ABIL_PX = 22
GAP = 8
BG = (18, 22, 30, 255)
FG = (235, 238, 245, 255)
MUTED = (150, 160, 175, 255)
LINE = (40, 48, 60, 255)

_FONT_PATHS = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/Library/Fonts/Arial.ttf",
)


def _font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    candidates = [p for p in _FONT_PATHS if ("Bold" in p) == bold or not bold]
    # Prefer bold files when bold=True, else any
    ordered = (
        [p for p in _FONT_PATHS if "Bold" in p] + list(_FONT_PATHS)
        if bold
        else list(_FONT_PATHS)
    )
    for path in ordered:
        try:
            return ImageFont.truetype(path, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _cache_path(url: str) -> Path:
    digest = hashlib.sha1(url.encode()).hexdigest()
    return CACHE_DIR / f"{digest}.png"


async def load_icon(
    session: aiohttp.ClientSession,
    url: str | None,
    size: int,
    *,
    ssl_ctx,
) -> Image.Image:
    """Download (or cache) and return a square RGBA icon."""
    placeholder = Image.new("RGBA", (size, size), (55, 62, 75, 255))
    if not url:
        return placeholder

    path = _cache_path(url)
    raw: bytes | None = None
    if path.exists() and path.stat().st_size > 0:
        raw = path.read_bytes()
    else:
        try:
            async with session.get(
                url, timeout=aiohttp.ClientTimeout(total=20), ssl=ssl_ctx
            ) as resp:
                if resp.status == 200:
                    raw = await resp.read()
                    path.write_bytes(raw)
        except Exception as exc:
            log.debug("Icon fetch failed %s: %s", url[-40:], exc)

    if not raw:
        return placeholder

    try:
        img = Image.open(io.BytesIO(raw)).convert("RGBA")
    except Exception:
        return placeholder

    # Cover-fit into square
    img.thumbnail((size, size), Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ox = (size - img.width) // 2
    oy = (size - img.height) // 2
    canvas.paste(img, (ox, oy), img)
    return canvas


def _plain_change_lines(lines) -> list[str]:
    out: list[str] = []
    shared = [c for c in lines if not c.mode]
    v5 = [c for c in lines if c.mode == "5v5"]
    v6 = [c for c in lines if c.mode == "6v6"]
    for c in shared:
        # Strip markdown for the bitmap
        text = c.text.replace("**", "").replace("`", "")
        out.append(f"{c.tone} {text}")
    if v5 or v6:
        if v5 and v6 and len(v5) == len(v6):
            for a, b in zip(v5, v6):
                ta = a.text.replace("**", "").replace("`", "")
                tb = b.text.replace("**", "").replace("`", "")
                out.append(f"5v5  {a.tone} {ta}")
                out.append(f"6v6  {b.tone} {tb}")
        else:
            for c in v5:
                out.append(f"5v5  {c.tone} {c.text.replace('**','').replace('`','')}")
            for c in v6:
                out.append(f"6v6  {c.tone} {c.text.replace('**','').replace('`','')}")
    return out


async def render_role_card(
    session: aiohttp.ClientSession,
    heroes: list,
    *,
    ssl_ctx,
) -> bytes:
    """
    Compact sheet: each hero portrait + small ability icons + tweak lines.
    Returns PNG bytes.
    """
    font_title = _font(16, bold=True)
    font_abil = _font(13, bold=True)
    font_body = _font(12, bold=False)

    # Preload icons
    icon_map: dict[str, Image.Image] = {}

    async def get(url: str | None, size: int) -> Image.Image:
        key = f"{url}|{size}"
        if key not in icon_map:
            icon_map[key] = await load_icon(session, url, size, ssl_ctx=ssl_ctx)
        return icon_map[key]

    # Measure
    blocks: list[dict] = []
    total_h = PAD
    for hero in heroes:
        by_ability: dict[str, list] = {}
        for ch in hero.changes:
            by_ability.setdefault(ch.ability, []).append(ch)

        abil_blocks = []
        hero_h = HERO_PX + 4
        for ability, lines in by_ability.items():
            plain = _plain_change_lines(lines)
            text_h = max(ABIL_PX, 4 + len(plain) * 15)
            abil_blocks.append(
                {
                    "name": ability,
                    "lines": plain,
                    "icon": next((c.icon_url for c in lines if c.icon_url), None),
                    "h": text_h + 6,
                }
            )
            hero_h += text_h + 6

        blocks.append(
            {
                "hero": hero,
                "abils": abil_blocks,
                "h": max(hero_h, HERO_PX + 8) + GAP + 6,
            }
        )
        total_h += blocks[-1]["h"]

    total_h += PAD
    img = Image.new("RGBA", (CARD_W, max(total_h, 80)), BG)
    draw = ImageDraw.Draw(img)
    y = PAD

    for block in blocks:
        hero = block["hero"]
        hero_icon = await get(hero.icon_url, HERO_PX)
        img.paste(hero_icon, (PAD, y), hero_icon)
        draw.text((PAD + HERO_PX + GAP, y + 2), hero.name, font=font_title, fill=FG)

        ay = y + 22
        for ab in block["abils"]:
            ab_icon = await get(ab["icon"], ABIL_PX)
            ax = PAD + HERO_PX + GAP
            img.paste(ab_icon, (ax, ay), ab_icon)
            draw.text(
                (ax + ABIL_PX + 6, ay),
                ab["name"],
                font=font_abil,
                fill=FG,
            )
            ty = ay + 16
            for line in ab["lines"]:
                draw.text((ax + ABIL_PX + 6, ty), line, font=font_body, fill=MUTED)
                ty += 15
            ay += ab["h"]

        y += block["h"]
        # divider
        if y < total_h - PAD:
            draw.line((PAD, y - GAP // 2, CARD_W - PAD, y - GAP // 2), fill=LINE, width=1)

    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="PNG", optimize=True)
    return buf.getvalue()
