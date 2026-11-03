"""One channel on air: build the programme ahead of time and push it to YouTube Live over one RTMP connection.

    python -m livedesk.air orbitwire --home /var/lib/live-desk --key-file /etc/live-desk/orbitwire.key
    python -m livedesk.air palpal --home ./work --out test.flv --minutes 3 --fast    # a local test, no YouTube

The programme: the channel's Shorts newest first (a new Short airs next), and a live update after every
SHORTS_PER_UPDATE of them. A key is read from its file and never printed: ffmpeg's messages are redacted.
Anything that stops the programme reaching YouTube ends the process, and systemd starts it again.
"""
from __future__ import annotations

import argparse
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
from collections import Counter, deque
from datetime import datetime, timezone
from pathlib import Path

from . import segments
from .channels import CHANNELS, Channel
from .sources import Desk, Library, write_json

INGEST = {"rtmp": "rtmp://a.rtmp.youtube.com/live2/",
          "rtmps": "rtmps://a.rtmps.youtube.com:443/live2/"}  # for hosts that only allow outgoing 443
LEAD_S = 240
SHORTS_PER_UPDATE = 4
NEW_SHORT_H = 24  # a Short published this recently airs next when it arrives; an older one joins the rotation
FETCH_EVERY_S = 30
STALL_S = 300  # longer than any segment: nothing aired for this long means the stream is dead
MAX_FAILS_IN_ROW = 5
BENCH_AFTER = 2
# Every chunk restarts the MPEG-TS continuity counters and AAC priming, so each junction draws these warnings while
# the packets still pass through; they would otherwise flood the journal.
JUNCTION_NOISE = re.compile(r"Packet corrupt|corrupt input packet|Non-monotonic DTS")


class Programme:
    """Which segment airs next. Reads the library as it stands; the fetch thread keeps the library fresh."""

    def __init__(self, library: Library):
        self.library, self.order, self.since_update, self.known = library, deque(), 0, set()
        self.items: dict[str, dict] = {}
        self.fails: Counter = Counter()  # a Short that keeps failing to encode sits out until the process restarts

    def refresh(self) -> None:
        items = {m["video_id"]: m for m in self.library.items if self.fails[m["video_id"]] < BENCH_AFTER}
        first_load = not self.known
        fresh = [v for v in items if v not in self.known]
        self.known = set(items)
        self.order = deque([v for v in self.order if v in items])
        cutoff = datetime.now(timezone.utc).timestamp() - NEW_SHORT_H * 3600
        for v in reversed(fresh):  # newest first, ahead of the rotation
            if first_load or _published(items[v]) > cutoff:
                self.order.appendleft(v)
            else:  # an older Short the library just gained (the channel's back catalogue) waits its turn
                self.order.append(v)
        if not self.order:
            self.order.extend(items)
        self.items = items

    def next(self) -> tuple[str, dict | None]:
        self.refresh()
        if not self.items or self.since_update >= SHORTS_PER_UPDATE:
            self.since_update = 0
            return "update", None
        vid = self.order.popleft()
        if not self.order:
            self.order.extend(self.items)
        self.since_update += 1
        return "short", self.items.get(vid)

    def peek(self) -> dict | None:
        return self.items.get(self.order[0]) if self.items and self.order else None

    def failed(self, video_id: str) -> None:
        self.fails[video_id] += 1
        self.order = deque(v for v in self.order if v != video_id)


def _published(item: dict) -> float:
    try:
        return datetime.fromisoformat(item.get("published_at", "")).timestamp()
    except (TypeError, ValueError):
        return 0.0


def redact(text: str, secret: str | None) -> str:
    return text.replace(secret, "<key>") if secret else text


