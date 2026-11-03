"""Live data the desks show: trusted outlets' headlines, USGS earthquakes, and each channel's own Shorts.

Everything is public and keyless. A source that fails keeps its last good answer, so a dead feed never blanks the
stream; every answer is cached on disk so a restart has something to show at once.
"""
from __future__ import annotations

import html
import json
import os
import re
import shutil
import subprocess
import threading
import time
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from concurrent.futures import TimeoutError as FuturesTimeout
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

from . import trending
from .channels import Channel

UA = ("Mozilla/5.0 (X11; Linux aarch64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36")
USGS = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/2.5_day.geojson"
LIBRARY = os.environ.get("LIVE_DESK_LIBRARY",
                         "https://github.com/ashivam-dot/live-desk/releases/download/library/library.json")
OWN_MAX_AGE_D = 30  # the channel's back catalogue airs for a month; older news is history, not a bulletin
OWN_PER_SYNC = 6
OWN_EVERY_S = 900
YTDLP_FORMAT = "bv*[vcodec^=avc1][height<=1920]+ba[acodec^=mp4a]/b[ext=mp4]/b"
HEADLINE_MAX_AGE_H = 18
REFRESH_S = 300
FEED_TIMEOUT_S = 15
REFRESH_DEADLINE_S = 45


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


