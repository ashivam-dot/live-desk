"""Programme segments, each encoded ahead of air as an MPEG-TS chunk that continues the stream's clock.

The air process copies the chunks one after another into a single RTMP connection, so every chunk must start on a
keyframe and carry timestamps that continue where the last one ended (-output_ts_offset). A chunk's length is a
whole number of QUANTUM seconds, which holds exactly 16 video frames at 30 fps and 25 AAC frames at 48 kHz, so
picture and sound end together and the next chunk's timestamps never step back.
"""
from __future__ import annotations

import json
import math
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

from . import look, music
from .channels import Channel

FPS = 30
QUANTUM = 8 / 15
VIDEO = ["-c:v", "libx264", "-preset", "veryfast", "-b:v", "2800k", "-maxrate", "2800k", "-bufsize", "5600k",
         "-g", "60", "-keyint_min", "60", "-sc_threshold", "0", "-bf", "0", "-pix_fmt", "yuv420p", "-r", str(FPS)]
AUDIO = ["-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2"]
TICKER_SPEED = 110  # pixels a second
PAGE_S = 10
UPDATE_PAGE_S = 6


def whole(seconds: float) -> float:
    return max(1, math.floor(seconds / QUANTUM)) * QUANTUM


def duration(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
                         capture_output=True, text=True, check=True).stdout
    return float(json.loads(out)["format"]["duration"])


def _pages_list(work: Path, frames: list[tuple[Path, float]]) -> Path:
    lines = []
    for path, secs in frames:
        lines += [f"file '{path.name}'", f"duration {secs:.3f}"]
    lines.append(f"file '{frames[-1][0].name}'")  # the concat demuxer needs the last file twice to hold it
    listing = work / "pages.txt"
    listing.write_text("\n".join(lines) + "\n")
    return listing


def _ticker(ch: Channel, desk, work: Path, offset: float, base: str, first: int) -> tuple[list[str], str]:
    """Inputs and filters that scroll the headline crawl along the bottom. The scroll position follows the stream's
    clock, so it carries on across segments while the headlines stay the same."""
    strip = look.ticker_strip(ch, desk)
    label, label_w = look.ticker_label(ch)
    strip.save(work / "ticker.png")
    label.save(work / "label.png")
    lane = look.W - label_w
    x = f"{look.W}-mod((t+{offset:.3f})*{TICKER_SPEED}\\,{strip.width + lane})"
    inputs = ["-loop", "1", "-framerate", str(FPS), "-i", str(work / "ticker.png"),
              "-loop", "1", "-framerate", str(FPS), "-i", str(work / "label.png")]
    graph = (f"[{base}][{first}:v]overlay=x='{x}':y={look.BOTTOM}:eval=frame[crawl];"
             f"[crawl][{first + 1}:v]overlay=0:{look.BOTTOM},format=yuv420p[v]")
    return inputs, graph


def _encode(args: list[str], out: Path, length: float, offset: float) -> None:
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args, *VIDEO, *AUDIO, "-t", f"{length:.6f}",
           "-output_ts_offset", f"{offset:.6f}", "-muxdelay", "0", "-muxpreload", "0", "-f", "mpegts", str(out)]
    subprocess.run(cmd, check=True, timeout=max(120, length * 6))


def short_pages(ch: Channel, length: float) -> list[str]:
    pages = ["headlines-1", "headlines-2"] + (["quakes"] if ch.quakes else [])
    return [pages[i % len(pages)] for i in range(math.ceil(length / PAGE_S))]


def short_segment(ch: Channel, short: dict, desk, work: Path, offset: float, air_at: datetime,
                  coming: dict | None = None) -> tuple[Path, float]:
    """A Short in the middle of the frame, its title on the left, live pages rotating on the right."""
    work.mkdir(parents=True, exist_ok=True)
    src = Path(short["path"])
    length = whole(duration(src))
    frames = []
    for i, page in enumerate(short_pages(ch, length)):
        p = work / f"p{i}.png"
        look.short_frame(ch, short, page, desk, air_at + timedelta(seconds=i * PAGE_S), coming).save(p)
        frames.append((p, min(PAGE_S, length - i * PAGE_S)))
    graph = (f"[0:v]split[a][b];"
             f"[a]scale=320:180:force_original_aspect_ratio=increase,crop=320:180,boxblur=8:2,"
             f"scale={look.W}:{look.H},eq=brightness=-0.22:saturation=0.75[bg];"
             f"[b]scale={look.SHORT_W}:{look.SHORT_H}:force_original_aspect_ratio=increase,"
             f"crop={look.SHORT_W}:{look.SHORT_H},setsar=1[fg];"
             f"[bg][fg]overlay={look.SHORT_X}:0[base];"
             f"[1:v]format=rgba,fps={FPS}[ov];"
             f"[base][ov]overlay=0:0:eof_action=repeat,fps={FPS}[paged];"
             f"[0:a]aresample=48000,apad[aud];")
    ticker_in, ticker_graph = _ticker(ch, desk, work, offset, "paged", 2)
    out = work.with_suffix(".ts")
    _encode(["-i", str(src), "-f", "concat", "-safe", "0", "-i", str(_pages_list(work, frames)), *ticker_in,
             "-filter_complex", graph + ticker_graph, "-map", "[v]", "-map", "[aud]"], out, length, offset)
    return out, length


def update_pages(ch: Channel, desk) -> list[str]:
    pages = ["headlines-1", "headlines-2"]
    if ch.quakes and desk.quakes:
        pages.append("quakes")
    return pages + ["follow"]


def update_segment(ch: Channel, desk, work: Path, offset: float, air_at: datetime,
                   coming: dict | None) -> tuple[Path, float]:
    """A full-frame live update between Shorts: a new page every UPDATE_PAGE_S seconds over the channel's bed."""
    work.mkdir(parents=True, exist_ok=True)
    pages = update_pages(ch, desk)
    length = whole(len(pages) * UPDATE_PAGE_S)
    frames = []
    for i, page in enumerate(pages):
        p = work / f"u{i}.png"
        look.update_page(ch, page, desk, air_at + timedelta(seconds=i * UPDATE_PAGE_S), coming).convert("RGB").save(p)
        frames.append((p, UPDATE_PAGE_S))
    bed = music.bed(ch, work.parent.parent / "assets")
    graph = (f"[0:v]fps={FPS},format=rgba[paged];"
             f"[1:a]aresample=48000,afade=t=in:d=1.5,afade=t=out:st={length - 1.5:.3f}:d=1.5[aud];")
    ticker_in, ticker_graph = _ticker(ch, desk, work, offset, "paged", 2)
    out = work.with_suffix(".ts")
    _encode(["-f", "concat", "-safe", "0", "-i", str(_pages_list(work, frames)), "-stream_loop", "-1",
             "-i", str(bed), *ticker_in, "-filter_complex", graph + ticker_graph, "-map", "[v]", "-map", "[aud]"],
            out, length, offset)
    return out, length
