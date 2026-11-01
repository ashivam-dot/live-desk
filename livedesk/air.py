"""One channel on air: build the programme ahead of time and push it to YouTube Live over one RTMP connection.

    python -m livedesk.air orbitwire --home /var/lib/live-desk --key-file /etc/live-desk/orbitwire.key
    python -m livedesk.air palpal --home ./work --out test.flv --minutes 3      # a local test, no YouTube

The programme: the channel's Shorts newest first (a new Short airs next), and a live update after every
SHORTS_PER_UPDATE of them. A key is read from its file and never printed: ffmpeg's messages are redacted.
"""
from __future__ import annotations

import argparse
import json
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import segments
from .channels import CHANNELS, Channel
from .sources import Desk, Library

INGEST = "rtmp://a.rtmp.youtube.com/live2/"
LEAD_S = 150
SHORTS_PER_UPDATE = 3
# Every chunk restarts the MPEG-TS continuity counters and AAC priming, so each junction draws these warnings while
# the packets still pass through; they would otherwise flood the journal.
JUNCTION_NOISE = re.compile(r"Packet corrupt|corrupt input packet|Non-monotonic DTS")


class Programme:
    """Which segment airs next."""

    def __init__(self, library: Library):
        self.library, self.order, self.since_update, self.known = library, deque(), 0, set()
        self.bad: set[str] = set()  # Shorts that failed to encode sit out until the process restarts

    def refresh(self) -> None:
        self.library.sync()
        items = {m["video_id"]: m for m in self.library.items if m["video_id"] not in self.bad}
        fresh = [v for v in items if v not in self.known]
        self.known = set(items)
        self.order = deque([v for v in self.order if v in items])
        for v in reversed(fresh):  # newest first, ahead of the rotation
            self.order.appendleft(v)
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
        return self.items.get(self.order[0]) if getattr(self, "items", None) and self.order else None


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
        self.offset = self.queued_s = 0.0
        self.started: float | None = None
        self.count = 0
        args = ["ffmpeg", "-hide_banner", "-loglevel", "warning"] + ([] if fast else ["-re"]) + [
            "-f", "mpegts", "-i", "pipe:0", "-c", "copy", "-bsf:a", "aac_adtstoasc", "-f", "flv", out]
        self.pusher = subprocess.Popen(args, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
        threading.Thread(target=self._relay_log, daemon=True).start()

    def _relay_log(self) -> None:
        for line in self.pusher.stderr:
            text = redact(line.decode(errors="replace").rstrip(), self.secret)
            if not JUNCTION_NOISE.search(text):
                print("pusher:", text, flush=True)

    def air_at(self) -> datetime:
        base = self.started or time.time()
        return datetime.fromtimestamp(base + self.offset, timezone.utc)

    def build_one(self) -> None:
        kind, short = self.programme.next()
        self.desk.refresh()
        self.count += 1
        work = self.work / f"{self.count:06d}-{kind}"
        try:
            if kind == "short" and short:
                path, length = segments.short_segment(self.ch, short, self.desk, work, self.offset, self.air_at(),
                                                      self.programme.peek())
            else:
                path, length = segments.update_segment(self.ch, self.desk, work, self.offset, self.air_at(),
                                                       self.programme.peek())
        except Exception as exc:  # a bad file or a failed encode costs one segment, never the stream
            print(f"segment {work.name} failed: {type(exc).__name__}: {exc}"[:300], flush=True)
            shutil.rmtree(work, ignore_errors=True)
            if short:
                self.programme.bad.add(short["video_id"])
                self.programme.order = deque(v for v in self.programme.order if v != short["video_id"])
            return
        shutil.rmtree(work, ignore_errors=True)
        self.offset += length
        self.queued_s += length
        self.ready.put((path, length, kind, (short or {}).get("title", "")))

    def feed(self) -> None:
        while True:
            path, length, kind, title = self.ready.get()
            if self.started is None:
                self.started = time.time() - (self.offset - self.queued_s)
            print(f"on air {datetime.now(timezone.utc):%H:%M:%S} {kind} {length:.1f}s {title[:60]}", flush=True)
            with open(path, "rb") as f:
                shutil.copyfileobj(f, self.pusher.stdin, 1 << 16)
            self.pusher.stdin.flush()
            path.unlink(missing_ok=True)
            self.queued_s -= length
            self.status(kind, title)

    def status(self, kind: str, title: str) -> None:
        (self.home / "status.json").write_text(json.dumps({
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "segment": kind, "title": title,
            "library": len(self.library.items), "headlines": len(self.desk.headlines),
            "quakes": len(self.desk.quakes), "errors": self.desk.errors}, ensure_ascii=False))

    def run(self, minutes: float | None) -> int:
        self.desk.refresh(force=True)
        self.build_one()
        threading.Thread(target=self.feed, daemon=True).start()
        until = time.time() + minutes * 60 if minutes else None
        while self.pusher.poll() is None:
            if until and time.time() > until:
                break
            # queued_s is programme not yet handed to the pusher, which reads it in real time: the lead on air.
            if self.ready.qsize() < 2 or self.queued_s < LEAD_S:
                self.build_one()
            else:
                time.sleep(2)
        if until:
            while not self.ready.empty():
                time.sleep(1)
            time.sleep(2)
            self.pusher.stdin.close()
            return self.pusher.wait()
        print(f"pusher exited with {self.pusher.returncode}", flush=True)
        return 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="livedesk.air")
    p.add_argument("channel", choices=sorted(CHANNELS))
    p.add_argument("--home", default="/var/lib/live-desk")
    p.add_argument("--key-file", help="file holding the YouTube stream key (one line)")
    p.add_argument("--out", help="write the stream to this file instead of YouTube (testing)")
    p.add_argument("--minutes", type=float, help="stop after this long (testing)")
    p.add_argument("--fast", action="store_true", help="do not pace to real time (testing with --out)")
    a = p.parse_args(argv)
    secret = None
    if a.out:
        out = a.out
    else:
        if not a.key_file or not Path(a.key_file).exists():
            print(f"no stream key file for {a.channel}; waiting for the owner to add it", flush=True)
            time.sleep(600)
            return 1
        secret = Path(a.key_file).read_text().strip()
        if not secret or secret.startswith("PASTE"):
            print(f"{a.key_file} has no stream key yet; waiting for the owner to add it", flush=True)
            time.sleep(600)
            return 1
        out = INGEST + secret
    return Air(CHANNELS[a.channel], Path(a.home), out, secret, a.fast).run(a.minutes)


if __name__ == "__main__":
    sys.exit(main())
