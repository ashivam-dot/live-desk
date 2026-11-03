"""The newsreader of the live updates: a script that changes every time, read by a neural voice (edge-tts, no key).

Each update page gets its own spoken part, so a page stays up exactly as long as its part is being read. Phrasing
rotates through several openers, attributions and sign-offs, chosen by the hour and the headline, so the desk
never repeats itself word for word. Anything that fails here costs the voice, never the update: the caller falls
back to the music bed.
"""
from __future__ import annotations

import asyncio
import json
import re
import subprocess
import zlib
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .channels import Channel

VOICES = {"orbitwire": ("en-US-AndrewNeural", "+4%"), "palpal": ("hi-IN-SwaraNeural", "+6%")}
TIMEOUT_S = 25
GAP_S = 0.45  # the breath between one headline and the next
READ_PER_PAGE = 2  # of the three a page shows; the whole bulletin stays near half a minute

WORDS = {
    "en": {
        "hello": {"morning": ["Good morning.", "Good morning, and welcome."],
                  "afternoon": ["Good afternoon.", "Hello, and good afternoon."],
                  "evening": ["Good evening.", "Good evening, and welcome."],
                  "night": ["Hello, if you are just joining us.", "Welcome, if you are with us through the night."]},
        "intro": ["This is Orbitwire, and here is the latest from around the world.",
                  "You are watching Orbitwire. Here is where things stand right now.",
                  "On Orbitwire, the stories leading the world this hour."],
        "more": ["Also making news.", "Elsewhere.", "In other stories.", "And there is more."],
        "by": ["{outlet} reports that {title}", "According to {outlet}, {title}", "{title}, {outlet} reports.",
               "{title}."],
        "quake_one": "The strongest earthquake in the last day was magnitude {mag}, {place}.",
        "quake_none": "No major earthquakes in the last day.",
        "next": ["Coming up next: {title}.", "Next on Orbitwire: {title}.", "Stay with us. Next, {title}."],
        "follow": ["Follow Orbitwire for the world's biggest stories.",
                   "If you are new here, follow Orbitwire for every update."],
    },
    "hi": {
        "hello": {"morning": ["नमस्कार, सुप्रभात।", "नमस्कार, आपकी सुबह शुभ हो।"],
                  "afternoon": ["नमस्कार।", "नमस्कार, दोपहर में आपका स्वागत है।"],
                  "evening": ["नमस्कार, शुभ संध्या।", "नमस्कार।"],
                  "night": ["नमस्कार, देर रात तक साथ बने रहने के लिए शुक्रिया।", "नमस्कार, अगर आप अभी जुड़े हैं।"]},
        "intro": ["आप देख रहे हैं पल पल न्यूज़। अब तक की बड़ी खबरें।",
                  "पल पल न्यूज़ पर आपका स्वागत है। जानिए इस वक्त की बड़ी खबरें।",
                  "पल पल न्यूज़ पर, देश की अभी की बड़ी खबरें।"],
        "more": ["दूसरी बड़ी खबर।", "अब बात करते हैं एक और खबर की।", "इसके अलावा।", "और अब आगे।"],
        "by": ["{outlet} के मुताबिक, {title}", "{title}। यह खबर {outlet} से।", "{title}।",
               "{outlet} की रिपोर्ट के अनुसार, {title}"],
        "quake_one": "",
        "quake_none": "",
        "next": ["आगे देखिए: {title}।", "अब देखिए: {title}।", "बने रहिए हमारे साथ। आगे, {title}।"],
        "follow": ["हर खबर सबसे पहले पाने के लिए पल पल न्यूज़ को फॉलो कीजिए।",
                   "पल पल न्यूज़ को सब्सक्राइब करना न भूलें।"],
    },
}


def _pick(options: list[str], seed: str) -> str:
    return options[zlib.crc32(seed.encode()) % len(options)]


def _part_of_day(hour: int) -> str:
    if hour < 5 or hour >= 22:
        return "night"
    return "morning" if hour < 12 else "afternoon" if hour < 17 else "evening"


