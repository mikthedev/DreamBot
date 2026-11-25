#!/usr/bin/env python3
from pathlib import Path
from PIL import Image, ImageChops

SRC = Path("/Users/user/.cursor/projects/Users-user-Desktop-Development-Dream-Team-Bot/assets")
OUT = Path("/Users/user/Desktop/Development/Dream Team Bot/gates/public/brands")

MAP = {
    "pepsico-dbb29f63-76be-48e9-894b-edd439b9c021.png": "pepsico",
    "kerin-32200687-1e5e-4024-9819-43b526b8eba4.png": "kering",
    "levis-5e63e545-f27c-49f2-b70d-bcc055c3f9e2.png": "levis",
    "gap-535a7d63-413d-493f-bd7b-d6c10178cbed.png": "gap",
    "tkmax-1d0836b1-34d4-4ef9-9ec4-0ce851b3218f.png": "tkmaxx",
    "Richemont-b8e03439-780d-4c4c-b2cb-f37478cc601b.png": "richemont",
    "lids-b7e940ee-74fa-4395-9026-62b9a5d1a146.png": "lids",
    "waymo-5ec2c5bc-9e7b-417a-8c72-09fc68cfa4e8.png": "waymo",
    "tyson-2339827c-232d-4125-aa27-48c553a9bd3d.png": "tyson",
    "sofi-07e2d81d-62d1-4d00-a2df-21a9c2e2f1b5.png": "sofi",
    "LV-57269533-3c9c-4618-8b82-f1a39949ab69.png": "lvmh",
    "wembley-ec0179be-32a6-49a7-96f9-16783a19f459.png": "wembley",
    "MSG-89a99685-390f-4447-9b6a-d5719a358cd6.png": "msg",
    "rawlings-c4d846f2-043c-4126-9604-7e5cd9c8f0c1.png": "rawlings",
    "Wilson-b08e57ac-a64f-406a-9e5c-a842677e6410.png": "wilson",
    "mitchel___ness-9c30acd9-1f7d-483c-84ca-3957f415744b.png": "mitchellness",
    "spalding-ec44d902-c13d-40a5-832a-2427c9ac6024.png": "spalding",
    "gordon-116c3977-8624-4f2c-8ea9-defea4bef681.png": "gordonfood",
    "Sysco-35eab95a-f7f9-4116-80f9-0d3c1825d9c5.png": "sysco",
    "dominos-ef8389f5-458f-4f55-8c0a-662ef6099d6b.png": "dominos",
    "us_foods-519fa897-6976-4b1f-a9ff-5855c808178b.png": "usfoods",
    "nestlr-ecf043a8-f985-4111-9e19-61db81068554.png": "nestle",
    "costco-95c3e7f6-4524-4844-bef6-07ecad0b1174.png": "costco",
    "Fanatics-2ff1625a-0be7-42f2-9765-de58a4ea8d84.png": "fanatics",
    "mac-5d08cf37-b1cd-475d-b5e7-0d2f377daefe.png": "mac",
    "du_pont-a5f21296-bc53-4243-85aa-5e2f788f33be.png": "dupont",
    "pepso-1551b2f7-3efa-4050-a116-c36fceb712a5.png": "pepsi",
    "carhart-fcfc2207-5575-486e-8df1-c4b99dab114a.png": "carhartt",
    "whole_foods-a673774a-41b7-46bb-8a79-61288247ba7c.png": "wholefoods",
    "FL_Logo_Primary_Black_RGB-e14d2730-4d3e-4d52-a8c0-91a88eb7543f.jpg": "footlocker",
    "GTB_black_RGB-1efd9d74-1a20-4359-8fbb-5969f9cb3774.png": "goretex",
    "7-eleven-4-logo-png-transparent-b58e704e-c60e-42a0-985d-6b0043b05b51.png": "seveneleven",
    "papa-johns-pizza-2-logo-black-and-white-629462bf-c667-428d-95fa-7ae3038a4f4f.png": "papajohns",
    "images__1_-f2b3bd3e-d962-41fb-96d6-fa604039999a.png": "cargill",
}

SIZE = 256
PAD = 0.10


def corner_lum(im: Image.Image) -> float:
    w, h = im.size
    pts = [(4, 4), (w - 5, 4), (4, h - 5), (w - 5, h - 5)]
    acc = 0.0
    for x, y in pts:
        r, g, b, a = im.getpixel((x, y))
        acc += (r + g + b) / 3
    return acc / 4


def chroma(r, g, b) -> int:
    return max(r, g, b) - min(r, g, b)


def fit_square(im: Image.Image) -> Image.Image:
    bbox = im.getbbox()
    if not bbox:
        return Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    im = im.crop(bbox)
    w, h = im.size
    side = int(max(w, h) * (1 + PAD * 2))
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    canvas.paste(im, ((side - w) // 2, (side - h) // 2), im)
    return canvas.resize((SIZE, SIZE), Image.Resampling.LANCZOS)


def process(im: Image.Image) -> tuple[Image.Image, str]:
    im = im.convert("RGBA")
    extrema = im.getextrema()
    rmax, gmax, bmax, amax = extrema[0][1], extrema[1][1], extrema[2][1], extrema[3][1]
    rgb_max = max(rmax, gmax, bmax)
    bg = corner_lum(im)
    px = im.load()
    w, h = im.size
    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    op = out.load()
    color_hits = 0
    mark_hits = 0

    # Logo lives only in alpha (black RGB).
    if rgb_max <= 4 and amax > 8:
        for y in range(h):
            for x in range(w):
                r, g, b, a = px[x, y]
                if a > 12:
                    op[x, y] = (255, 255, 255, a)
                    mark_hits += 1
        return fit_square(out), "sil"

    for y in range(h):
        for x in range(w):
            r, g, b, a = px[x, y]
            if a < 8:
                continue
            lum = (r + g + b) / 3
            ch = chroma(r, g, b)
            if bg < 40:
                if lum < 5 and ch < 8:
                    continue
                if ch > 28:
                    op[x, y] = (r, g, b, a)
                    color_hits += 1
                else:
                    # gray/white on black → white silhouette
                    aa = max(a, int(min(255, lum * 1.15)))
                    op[x, y] = (255, 255, 255, aa)
                mark_hits += 1
            else:
                if lum > 242 and ch < 12:
                    continue
                if ch > 28:
                    op[x, y] = (r, g, b, a)
                    color_hits += 1
                else:
                    inv = int(max(0, 255 - lum))
                    op[x, y] = (255, 255, 255, max(a, inv))
                mark_hits += 1

    kind = "color" if color_hits > mark_hits * 0.18 and color_hits > 80 else "sil"
    return fit_square(out), kind


def main():
    kinds = {}
    for name, dest in MAP.items():
        src = SRC / name
        if not src.exists():
            print("MISSING", name)
            continue
        fitted, kind = process(Image.open(src))
        if dest in {"seveneleven", "wholefoods", "dominos"}:
            print("skip broken source", dest)
            continue
        kinds[dest] = kind
        fitted.save(OUT / f"{dest}.png", "PNG")
        print(f"{dest:16} {kind:5} bbox-ok {fitted.getbbox()}")
    print("KINDS", kinds)


if __name__ == "__main__":
    main()
