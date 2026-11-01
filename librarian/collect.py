"""Collect each channel's published Shorts into this repo's `library` release, for the live servers to play.

Both producers keep every finished Short as a run artifact for 14 days (work/<story>/short.mp4). A Short joins the
library only when its story is in the producer's published ledger (dry runs never are) and YouTube still shows the
video publicly (oEmbed answers); one that stops being public leaves the library on the next run. library.json lists
what the servers may air.

    GH_TOKEN=... python3 librarian/collect.py [--dry]
"""
from __future__ import annotations

import io
import json
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

REPO = "ashivam-dot/live-desk"
TAG = "library"
SOURCES = (
    # channel, producer repo, artifact name prefix, first Short of the channel's current identity
    ("orbitwire", "ashivam-dot/rebuilt-in-3d", "shorts-", "2026-10-09T00:00:00+00:00"),
    ("palpal", "ashivam-dot/hindi-news-desk", "short-", "2026-10-29T00:00:00+00:00"),
)
MAX_PER_CHANNEL = 40
MIN_ARTIFACT = 1_000_000  # artifacts without a video in them are a few KB
DOWNLOAD = f"https://github.com/{REPO}/releases/download/{TAG}/"


def gh(*args: str, binary: bool = False):
    out = subprocess.run(["gh", *args], capture_output=True, check=True)
    return out.stdout if binary else out.stdout.decode()


def public(video_id: str) -> bool | None:
    """True when YouTube shows the video publicly, False when it does not, None when YouTube could not be asked."""
    url = f"https://www.youtube.com/oembed?format=json&url=https://www.youtube.com/shorts/{video_id}"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "live-desk-librarian"}),
                                    timeout=20):
            return True
    except urllib.error.HTTPError as exc:
        return False if exc.code in (400, 401, 403, 404) else None
    except Exception:
        return None


def published(repo: str, since: str) -> dict[str, dict]:
    raw = urllib.request.urlopen(f"https://raw.githubusercontent.com/{repo}/state/published.json", timeout=30).read()
    rows = json.loads(raw)
    rows = rows if isinstance(rows, list) else list(rows.values())
    out = {}
    for r in rows:
        vid = r.get("video_id") or (r.get("url") or "").rstrip("/").rsplit("/", 1)[-1]
        if vid and r.get("published_at", "") >= since and r.get("privacy", "public") == "public":
            out[r["id"]] = {"video_id": vid, "title": r.get("title", ""), "published_at": r["published_at"]}
    return out


def artifacts(repo: str, prefix: str) -> list[dict]:
    pages = json.loads(gh("api", "--paginate", "--slurp", f"repos/{repo}/actions/artifacts?per_page=100"))
    rows = [a for page in pages for a in page["artifacts"]]
    return [a for a in rows if a["name"].startswith(prefix) and not a["expired"] and a["size_in_bytes"] > MIN_ARTIFACT]


def release_assets() -> dict[str, int]:
    try:
        rows = json.loads(gh("api", f"repos/{REPO}/releases/tags/{TAG}"))["assets"]
    except subprocess.CalledProcessError:
        gh("release", "create", TAG, "-R", REPO, "--title", "Shorts library for the live streams",
           "--notes", "Written by the librarian workflow; the live servers read library.json.")
        rows = []
    return {a["name"]: a["id"] for a in rows}


def manifest() -> list[dict]:
    try:
        return json.loads(urllib.request.urlopen(DOWNLOAD + "library.json", timeout=30).read())["shorts"]
    except Exception:
        return []


def main(dry: bool = False) -> int:
    assets = release_assets()
    shorts = [m for m in manifest() if f"{m['channel']}-{m['video_id']}.mp4" in assets]
    have = {(m["channel"], m["video_id"]) for m in shorts}
    notes = []
    for channel, repo, prefix, since in SOURCES:
        told = published(repo, since)
        for art in artifacts(repo, prefix):
            zipped = None
            for sid, row in told.items():
                if (channel, row["video_id"]) in have:
                    continue
                if zipped is None:
                    zipped = zipfile.ZipFile(io.BytesIO(gh("api", f"repos/{repo}/actions/artifacts/{art['id']}/zip",
                                                           binary=True)))
                    names = set(zipped.namelist())
                if f"{sid}/short.mp4" not in names:
                    continue
                if public(row["video_id"]) is not True:
                    continue
                name = f"{channel}-{row['video_id']}.mp4"
                if not dry:
                    with tempfile.TemporaryDirectory() as tmp:
                        path = Path(tmp) / name
                        path.write_bytes(zipped.read(f"{sid}/short.mp4"))
                        gh("release", "upload", TAG, str(path), "-R", REPO, "--clobber")
                shorts.append({"channel": channel, "video_id": row["video_id"], "story": sid, "title": row["title"],
                               "published_at": row["published_at"], "url": DOWNLOAD + name})
                have.add((channel, row["video_id"]))
                notes.append(f"added {name}: {row['title'][:60]}")
    kept = []
    for channel, *_ in SOURCES:
        mine = sorted((m for m in shorts if m["channel"] == channel), key=lambda m: m["published_at"], reverse=True)
        for i, m in enumerate(mine):
            name = f"{channel}-{m['video_id']}.mp4"
            if i >= MAX_PER_CHANNEL or public(m["video_id"]) is False:
                notes.append(f"removed {name}")
                if not dry and name in assets:
                    gh("api", "-X", "DELETE", f"repos/{REPO}/releases/assets/{assets[name]}")
                continue
            kept.append(m)
    if not dry:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "library.json"
            path.write_text(json.dumps({"shorts": kept}, ensure_ascii=False, indent=1))
            gh("release", "upload", TAG, str(path), "-R", REPO, "--clobber")
    counts = {c: sum(m["channel"] == c for m in kept) for c, *_ in SOURCES}
    print(json.dumps({"library": counts, "changes": notes}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main(dry="--dry" in sys.argv))
