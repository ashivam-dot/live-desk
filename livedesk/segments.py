"""Programme segments, each encoded ahead of air as an MPEG-TS chunk that continues the stream's clock.

The air process copies the chunks one after another into a single RTMP connection, so every chunk must start on a
keyframe and carry timestamps that continue where the last one ended (-output_ts_offset). A chunk's length is a
whole number of QUANTUM seconds, which holds exactly 16 video frames at 30 fps and 25 AAC frames at 48 kHz, so
picture and sound end together and the next chunk's timestamps never step back.
"""
from __future__ import annotations

import json
import math
import re
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

from . import look, music, voice
from .channels import Channel

FPS = 30
QUANTUM = 8 / 15
VIDEO = ["-c:v", "libx264", "-preset", "veryfast", "-b:v", "2800k", "-maxrate", "2800k", "-bufsize", "5600k",
         "-g", "60", "-keyint_min", "60", "-sc_threshold", "0", "-bf", "0", "-pix_fmt", "yuv420p", "-r", str(FPS)]
AUDIO = ["-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2"]
TICKER_SPEED = 110  # pixels a second
PAGE_S = 10
UPDATE_PAGE_S = 6
TARGET_LUFS = -16  # every segment airs at one loudness, so nothing jumps between a Short and an update
TAIL_S = 0.35  # the pause a Short keeps after its last sound; its own outro silence would be dead air
EDGE_FADE_S = 0.02  # chunk edges start and end at zero, so a junction never clicks
BED_UNDER_VOICE_DB = -12

_measured: dict[tuple[str, float], tuple[float, float | None]] = {}


def whole(seconds: float) -> float:
    return max(1, math.floor(seconds / QUANTUM)) * QUANTUM


def duration(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
                         capture_output=True, text=True, check=True).stdout
    return float(json.loads(out)["format"]["duration"])


def measure(path: Path) -> tuple[float, float | None]:
    """(gain in dB that brings the file to TARGET_LUFS, when its closing silence starts or None)."""
    key = (str(path), path.stat().st_mtime)
    if key not in _measured:
        log = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-v", "info", "-i", str(path), "-vn",
                              "-af", "silencedetect=n=-50dB:d=0.3,ebur128", "-f", "null", "-"],
                             capture_output=True, text=True, timeout=120).stderr
        loud = re.findall(r"\bI:\s+(-?[\d.]+) LUFS", log)
        gain = min(15.0, max(-20.0, TARGET_LUFS - float(loud[-1]))) if loud and float(loud[-1]) > -70 else 0.0
        starts = [float(x) for x in re.findall(r"silence_start: (-?[\d.]+)", log)]
        ends = [float(x) for x in re.findall(r"silence_end: (-?[\d.]+)", log)]
        tail = starts[-1] if starts and ends and len(starts) == len(ends) and ends[-1] >= duration(path) - 0.25 \
            else None
        _measured[key] = (gain, tail)
    return _measured[key]


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
    gain, tail = measure(src)
    full = duration(src)
    length = whole(min(full, tail + TAIL_S) if tail and tail > full / 2 else full)
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
             f"[0:a]aresample=48000,volume={gain:.2f}dB,alimiter=limit=0.9:level=false,apad,"
             f"afade=t=in:d={EDGE_FADE_S},afade=t=out:st={length - 0.25:.3f}:d=0.25[aud];")
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


def footage_for(desk, clips: list[dict], turn: int) -> dict | None:
    """The channel's own Short to play behind an update: the one telling the lead story if there is one, else the
    clips in turn, so every update shows different original footage."""
    from .trending import same_story, tokens

    clips = [c for c in clips if Path(c.get("path", "")).exists()]
    if not clips:
        return None
    for row in desk.headlines[:3]:
        lead = tokens(row["title"])
        match = next((c for c in clips if same_story(lead, tokens(c.get("title", "")))), None)
        if match:
            return match
    return clips[turn % len(clips)]