class Air:
    def __init__(self, ch: Channel, home: Path, out: str, secret: str | None, fast: bool = False):
        self.ch, self.home, self.secret = ch, home / ch.key, secret
        self.work = self.home / "segments"
        shutil.rmtree(self.work, ignore_errors=True)
        self.work.mkdir(parents=True)
        self.desk, self.library = Desk(ch, self.home), Library(ch, self.home)
        self.programme = Programme(self.library)
        self.ready: queue.Queue = queue.Queue()
        # offset is written only by the builder, aired only by the feeder: lead = offset - aired needs no lock
        self.offset = self.aired = 0.0
        self.started: float | None = None
        self.progress = time.time()
        self.count = self.fails_in_row = 0
        timeout = ["-rw_timeout", "15000000"] if out.startswith("rtmp") else []
        # Sound is encoded once here, as one continuous stream: each chunk's own AAC opens with encoder priming that
        # overlaps the chunk before it, and copied through it stutters at every junction. aresample trims those
        # overlaps against the chunks' timestamps, so picture and sound stay in step.
        args = ["ffmpeg", "-hide_banner", "-loglevel", "warning"] + ([] if fast else ["-re"]) + [
            "-f", "mpegts", "-i", "pipe:0", "-c:v", "copy", "-af", "aresample=async=1:min_hard_comp=0.01",
            "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2", *timeout, "-f", "flv", out]
        self.pusher = subprocess.Popen(args, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
        threading.Thread(target=self._relay_log, daemon=True).start()

    def _relay_log(self) -> None:
        for line in self.pusher.stderr:
            text = redact(line.decode(errors="replace").rstrip(), self.secret)
            if not JUNCTION_NOISE.search(text):
                print("pusher:", text, flush=True)

    def _fetch(self) -> None:
        while True:
            for job in (self.desk.refresh, self.library.sync):
                try:
                    job()
                except Exception as exc:  # sources keep their last good answer
                    print(f"fetch {job.__qualname__} failed: {type(exc).__name__}: {exc}"[:300], flush=True)
            time.sleep(FETCH_EVERY_S)

    def _own(self) -> None:
        while True:
            try:
                self.library.sync_own()
            except Exception as exc:  # the back catalogue is extra; the librarian's Shorts keep airing
                print(f"own Shorts sync failed: {type(exc).__name__}: {exc}"[:300], flush=True)
            time.sleep(FETCH_EVERY_S)

    def air_at(self) -> datetime:
        """When the segment being built now goes on air (the pusher reads at real time from `started`)."""
        return datetime.fromtimestamp((self.started or time.time()) + self.offset, timezone.utc)

    def build_one(self) -> bool:
        kind, short = self.programme.next()
        self.count += 1
        work = self.work / f"{self.count:06d}-{kind}"
        out = work.with_suffix(".ts")
        try:
            if kind == "short" and short:
                path, length = segments.short_segment(self.ch, short, self.desk, work, self.offset, self.air_at(),
                                                      self.programme.peek())
            else:
                path, length = segments.update_segment(self.ch, self.desk, work, self.offset, self.air_at(),
                                                       self.programme.peek(), self.library.items, self.count)
        except Exception as exc:  # a bad file or a failed encode costs one segment, never the stream
            print(f"segment {work.name} failed: {type(exc).__name__}: {exc}"[:300], flush=True)
            out.unlink(missing_ok=True)
            if short:
                self.programme.failed(short["video_id"])
            return False
        finally:
            shutil.rmtree(work, ignore_errors=True)
        self.offset += length
        self.ready.put((path, self.offset, kind, (short or {}).get("title", "")))
        return True

    def feed(self) -> None:
        while True:
            path, end, kind, title = self.ready.get()
            if self.started is None:
                self.started = time.time()
            print(f"on air {datetime.now(timezone.utc):%H:%M:%S} {kind} {title[:60]}", flush=True)
            with open(path, "rb") as f:
                shutil.copyfileobj(f, self.pusher.stdin, 1 << 16)
            self.pusher.stdin.flush()
            path.unlink(missing_ok=True)
            self.aired, self.progress = end, time.time()
            try:
                self.status(kind, title)
            except OSError:
                pass

    def status(self, kind: str, title: str) -> None:
        write_json(self.home / "status.json", {
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "segment": kind, "title": title,
            "lead_s": round(self.offset - self.aired), "library": len(self.library.items),
            "headlines": len(self.desk.headlines), "quakes": len(self.desk.quakes), "errors": self.desk.errors})

    def run(self, minutes: float | None) -> int:
        threading.Thread(target=self._fetch, daemon=True).start()
        threading.Thread(target=self._own, daemon=True).start()
        time.sleep(0 if self.desk.headlines else 15)  # a cold start gets a moment to fetch its first headlines
        self.build_one()
        feeder = threading.Thread(target=self.feed, daemon=True)
        feeder.start()
        until = time.time() + minutes * 60 if minutes else None
        while self.pusher.poll() is None:
            if until and time.time() > until:
                while not self.ready.empty() and feeder.is_alive():
                    time.sleep(1)
                time.sleep(2)
                self.pusher.stdin.close()
                return self.pusher.wait()
            if not feeder.is_alive() or time.time() - self.progress > STALL_S:
                print("nothing reached the pusher for too long; restarting", flush=True)
                self.pusher.kill()
                return 1
            if self.ready.qsize() < 2 or self.offset - self.aired < LEAD_S:
                if self.build_one():
                    self.fails_in_row = 0
                else:
                    self.fails_in_row += 1
                    if self.fails_in_row >= MAX_FAILS_IN_ROW:
                        print("segments keep failing; restarting", flush=True)
                        self.pusher.kill()
                        return 1
                    time.sleep(5)
            else:
                time.sleep(2)
        print(f"pusher exited with {self.pusher.returncode}", flush=True)
        return 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="livedesk.air")
    p.add_argument("channel", choices=sorted(CHANNELS))
    p.add_argument("--home", default="/var/lib/live-desk")
    p.add_argument("--key-file", help="file holding the YouTube stream key (one line)")
    p.add_argument("--key-env", help="environment variable holding the stream key (a host's secret setting)")
    p.add_argument("--ingest", choices=sorted(INGEST), default="rtmp")
    p.add_argument("--out", help="write the stream to this file instead of YouTube (testing)")
    p.add_argument("--minutes", type=float, help="stop after this long (testing)")
    p.add_argument("--fast", action="store_true", help="do not pace to real time (testing with --out)")
    a = p.parse_args(argv)
    secret = None
    if a.out:
        out = a.out
    else:
        if a.key_env:
            secret = os.environ.get(a.key_env, "").strip()
        else:
            secret = Path(a.key_file).read_text().strip() if a.key_file and Path(a.key_file).exists() else ""
        if not secret or secret.startswith("PASTE"):
            print(f"no stream key for {a.channel} yet; waiting for the owner to add it", flush=True)
            time.sleep(600)
            return 1
        out = INGEST[a.ingest] + secret
    return Air(CHANNELS[a.channel], Path(a.home), out, secret, a.fast).run(a.minutes)


if __name__ == "__main__":
    sys.exit(main())
