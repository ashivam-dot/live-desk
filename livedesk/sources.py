"""Live data the desks show: trusted outlets' headlines, USGS earthquakes, and each channel's own Shorts.

Everything is public and keyless. A source that fails keeps its last good answer, so a dead feed never blanks the
stream; every answer is cached on disk so a restart has something to show at once.
"""
from __future__ import annotations

import html
import json
import os
import re
import time
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

from .channels import Channel

UA = ("Mozilla/5.0 (X11; Linux aarch64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36")
USGS = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/2.5_day.geojson"
LIBRARY = os.environ.get("LIVE_DESK_LIBRARY",
                         "https://github.com/ashivam-dot/live-desk/releases/download/library/library.json")
HEADLINE_MAX_AGE_H = 18
REFRESH_S = 300


def get(url: str, timeout: float = 20) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*", "Accept-Language": "en,hi;q=0.8"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _when(text: str | None) -> datetime | None:
    if not text:
        return None
    try:
        d = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        try:
            d = datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def clean(text: str) -> str:
    text = html.unescape(re.sub(r"<[^>]+>", " ", text or ""))
    return re.sub(r"\s+", " ", text).strip()


def parse_feed(data: bytes, outlet: str) -> list[dict]:
    """Items of an RSS, RDF or Atom feed: [{title, outlet, at}]."""
    root = ET.fromstring(data)
    out = []
    for el in root.iter():
        tag = el.tag.rsplit("}", 1)[-1]
        if tag not in ("item", "entry"):
            continue
        fields = {c.tag.rsplit("}", 1)[-1]: (c.text or "") for c in el}
        title = clean(fields.get("title", ""))
        at = _when(fields.get("pubDate") or fields.get("date") or fields.get("updated") or fields.get("published"))
        if title:
            out.append({"title": title, "outlet": outlet, "at": at.isoformat() if at else None})
    return out


def interleave(lists: list[list[dict]], limit: int) -> list[dict]:
    """Newest first within each outlet, then one from each outlet in turn, so no single feed fills the screen.
    Near-identical titles (the same wire story) appear once."""
    lists = [sorted(rows, key=lambda r: r["at"] or "", reverse=True) for rows in lists if rows]
    out, seen, i = [], set(), 0
    while len(out) < limit and any(i < len(rows) for rows in lists):
        for rows in lists:
            if i < len(rows):
                key = re.sub(r"\W+", "", rows[i]["title"].lower())[:48]
                if key not in seen:
                    seen.add(key)
                    out.append(rows[i])
        i += 1
    return out[:limit]


def quakes_from(data: bytes, limit: int = 6) -> list[dict]:
    """The strongest earthquakes of the last 24 h: [{mag, place, at, lat, lon}]."""
    feats = json.loads(data)["features"]
    now = time.time() * 1000
    rows = [{"mag": round(f["properties"]["mag"], 1), "place": f["properties"]["place"] or "",
             "at": datetime.fromtimestamp(f["properties"]["time"] / 1000, timezone.utc).isoformat(),
             "lon": f["geometry"]["coordinates"][0], "lat": f["geometry"]["coordinates"][1]}
            for f in feats if f["properties"].get("mag") is not None and now - f["properties"]["time"] < 86400e3]
    return sorted(rows, key=lambda r: -r["mag"])[:limit]


class Desk:
    """What one channel's stream knows about the world right now."""

    def __init__(self, ch: Channel, home: Path):
        self.ch, self.home = ch, home
        home.mkdir(parents=True, exist_ok=True)
        self.cache = home / "sources.json"
        saved = json.loads(self.cache.read_text()) if self.cache.exists() else {}
        self.headlines: list[dict] = saved.get("headlines", [])
        self.quakes: list[dict] = saved.get("quakes", [])
        self.errors: list[str] = []
        self.fetched = 0.0

    def refresh(self, force: bool = False) -> None:
        if not force and time.time() - self.fetched < REFRESH_S:
            return
        self.fetched, self.errors = time.time(), []
        lists = []
        for outlet, url in self.ch.feeds:
            try:
                rows = parse_feed(get(url), outlet)
                cutoff = time.time() - HEADLINE_MAX_AGE_H * 3600
                lists.append([r for r in rows if not r["at"] or datetime.fromisoformat(r["at"]).timestamp() > cutoff])
            except Exception as exc:  # one dead feed never blanks the page
                self.errors.append(f"{outlet}: {type(exc).__name__}")
        if fresh := interleave(lists, 24):
            self.headlines = fresh
        if self.ch.quakes:
            try:
                self.quakes = quakes_from(get(USGS))
            except Exception as exc:
                self.errors.append(f"USGS: {type(exc).__name__}")
        self.cache.write_text(json.dumps({"headlines": self.headlines, "quakes": self.quakes}, ensure_ascii=False))


class Library:
    """The channel's own public Shorts, collected by the librarian workflow into the repo's `library` release."""

    def __init__(self, ch: Channel, home: Path, manifest_url: str = LIBRARY):
        self.ch, self.dir, self.url = ch, home / "library", manifest_url
        self.dir.mkdir(parents=True, exist_ok=True)
        self.index = self.dir / "index.json"
        self.items: list[dict] = json.loads(self.index.read_text()) if self.index.exists() else []
        self.synced = 0.0

    def sync(self, every_s: float = 1800) -> None:
        if time.time() - self.synced < every_s:
            return
        self.synced = time.time()
        try:
            manifest = json.loads(get(self.url))
        except Exception:
            return  # keep playing what is already here
        wanted = [m for m in manifest.get("shorts", []) if m.get("channel") == self.ch.key]
        keep = []
        for m in wanted:
            path = self.dir / f"{m['video_id']}.mp4"
            if not path.exists():
                try:
                    tmp = path.with_suffix(".part")
                    tmp.write_bytes(get(m["url"], timeout=300))
                    tmp.rename(path)
                except Exception:
                    continue
            keep.append(m | {"path": str(path)})
        names = {Path(m["path"]).name for m in keep}
        for f in self.dir.glob("*.mp4"):
            if f.name not in names:
                f.unlink()  # retracted or aged out of the library: never aired again
        self.items = sorted(keep, key=lambda m: m.get("published_at", ""), reverse=True)
        self.index.write_text(json.dumps(self.items, ensure_ascii=False))
