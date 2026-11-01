"""Every picture the stream draws: the panels around a Short and the full-frame live-update pages.

A Short (9:16) plays in the middle of the 16:9 frame over a blurred, darkened copy of itself; the left panel says
what is playing, the right panel rotates live pages (clocks, headlines, quakes). Between Shorts a live update fills
the frame, a new page every few seconds. Sizes follow YouTube's 1280x720 at 30 fps ingest.
"""
from __future__ import annotations

from datetime import datetime, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, ImageFont

from .channels import Channel

W, H = 1280, 720
SHORT_W = 404  # a 9:16 Short at the frame's full height, kept even for 4:2:0 video
SHORT_X = (W - SHORT_W) // 2
SIDE_W = SHORT_X - 56
LEFT_X, RIGHT_X = 28, SHORT_X + SHORT_W + 28
TOP = 108
BODY_WEIGHT = 560


@lru_cache(maxsize=64)
def font(path, size: int) -> ImageFont.FreeTypeFont:
    f = ImageFont.truetype(str(path), size, layout_engine=ImageFont.Layout.RAQM
                           if ImageFont.core.HAVE_RAQM else ImageFont.Layout.BASIC)
    try:  # a variable font opens at its thinnest weight, too faint over video
        axes = f.get_variation_axes()
        f.set_variation_by_axes([min(max(BODY_WEIGHT, a["minimum"]), a["maximum"]) for a in axes])
    except OSError:
        pass
    return f


def wrap(text: str, f: ImageFont.FreeTypeFont, width: int, lines: int) -> list[str]:
    words, out, line = text.split(), [], ""
    for w in words:
        trial = f"{line} {w}".strip()
        if f.getlength(trial) <= width:
            line = trial
            continue
        if line:
            out.append(line)
        line = w
        if len(out) == lines:
            break
    if line and len(out) < lines:
        out.append(line)
    if len(out) == lines and " ".join(out) != " ".join(words):
        last = out[-1]
        while last and f.getlength(last + "…") > width:
            last = last.rsplit(" ", 1)[0] if " " in last else last[:-1]
        out[-1] = last + "…"
    return out


def text_block(d: ImageDraw.ImageDraw, xy, text: str, f, fill, width: int, lines: int, gap: float = 1.18) -> int:
    """Draws wrapped text; returns the y below it."""
    x, y = xy
    for line in wrap(text, f, width, lines):
        d.text((x, y), line, font=f, fill=fill)
        y += int(f.size * gap)
    return y


@lru_cache(maxsize=4)
def logo(path, size: int) -> Image.Image:
    img = Image.open(path).convert("RGBA").resize((size, size), Image.LANCZOS)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size - 1, size - 1), fill=255)
    img.putalpha(mask)
    return img


def brand_bar(img: Image.Image, ch: Channel) -> None:
    """Logo, name and the LIVE pill, top left; the same on every frame."""
    d = ImageDraw.Draw(img)
    img.alpha_composite(logo(ch.logo, 64), (LEFT_X, 24))
    name = font(ch.head_font, 34)
    d.text((LEFT_X + 78, 26), ch.name, font=name, fill=ch.text)
    live = font(ch.head_font, 22)
    text = ch.words["live"]
    lw = int(live.getlength(text)) + 44
    x, y = LEFT_X + 78, 26 + int(name.size * 1.25)
    d.rounded_rectangle((x, y, x + lw, y + 30), radius=6, fill=ch.hot)
    d.ellipse((x + 11, y + 10, x + 21, y + 20), fill=(255, 255, 255))
    d.text((x + 30, y + 1), text, font=live, fill=(255, 255, 255))


def subscribe(d: ImageDraw.ImageDraw, ch: Channel, x: int, y: int) -> None:
    """A YouTube-red subscribe button with the channel handle beside it."""
    f = font(ch.head_font, 22)
    text = ch.words["subscribe"]
    w = int(f.getlength(text)) + 50
    d.rounded_rectangle((x, y, x + w, y + 38), radius=8, fill=(255, 0, 0))
    d.polygon(((x + 14, y + 11), (x + 14, y + 27), (x + 28, y + 19)), fill=(255, 255, 255))
    d.text((x + 36, y + 5), text, font=f, fill=(255, 255, 255))
    d.text((x, y + 48), ch.handle, font=font(ch.head_font, 24), fill=ch.text)


