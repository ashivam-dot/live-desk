"""Which stories matter most right now: the desk's headlines grouped into stories and ranked by how widely they are
covered, where Google News places them among its top stories, how many people are searching for them (Google
Trends), and how fresh they are. All keyless public feeds; any of them failing only drops its signal.
"""
from __future__ import annotations

import math
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime

STOP = set("""the a an of in on to for and or is are was were be been by with at from as after over amid into its it
this that than says said say new live latest updates update news report reports how why what who will can could
vs v""".split()) | set("""में की के का ने से को पर और है हैं था थी थे भी यह वह ये वो एक लिए साथ बाद अब तक कहा बोले
जी हुआ हुई होगा होगी क्या कैसे लाइव अपडेट खबर बड़ी नहीं गया गई किया दिया रहे रहा रही""".split())
BREAKING_OUTLETS = 3
BREAKING_AGE_H = 1.0
BREAKING_NEW_H = 3.0  # a story first reported longer ago than this is ongoing, not breaking
TREND_ADD_TRAFFIC = 50000  # a search spike this big airs even when no trusted feed has the story yet


def tokens(title: str) -> set[str]:
    t = title.lower().replace("ँ", "ं").replace("़", "")
    return {w for w in re.findall(r"[a-z0-9\u0900-\u097F]+", t) if len(w) > 2 and w not in STOP}


def same_story(a: set[str], b: set[str]) -> bool:
    shared = len(a & b)
    return shared >= 3 or (shared >= 2 and shared / max(1, min(len(a), len(b))) >= 0.5)


def traffic(text: str | None) -> int:
    m = re.match(r"([\d.,]+)\s*([KkMm]?)", (text or "").strip())
    if not m:
        return 0
    n = float(m.group(1).replace(",", ""))
    return int(n * {"k": 1e3, "m": 1e6}.get(m.group(2).lower(), 1))


def parse_top_stories(data: bytes) -> list[dict]:
    """Google News top stories in its order: [{title, outlet, at, top}] where top is 1.0 for the lead story."""
    from .sources import _when, clean

    items = [el for el in ET.fromstring(data).iter("item")]
    out = []
    for i, el in enumerate(items):
        title = clean(el.findtext("title") or "")
        outlet = clean(el.findtext("source") or "")
        if outlet and title.endswith(f" - {outlet}"):
            title = title[: -len(outlet) - 3]
        elif " - " in title:
            title, outlet = title.rsplit(" - ", 1)
        at = _when(el.findtext("pubDate"))
        if title:
            out.append({"title": title.strip(), "outlet": outlet.strip(), "at": at.isoformat() if at else None,
                        "top": 1 - i / max(1, len(items))})
    return out


def parse_trends(data: bytes) -> list[dict]:
    """Google Trends daily searches: [{term, traffic, news: [{title, outlet}]}]."""
    out = []
    for el in ET.fromstring(data).iter("item"):
        term = (el.findtext("title") or "").strip()
        if not term:
            continue
        found = {"term": term, "traffic": 0, "news": []}
        for c in el:
            tag = c.tag.rsplit("}", 1)[-1]
            if tag == "approx_traffic":
                found["traffic"] = traffic(c.text)
            elif tag == "news_item":
                f = {x.tag.rsplit("}", 1)[-1]: (x.text or "").strip() for x in c}
                if f.get("news_item_title"):
                    found["news"].append({"title": f["news_item_title"], "outlet": f.get("news_item_source", "")})
        out.append(found)
    return out


def rank(feeds: list[list[dict]], top: list[dict], trends: list[dict], limit: int, lang: str,
         now: float | None = None) -> list[dict]:
    """The most important stories first, one headline per story, from the channel's own outlets where possible.
    Each row keeps {title, outlet, at} and adds coverage (outlets carrying it) and breaking."""
    now = now or time.time()
    candidates = [dict(r, own=True) for rows in feeds for r in rows] + [dict(r, own=False) for r in top]
    for t in trends:
        if t["traffic"] >= TREND_ADD_TRAFFIC and t["news"] and lang == "en":
            candidates.append({"title": t["news"][0]["title"], "outlet": t["news"][0]["outlet"], "at": None,
                               "own": False})
    stories: list[dict] = []
    for row in candidates:
        tok = tokens(row["title"])
        if not tok:
            continue
        # matched against the story's first headline, not everything gathered so far, so stories never snowball
        home = next((s for s in stories if same_story(tok, s["seed"])), None)
        if home is None:
            home = {"seed": tok, "tokens": set(), "rows": []}
            stories.append(home)
        home["tokens"] |= tok
        home["rows"].append(row)

    def age_h(row: dict) -> float:
        return (now - datetime.fromisoformat(row["at"]).timestamp()) / 3600 if row.get("at") else 6.0

    for s in stories:
        outlets = {r["outlet"] for r in s["rows"] if r["outlet"]}
        best_top = max((r.get("top", 0) for r in s["rows"]), default=0)
        searched = 0.0
        for t in trends:
            term = tokens(t["term"])
            if (term and term <= s["tokens"]) or any(same_story(tokens(n["title"]), s["tokens"]) for n in t["news"]):
                searched = max(searched, math.log10(max(t["traffic"], 10)) / 6)
        newest = min(age_h(r) for r in s["rows"])
        s["coverage"], s["newest"], s["oldest"] = len(outlets), newest, max(age_h(r) for r in s["rows"])
        s["score"] = min(len(outlets), 6) + 3 * best_top + 2.5 * searched + 2 * math.exp(-max(newest, 0) / 8)
    stories.sort(key=lambda s: -s["score"])
    out = []
    for s in stories[:limit]:
        own = [r for r in s["rows"] if r["own"]]
        pick = max(own or s["rows"], key=lambda r: r.get("at") or "")
        out.append({"title": pick["title"], "outlet": pick["outlet"], "at": pick.get("at"),
                    "coverage": s["coverage"],
                    "breaking": s["coverage"] >= BREAKING_OUTLETS and s["newest"] <= BREAKING_AGE_H
                    and s["oldest"] <= BREAKING_NEW_H})
    return out
