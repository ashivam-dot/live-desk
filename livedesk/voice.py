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
import unicodedata
import zlib
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .channels import Channel

VOICES = {"orbitwire": ("en-US-AndrewNeural", "+4%"), "palpal": ("hi-IN-SwaraNeural", "+6%")}
TIMEOUT_S = 25
GAP_S = 0.45  # the breath between one headline and the next
READ_PER_PAGE = 2  # of the three a page shows; the whole bulletin stays under about a minute

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
        "breaking": "Breaking news.",
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
        "breaking": "ब्रेकिंग न्यूज़।",
        "quake_one": "",
        "quake_none": "",
        "next": ["आगे देखिए: {title}।", "अब देखिए: {title}।", "बने रहिए हमारे साथ। आगे, {title}।"],
        "follow": ["हर खबर सबसे पहले पाने के लिए पल पल न्यूज़ को फॉलो कीजिए।",
                   "पल पल न्यूज़ को सब्सक्राइब करना न भूलें।"],
    },
}


# Owner, 2026-11-04: a bookish Hindi word that Indians normally say in English is read in English, the way Aaj Tak
# or ABP anchors speak; written in Devanagari so the Hindi voice says it naturally. Only the spoken words change,
# never the headline on screen. Longer phrases are replaced before the words inside them.
EVERYDAY_HI = {
    "भारतीय अंतरिक्ष अनुसंधान संगठन": "इसरो", "प्रवर्तन निदेशालय": "ईडी", "केंद्रीय अन्वेषण ब्यूरो": "सीबीआई",
    "राष्ट्रीय अन्वेषण अभिकरण": "एनआईए", "भारतीय रिज़र्व बैंक": "आरबीआई", "भारतीय प्रौद्योगिकी संस्थान": "आईआईटी",
    "अखिल भारतीय आयुर्विज्ञान संस्थान": "एम्स", "अंतरराष्ट्रीय मुद्रा कोष": "आईएमएफ", "वस्तु एवं सेवा कर": "जीएसटी",
    "सकल घरेलू उत्पाद": "जीडीपी", "वायु गुणवत्ता सूचकांक": "एक्यूआई", "जनहित याचिका": "पीआईएल",
    "प्राथमिकी": "एफआईआर", "उच्चतम न्यायालय": "सुप्रीम कोर्ट", "सर्वोच्च न्यायालय": "सुप्रीम कोर्ट",
    "उच्च न्यायालय": "हाई कोर्ट", "न्यायालय": "कोर्ट", "न्यायाधीश": "जज", "अधिवक्ता": "वकील",
    "कारावास": "जेल", "कारागार": "जेल", "निर्वाचन आयोग": "चुनाव आयोग", "मध्यावधि चुनाव": "मिडटर्म चुनाव",
    "मतगणना": "काउंटिंग", "मतपत्र": "बैलेट पेपर", "मंत्रिमंडल": "कैबिनेट", "विधेयक": "बिल",
    "अधिसूचना": "नोटिफिकेशन", "महाभियोग": "इंपीचमेंट", "संवाददाता सम्मेलन": "प्रेस कॉन्फ्रेंस",
    "पत्रकार वार्ता": "प्रेस कॉन्फ्रेंस", "साक्षात्कार": "इंटरव्यू", "शिखर सम्मेलन": "समिट",
    "युद्धविराम": "सीज़फायर", "संघर्ष विराम": "सीज़फायर", "प्रक्षेपास्त्र": "मिसाइल", "परमाणु": "न्यूक्लियर",
    "सुरक्षा परिषद": "सिक्योरिटी काउंसिल", "अनुसंधान केंद्र": "रिसर्च सेंटर", "अनुसंधान": "रिसर्च",
    "उपग्रह": "सैटेलाइट", "प्रक्षेपण": "लॉन्च", "अंतरिक्ष यात्री": "एस्ट्रोनॉट", "अंतरिक्ष यान": "स्पेसक्राफ्ट",
    "प्रौद्योगिकी": "टेक्नोलॉजी", "दूरसंचार": "टेलीकॉम", "संगणक": "कंप्यूटर", "अंतर्जाल": "इंटरनेट",
    "दूरभाष": "फ़ोन", "चलभाष": "मोबाइल", "विश्वविद्यालय": "यूनिवर्सिटी", "महाविद्यालय": "कॉलेज",
    "विद्यालय": "स्कूल", "प्रवेश परीक्षा": "एंट्रेंस एग्ज़ाम", "परीक्षा परिणाम": "रिजल्ट", "चिकित्सक": "डॉक्टर",
    "चिकित्सालय": "अस्पताल", "हवाई अड्डा": "एयरपोर्ट", "हवाई अड्डे": "एयरपोर्ट", "उड़ान": "फ्लाइट",
    "उड़ानें": "फ्लाइट्स", "रेलगाड़ी": "ट्रेन", "यातायात": "ट्रैफिक", "नागरिक उड्डयन": "सिविल एविएशन",
    "उड्डयन": "एविएशन", "चक्रवात": "साइक्लोन", "चक्रवाती तूफान": "साइक्लोन", "मौसम विज्ञान विभाग": "मौसम विभाग",
    "सूचकांक": "इंडेक्स", "मुद्रास्फीति": "महंगाई", "आयकर": "इनकम टैक्स", "अनुबंध": "कॉन्ट्रैक्ट",
    "निविदा": "टेंडर", "प्रतिबंध": "बैन", "सांकेतिक": "सिंबॉलिक", "अभियंता": "इंजीनियर", "विद्युत": "बिजली",
    "विश्व कप": "वर्ल्ड कप", "प्रशिक्षक": "कोच",
}
_EVERYDAY = sorted(((unicodedata.normalize("NFC", k), v) for k, v in EVERYDAY_HI.items()), key=lambda kv: -len(kv[0]))
# whole words only: an inflected form ("परमाणुओं") is left alone rather than read half in English
_EVERYDAY_RE = re.compile("(?<![\u0900-\u097F])(" + "|".join(re.escape(k) for k, _ in _EVERYDAY) +
                          ")(?![\u0900-\u097F])")


def everyday_hi(text: str) -> str:
    """Hindi as an anchor says it: bookish words swapped for the English everyone uses (see EVERYDAY_HI)."""
    lookup = dict(_EVERYDAY)
    return _EVERYDAY_RE.sub(lambda m: lookup[m.group(1)], unicodedata.normalize("NFC", text))


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
    if row.get("breaking"):
        line = w["breaking"] + " " + line
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
    return [everyday_hi(p) for p in parts] if ch.lang == "hi" else parts


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