def write_json(path: Path, data) -> None:
    """Replace a file whole, so a crash mid-write never leaves half a file for the next start."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False))
    os.replace(tmp, path)


def read_json(path: Path, default):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


class Desk:
    """What one channel's stream knows about the world right now. Lists are swapped whole, never edited in place,
    so the encoder can read them while a refresh runs in another thread."""

    def __init__(self, ch: Channel, home: Path):
        self.ch, self.home = ch, home
        home.mkdir(parents=True, exist_ok=True)
        self.cache = home / "sources.json"
        saved = read_json(self.cache, {})
        self.headlines: list[dict] = saved.get("headlines", []) if isinstance(saved, dict) else []
        self.quakes: list[dict] = saved.get("quakes", []) if isinstance(saved, dict) else []
        self.errors: list[str] = []
        self.fetched = 0.0

    def _feed(self, outlet: str, url: str) -> list[dict]:
        cutoff = time.time() - HEADLINE_MAX_AGE_H * 3600
        rows = parse_feed(get(url, timeout=FEED_TIMEOUT_S), outlet)
        return [r for r in rows if not r["at"] or datetime.fromisoformat(r["at"]).timestamp() > cutoff]

    def refresh(self, force: bool = False) -> None:
        if not force and time.time() - self.fetched < REFRESH_S:
            return
        self.fetched, errors, lists, top, trends = time.time(), [], [], [], []
        cutoff = time.time() - HEADLINE_MAX_AGE_H * 3600
        pool = ThreadPoolExecutor(max_workers=12)
        jobs = {pool.submit(self._feed, o, u): o for o, u in self.ch.feeds}
        if self.ch.quakes:
            jobs[pool.submit(lambda: quakes_from(get(USGS, timeout=FEED_TIMEOUT_S)))] = "USGS"
        for url in self.ch.top_stories:
            jobs[pool.submit(lambda u=url: trending.parse_top_stories(get(u, timeout=FEED_TIMEOUT_S)))] = "top"
        for url in self.ch.trends:
            jobs[pool.submit(lambda u=url: trending.parse_trends(get(u, timeout=FEED_TIMEOUT_S)))] = "trends"
        try:
            for job in as_completed(jobs, timeout=REFRESH_DEADLINE_S):
                try:
                    rows = job.result()
                except Exception as exc:  # one dead feed never blanks the page
                    errors.append(f"{jobs[job]}: {type(exc).__name__}")
                    continue
                if jobs[job] == "USGS":
                    self.quakes = rows
                elif jobs[job] == "top":
                    top += [r for r in rows if not r["at"] or datetime.fromisoformat(r["at"]).timestamp() > cutoff]
                elif jobs[job] == "trends":
                    trends += rows
                else:
                    lists.append(rows)
        except FuturesTimeout:
            errors.append("some feeds too slow")
        pool.shutdown(wait=False, cancel_futures=True)
        try:
            fresh = trending.rank(lists, top, trends, 24, self.ch.lang) if lists or top else []
        except Exception as exc:  # ranking is a refinement: the plain rotation still airs
            errors.append(f"ranking: {type(exc).__name__}")
            fresh = interleave(lists, 24)
        if fresh:
            self.headlines = fresh
        self.errors = errors
        write_json(self.cache, {"headlines": self.headlines, "quakes": self.quakes})


class Library:
    """The channel's own public Shorts: those the librarian workflow collects into the repo's `library` release,
    plus (on hosts with yt-dlp) the channel's back catalogue fetched from YouTube itself."""

    def __init__(self, ch: Channel, home: Path, manifest_url: str = LIBRARY):
        self.ch, self.dir, self.url = ch, home / "library", manifest_url
        self.own_dir = self.dir / "own"
        self.own_dir.mkdir(parents=True, exist_ok=True)
        self.index = self.dir / "index.json"
        self.own_state = self.own_dir / "state.json"
        saved = read_json(self.index, [])
        saved = [m for m in saved if isinstance(m, dict) and Path(m.get("path", "")).exists()] \
            if isinstance(saved, list) else []
        self.shared = [m for m in saved if not m.get("own")]
        self.own = [m for m in saved if m.get("own")]
        self.items: list[dict] = saved
        self.lock = threading.Lock()
        self.synced = self.own_synced = 0.0

    def _publish(self, keep: list[dict] | None = None, own: list[dict] | None = None) -> None:
        """The librarian's Shorts and the channel's back catalogue, as one list newest first (the librarian's copy
        wins when both have a video)."""
        with self.lock:
            self.shared = self.shared if keep is None else keep
            self.own = self.own if own is None else own
            ids = {m["video_id"] for m in self.shared}
            merged = self.shared + [m for m in self.own if m["video_id"] not in ids]
            self.items = sorted(merged, key=lambda m: m.get("published_at", ""), reverse=True)
            write_json(self.index, self.items)

    def sync_own(self) -> None:
        """Download the channel's own public Shorts from the last OWN_MAX_AGE_D days that the librarian no longer
        holds (its copies expire after 14 days), a few per pass. Needs yt-dlp; hosts without it skip this. A Short
        the channel no longer lists (removed or private) is deleted here too."""
        ytdlp = shutil.which("yt-dlp")
        if not ytdlp or time.time() - self.own_synced < OWN_EVERY_S:
            return
        self.own_synced = time.time()
        listing = subprocess.run([ytdlp, "--flat-playlist", "--print", "%(id)s",
                                  f"https://www.youtube.com/{self.ch.handle}/shorts"],
                                 capture_output=True, text=True, timeout=180)
        ids = [x.strip() for x in listing.stdout.splitlines() if re.fullmatch(r"[\w-]{11}", x.strip())]
        if listing.returncode != 0 or not ids:
            return  # YouTube would not say: change nothing
        since = datetime.fromisoformat(self.ch.since).timestamp() if self.ch.since else 0
        cutoff = max(time.time() - OWN_MAX_AGE_D * 86400, since)
        state = read_json(self.own_state, {})
        state = state if isinstance(state, dict) else {}
        listed, shared = set(ids), {m["video_id"] for m in self.shared}
        for vid, meta in list(state.items()):
            path = self.own_dir / f"{vid}.mp4"
            if vid not in listed or vid in shared:
                path.unlink(missing_ok=True)
                state.pop(vid)
            elif not meta.get("skip") and datetime.fromisoformat(meta["published_at"]).timestamp() < cutoff:
                path.unlink(missing_ok=True)
                state[vid] = {"skip": True}
        fetched = 0
        for n, vid in enumerate(ids):
            if fetched >= OWN_PER_SYNC:
                break
            if vid in state or vid in shared:
                continue
            fetched += 1
            path = self.own_dir / f"{vid}.mp4"
            got = subprocess.run([ytdlp, "--no-progress", "--no-playlist", "-f", YTDLP_FORMAT,
                                  "--merge-output-format", "mp4", "--match-filter", f"timestamp>={int(cutoff)}",
                                  "-o", str(self.own_dir / f"{vid}.%(ext)s"),
                                  "--print", "after_move:%(timestamp)s\t%(title)s",
                                  f"https://www.youtube.com/shorts/{vid}"], capture_output=True, text=True, timeout=300)
            line = (got.stdout.strip().splitlines() or [""])[-1]
            if got.returncode == 0 and path.exists() and "\t" in line:
                ts, title = line.split("\t", 1)
                state[vid] = {"channel": self.ch.key, "video_id": vid, "own": True, "path": str(path),
                              "title": re.sub(r"\s*#\S+", "", title).strip(),
                              "published_at": datetime.fromtimestamp(int(float(ts)), timezone.utc).isoformat()}
            elif got.returncode == 0:  # older than the cutoff; the listing runs newest first, so the rest are too
                for later in ids[n:]:
                    state.setdefault(later, {"skip": True})
                break
            write_json(self.own_state, state)
            self._publish(own=[m for m in state.values() if not m.get("skip") and Path(m["path"]).exists()])
        write_json(self.own_state, state)
        self._publish(own=[m for m in state.values() if not m.get("skip") and Path(m["path"]).exists()])

    def sync(self, every_s: float = 1800) -> None:
        if time.time() - self.synced < every_s:
            return
        self.synced = time.time()
        try:
            manifest = json.loads(get(self.url))
            wanted = [m for m in manifest["shorts"] if m.get("channel") == self.ch.key]
        except Exception:
            return  # keep playing what is already here
        if not wanted and self.items:
            return  # a whole channel vanishing at once is a librarian fault, not a retraction
        have = {f.name for f in self.dir.glob("*.mp4")}
        keep = [m | {"path": str(self.dir / f"{m['video_id']}.mp4")} for m in wanted if f"{m['video_id']}.mp4" in have]
        names = {Path(m["path"]).name for m in keep}
        for name in have - names:
            (self.dir / name).unlink(missing_ok=True)  # retracted or aged out of the library: never aired again
        self._publish(keep)
        for m in wanted:
            path = self.dir / f"{m['video_id']}.mp4"
            if path.name in names:
                continue
            tmp = path.with_suffix(".part")
            try:
                req = urllib.request.Request(m["url"], headers={"User-Agent": UA})
                with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as f:
                    shutil.copyfileobj(r, f, 1 << 20)
                tmp.rename(path)
            except Exception:
                tmp.unlink(missing_ok=True)
                continue
            keep.append(m | {"path": str(path)})
            self._publish(keep)  # each Short can air as soon as it lands