def update_segment(ch: Channel, desk, work: Path, offset: float, air_at: datetime,
                   coming: dict | None, library: list[dict] | None = None, turn: int = 0) -> tuple[Path, float]:
    """A full-frame live update between Shorts: the newsreader reads each page while it is up, over the channel's bed
    (ducked under the voice), with the channel's own footage moving behind the pages. Without the voice, a new page
    every UPDATE_PAGE_S seconds over the bed alone."""
    work.mkdir(parents=True, exist_ok=True)
    pages = update_pages(ch, desk)
    footage = footage_for(desk, library or [], turn)
    spoken: list[tuple[Path, float] | None] = [None] * len(pages)
    try:
        for i, text in enumerate(voice.script(ch, pages, desk, air_at, coming)):
            if text:
                clip = work / f"v{i}.mp3"
                spoken[i] = (clip, voice.speak(ch, text, clip))
    except Exception as exc:  # the update still airs, with music only
        print(f"update voice skipped: {exc}"[:200], flush=True)
        spoken = [None] * len(pages)
    holds = [max(UPDATE_PAGE_S, s[1] + 2 * voice.GAP_S) if s else UPDATE_PAGE_S for s in spoken]
    length = whole(sum(holds))
    holds[-1] += length - sum(holds)
    frames, start = [], 0.0
    for i, page in enumerate(pages):
        p = work / f"u{i}.png"
        img = look.update_page(ch, page, desk, air_at + timedelta(seconds=start), coming, footage=bool(footage))
        (img if footage else img.convert("RGB")).save(p)
        frames.append((p, holds[i]))
        start += holds[i]
    bed = music.bed(ch, work.parent.parent / "assets")
    bed_gain, _ = measure(bed)
    clips = [(i, s) for i, s in enumerate(spoken) if s]
    if footage:
        f = 4 + len(clips)
        graph = (f"[{f}:v]scale=640:360:force_original_aspect_ratio=increase,crop=640:360,boxblur=4:1,"
                 f"scale={look.W}:{look.H},eq=brightness=-0.12:saturation=0.9,fps={FPS},format=rgba[foot];"
                 f"[0:v]fps={FPS},format=rgba[pg];[foot][pg]overlay=0:0:eof_action=repeat[paged];")
    else:
        graph = f"[0:v]fps={FPS},format=rgba[paged];"
    fade = f"afade=t=in:d={EDGE_FADE_S},afade=t=out:st={length - 0.6:.3f}:d=0.6"
    if clips:
        graph += (f"[1:a]aresample=48000,volume={bed_gain + BED_UNDER_VOICE_DB:.2f}dB,"
                  f"afade=t=in:d=0.8[bed];")
        for n, (i, (clip, _)) in enumerate(clips):
            at_ms = round((sum(holds[:i]) + voice.GAP_S) * 1000)
            graph += (f"[{4 + n}:a]aresample=48000,aformat=channel_layouts=stereo,"
                      f"volume={measure(clip)[0]:.2f}dB,adelay=delays={at_ms}:all=1[c{n}];")
        graph += ("".join(f"[c{n}]" for n in range(len(clips))) +
                  f"amix=inputs={len(clips)}:normalize=0:dropout_transition=0,apad,asplit[vk][vm];"
                  f"[bed][vk]sidechaincompress=threshold=0.02:ratio=8:attack=20:release=450[duck];"
                  f"[duck][vm]amix=inputs=2:normalize=0:duration=first,alimiter=limit=0.9:level=false,{fade}[aud];")
    else:
        graph += f"[1:a]aresample=48000,volume={bed_gain:.2f}dB,{fade}[aud];"
    ticker_in, ticker_graph = _ticker(ch, desk, work, offset, "paged", 2)
    out = work.with_suffix(".ts")
    voice_in = [a for _, (clip, _) in clips for a in ("-i", str(clip))]
    footage_in = ["-stream_loop", "-1", "-i", footage["path"]] if footage else []
    _encode(["-f", "concat", "-safe", "0", "-i", str(_pages_list(work, frames)), "-stream_loop", "-1",
             "-i", str(bed), *ticker_in, *voice_in, *footage_in, "-filter_complex", graph + ticker_graph,
             "-map", "[v]", "-map", "[aud]"], out, length, offset)
    return out, length