def clean(title: str) -> str:
    """A headline as a newsreader says it: no LIVE tags, section labels after a pipe, ellipses, quotes or emoji."""
    t = re.sub(r"\b(LIVE|Live)( Updates?| UPDATES?)?\b\s*[:|-]?\s*", "", title)
    if " | " in t:
        head, rest = t.split(" | ", 1)
        t = head if len(head) >= 20 else f"{head}, {rest}"
    t = re.sub(r"[\u2026]|\.{2,}", ", ", t)
    t = re.sub(r"\s*[|•·]\s*", ", ", t)
    t = re.sub(r"(?<![A-Za-z])['‘’]|['‘’](?![A-Za-z])|[\"“”«»]", "", t).replace("’", "'")
    t = re.sub(r"[^\w\s,.?!:;'%₹$€£()\-\u0900-\u097F]", "", t)
    t = re.sub(r"\s*,\s*(,\s*)+", ", ", t)
    return re.sub(r"\s+", " ", t).strip(" ,;:-")


COMPASS = {"N": "north", "S": "south", "E": "east", "W": "west", "NE": "north-east", "NW": "north-west",
           "SE": "south-east", "SW": "south-west", "NNE": "north-north-east", "ENE": "east-north-east",
           "ESE": "east-south-east", "SSE": "south-south-east", "SSW": "south-south-west", "WSW": "west-south-west",
           "WNW": "west-north-west", "NNW": "north-north-west"}


def spoken_place(place: str) -> str:
    """USGS's "45 km NE of Hualien City, Taiwan" as a reader says it: "45 kilometres north-east of ..."."""
    p = re.sub(r"\bkm\b", "kilometres", clean(place))
    p = re.sub(r"\b([NSEW]{1,3})\b(?= of)", lambda m: COMPASS.get(m.group(1), m.group(1)), p)
    return p if p[:1].isdigit() else f"near {p}" if p else "location unknown"


def _headline(ch: Channel, row: dict, seed: str) -> str:
    w = WORDS[ch.lang]
    title = clean(row.get("title", ""))
    if not title:
        return ""
    line = _pick(w["by"], seed + title).format(outlet=row.get("outlet", ""), title=title)
    if not line.rstrip().endswith((".", "?", "!", "।")):
        line += "।" if ch.lang == "hi" else "."
    return re.sub(r"([?!])\s*[।.]", r"\1", line)


def script(ch: Channel, pages: list[str], desk, at: datetime, coming: dict | None) -> list[str]:
    """One spoken part per update page, in page order (empty string: that page is not read aloud)."""
    w = WORDS[ch.lang]
    local = at.astimezone(ZoneInfo("Asia/Kolkata" if ch.lang == "hi" else ch.clocks[0][1]))
    seed = f"{ch.key}{local:%Y%m%d%H%M}"
    parts = []
    for page in pages:
        if page.startswith("headlines"):
            n = int(page.rsplit("-", 1)[-1]) if "-" in page else 1
            rows = desk.headlines[(n - 1) * 3:n * 3] or desk.headlines[:3]
            lines = [_headline(ch, r, seed + str(i)) for i, r in enumerate(rows[:READ_PER_PAGE])]
            lines = [x for x in lines if x]
            if n == 1:
                lead = [_pick(w["hello"][_part_of_day(local.hour)], seed), _pick(w["intro"], seed + "i")]
            else:
                lead = [_pick(w["more"], seed + "m")]
            parts.append(" ".join(lead + lines) if lines else "")
        elif page == "quakes":
            q = desk.quakes[0] if desk.quakes else None
            if q and w["quake_one"]:
                parts.append(w["quake_one"].format(mag=f"{float(q['mag']):.1f}", place=spoken_place(q.get("place", ""))))
            else:
                parts.append(w["quake_none"])
        else:
            line = _pick(w["follow"], seed + "f")
            if coming and coming.get("title"):
                line = _pick(w["next"], seed + "n").format(title=clean(coming["title"])) + " " + line
            parts.append(line)
    return parts


async def _say(text: str, voice: str, rate: str, path: Path) -> None:
    import edge_tts

    await edge_tts.Communicate(text, voice, rate=rate).save(str(path))


def speak(ch: Channel, text: str, path: Path) -> float:
    """Read `text` into `path` (mp3); returns its length in seconds. Raises when the voice is unreachable."""
    voice, rate = VOICES[ch.key]
    last: Exception | None = None
    for _ in range(2):
        try:
            asyncio.run(asyncio.wait_for(_say(text, voice, rate, path), TIMEOUT_S))
            out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json",
                                  str(path)], capture_output=True, text=True, check=True).stdout
            return float(json.loads(out)["format"]["duration"])
        except Exception as exc:
            last = exc
            path.unlink(missing_ok=True)
    raise RuntimeError(f"voice unavailable: {type(last).__name__}")