def panel(d: ImageDraw.ImageDraw, ch: Channel, box) -> None:
    d.rounded_rectangle(box, radius=18, fill=ch.panel)


def label(d: ImageDraw.ImageDraw, ch: Channel, xy, text: str, size: int = 22) -> int:
    f = font(ch.head_font, size)
    d.text(xy, text, font=f, fill=ch.accent)
    return xy[1] + int(size * 1.45)


def clocks(d: ImageDraw.ImageDraw, ch: Channel, x: int, y: int, width: int, at: datetime) -> int:
    y = label(d, ch, (x, y), ch.words["clocks"])
    many = len(ch.clocks) > 1
    per_row = 2 if many else 1  # four zone names never fit on one row of a side panel
    cell, row_h = width // per_row, 64 if many else 84
    big, small = font(ch.head_font, 30 if many else 46), font(ch.body_font, 15)
    for i, (name, zone) in enumerate(ch.clocks):
        local = at.astimezone(ZoneInfo(zone))
        cx, cy = x + (i % per_row) * cell, y + (i // per_row) * row_h
        d.text((cx, cy), local.strftime("%H:%M"), font=big, fill=ch.text)
        sub = name if many else f"{name} · {local:%d.%m.%Y}"
        d.text((cx + (int(big.getlength("00:00")) + 10 if many else 0), cy + (12 if many else int(big.size * 1.15))),
               sub, font=small, fill=ch.text + (190,))
    rows = -(-len(ch.clocks) // per_row)
    return y + rows * row_h + (4 if many else 0)


def ago(iso: str | None, at: datetime, lang: str) -> str:
    if not iso:
        return ""
    mins = max(0, int((at - datetime.fromisoformat(iso)).total_seconds() // 60))
    if lang == "hi":
        return f"{mins} मिनट पहले" if mins < 60 else f"{mins // 60} घंटे पहले"
    return f"{mins} min ago" if mins < 60 else f"{mins // 60} h ago"


def headline_list(d, ch: Channel, x: int, y: int, width: int, rows: list[dict], at: datetime, bottom: int,
                  size: int = 21, lines: int = 3) -> int:
    y = label(d, ch, (x, y), ch.words["headlines"])
    f, meta = font(ch.body_font, size), font(ch.body_font, 15)
    for r in rows:
        need = int(size * 1.18) * min(lines, len(wrap(r["title"], f, width, lines))) + 34
        if y + need > bottom:
            break
        d.rectangle((x, y + 4, x + 4, y + need - 30), fill=ch.accent)
        end = text_block(d, (x + 16, y), r["title"], f, ch.text, width - 16, lines)
        d.text((x + 16, end + 2), f"{r['outlet']} · {ago(r['at'], at, ch.lang)}".strip(" ·"), font=meta,
               fill=ch.text + (170,))
        y = end + 32
    return y


def quake_list(d, ch: Channel, x: int, y: int, width: int, rows: list[dict], at: datetime, bottom: int) -> int:
    y = label(d, ch, (x, y), ch.words["quakes"])
    mag, place, meta = font(ch.head_font, 34), font(ch.body_font, 18), font(ch.body_font, 15)
    for r in rows:
        if y + 60 > bottom:
            break
        colour = ch.hot if r["mag"] >= 6 else (255, 179, 0) if r["mag"] >= 5 else ch.accent
        d.text((x, y), f"M{r['mag']:.1f}", font=mag, fill=colour)
        text_block(d, (x + 96, y + 2), r["place"], place, ch.text, width - 96, 1)
        d.text((x + 96, y + 28), ago(r["at"], at, "en") + " · USGS", font=meta, fill=ch.text + (170,))
        y += 62
    return y


def short_frame(ch: Channel, short: dict, page: str, desk, at: datetime, coming: dict | None = None) -> Image.Image:
    """The overlay around a playing Short: transparent where the Short and its blurred backdrop show."""
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    brand_bar(img, ch)
    x, width = LEFT_X + 10, SIDE_W - 20
    panel(d, ch, (LEFT_X - 8, TOP + 40, LEFT_X + SIDE_W + 8, H - 40))
    y = label(d, ch, (x, TOP + 58), ch.words["now"])
    y = text_block(d, (x, y + 4), short["title"], font(ch.head_font, 38), ch.text, width, 4, 1.14)
    when = datetime.fromisoformat(short["published_at"]).astimezone(ZoneInfo(ch.clocks[0][1]))
    meta = font(ch.body_font, 18)
    zone = ch.words.get("zone", f"{when:%Z}")
    d.text((x, y + 10), f"{ch.words['published']} {when:%d.%m.%Y %H:%M} {zone}".strip(), font=meta,
           fill=ch.text + (190,))
    y += 54
    if coming and coming.get("video_id") != short.get("video_id"):
        d.line((x, y, x + width, y), fill=ch.text + (60,), width=1)
        y = label(d, ch, (x, y + 16), ch.words["next"].upper() if ch.lang == "en" else ch.words["next"], 20)
        text_block(d, (x, y), coming["title"], font(ch.body_font, 22), ch.text + (230,), width, 3)
    d.text((x, H - 168), ch.words["full"], font=meta, fill=ch.accent)
    subscribe(d, ch, x, H - 138)

    box = (RIGHT_X - 8, 24, RIGHT_X + SIDE_W + 8, H - 40)
    panel(d, ch, box)
    x, y, width, bottom = RIGHT_X + 10, 40, SIDE_W - 20, H - 56
    y = clocks(d, ch, x, y, width, at)
    if page == "quakes" and desk.quakes:
        quake_list(d, ch, x, y, width, desk.quakes, at, bottom)
    else:
        rows = desk.headlines[4:8] if page == "headlines-2" else desk.headlines[:4]
        headline_list(d, ch, x, y, width, rows or desk.headlines[:4], at, bottom)
    return img


def update_page(ch: Channel, page: str, desk, at: datetime, coming: dict | None) -> Image.Image:
    """A full-frame live-update page shown between Shorts."""
    img = Image.new("RGBA", (W, H), ch.bg + (255,))
    d = ImageDraw.Draw(img)
    for i in range(H):  # a soft vertical light, so the page is never a flat slab of colour
        shade = int(28 * (1 - abs(i - H * 0.35) / H))
        d.line((0, i, W, i), fill=tuple(min(255, c + shade) for c in ch.bg))
    brand_bar(img, ch)
    big = font(ch.head_font, 30)
    title = ch.words["update"]
    d.text((W - 48 - big.getlength(title), 34), title, font=big, fill=ch.accent)
    stamp = at.astimezone(ZoneInfo(ch.clocks[0][1]))
    d.text((W - 48 - font(ch.body_font, 18).getlength(f"{stamp:%H:%M} · {stamp:%d.%m.%Y}"), 76),
           f"{stamp:%H:%M} · {stamp:%d.%m.%Y}", font=font(ch.body_font, 18), fill=ch.text + (200,))
    x, y, width, bottom = 64, 150, W - 128, H - 70
    if page.startswith("headlines"):
        n = int(page.rsplit("-", 1)[-1]) if "-" in page else 1
        rows = desk.headlines[(n - 1) * 3:n * 3] or desk.headlines[:3]
        headline_list(d, ch, x, y, width, rows, at, bottom, size=40, lines=2)
    elif page == "quakes":
        quake_list(d, ch, x, y, width, desk.quakes, at, bottom)
    else:  # follow card: the channel, what it is, what plays next
        img.alpha_composite(logo(ch.logo, 220), ((W - 220) // 2, 150))
        f = font(ch.head_font, 40)
        line = ch.words["follow"]
        d.text(((W - f.getlength(line)) / 2, 400), line, font=f, fill=ch.text)
        hf = font(ch.head_font, 30)
        d.text(((W - hf.getlength(ch.handle)) / 2, 462), ch.handle, font=hf, fill=ch.accent)
        if coming:
            nf = font(ch.body_font, 24)
            text = f"{ch.words['next']}: {coming['title']}"
            for i, l in enumerate(wrap(text, nf, W - 240, 2)):
                d.text(((W - nf.getlength(l)) / 2, 540 + i * 32), l, font=nf, fill=ch.text + (220,))
    outlets = ", ".join(dict.fromkeys(r["outlet"] for r in desk.headlines[:9])) or "—"
    d.text((x, H - 44), ch.words["source"].format(outlets=outlets), font=font(ch.body_font, 15),
           fill=ch.text + (150,))
    return img


def now() -> datetime:
    return datetime.now(timezone.utc)
