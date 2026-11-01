import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from livedesk import air, look, segments, sources  # noqa: E402
from livedesk.channels import CHANNELS  # noqa: E402

AT = datetime(2026, 11, 1, 15, 0, tzinfo=timezone.utc)


class FakeLibrary:
    def __init__(self, items):
        self.items = items

    def sync(self):
        pass


def short(vid, when):
    return {"video_id": vid, "title": f"Story {vid}", "published_at": when, "path": f"/x/{vid}.mp4"}


def test_segment_lengths_are_whole_quanta():
    for secs in (21.9, 25.9, 59.99, 0.2):
        n = segments.whole(secs) / segments.QUANTUM
        assert abs(n - round(n)) < 1e-9 and segments.whole(secs) <= max(secs, segments.QUANTUM)
    assert segments.QUANTUM * segments.FPS == 16 and segments.QUANTUM * 48000 / 1024 == pytest.approx(25)


def test_programme_puts_new_shorts_first_and_updates_between():
    lib = FakeLibrary([short("b", "2026-11-01T10:00"), short("a", "2026-11-01T09:00")])
    p = air.Programme(lib)
    played = [p.next() for _ in range(4)]
    assert [k for k, _ in played] == ["short", "short", "short", "update"]
    assert [s["video_id"] for _, s in played[:3]] == ["b", "a", "b"]
    lib.items = [short("c", "2026-11-01T12:00")] + lib.items
    assert p.next()[1]["video_id"] == "c"


def test_programme_skips_bad_and_falls_back_to_updates():
    lib = FakeLibrary([short("a", "2026-11-01T09:00")])
    p = air.Programme(lib)
    assert p.next()[0] == "short"
    for _ in range(air.BENCH_AFTER):
        p.failed("a")
    p.since_update = 0
    assert p.next() == ("update", None)
    assert air.Programme(FakeLibrary([])).next() == ("update", None)


def test_redact_hides_key():
    assert air.redact("rtmp://a.rtmp.youtube.com/live2/abcd-1234: I/O error", "abcd-1234").endswith("<key>: I/O error")
    assert air.redact("fine", None) == "fine"


def test_parse_rss_and_atom():
    rss = b"""<rss><channel><item><title>One &amp; two</title><link>https://x/1</link>
      <pubDate>Sun, 01 Nov 2026 14:00:00 GMT</pubDate></item></channel></rss>"""
    atom = b"""<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Atom story</title>
      <updated>2026-11-01T13:00:00Z</updated></entry></feed>"""
    r = sources.parse_feed(rss, "BBC")
    a = sources.parse_feed(atom, "DW")
    assert r[0]["title"] == "One & two" and r[0]["outlet"] == "BBC" and r[0]["at"].startswith("2026-11-01T14:00")
    assert a[0]["title"] == "Atom story"
    mixed = sources.interleave([r * 2, a], 5)
    assert [m["title"] for m in mixed] == ["One & two", "Atom story"]


def test_quakes_last_day_by_magnitude():
    now = datetime.now(timezone.utc).timestamp() * 1000
    geo = {"coordinates": [10.0, 20.0, 5.0]}
    data = {"features": [
        {"properties": {"mag": 4.1, "place": "A", "time": now}, "geometry": geo},
        {"properties": {"mag": 6.2, "place": "B", "time": now}, "geometry": geo},
        {"properties": {"mag": 7.0, "place": "old", "time": now - 3 * 86400e3}, "geometry": geo}]}
    rows = sources.quakes_from(json.dumps(data).encode())
    assert [q["place"] for q in rows] == ["B", "A"]


def test_librarian_takes_only_public_shorts_of_the_current_identity(monkeypatch):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "librarian"))
    import collect

    rows = [{"id": "s1", "video_id": "v1", "title": "t", "published_at": "2026-10-30T00:00:00+00:00"},
            {"id": "s0", "video_id": "v0", "title": "old", "published_at": "2026-10-20T00:00:00+00:00"},
            {"id": "s2", "url": "https://www.youtube.com/shorts/v2", "title": "u",
             "published_at": "2026-10-31T00:00:00+00:00"},
            {"id": "s3", "video_id": "v3", "privacy": "private", "published_at": "2026-10-31T00:00:00+00:00"}]

    class Resp:
        def read(self):
            return json.dumps(rows).encode()

    monkeypatch.setattr(collect.urllib.request, "urlopen", lambda *a, **k: Resp())
    got = collect.published("x/y", "2026-10-29T00:00:00+00:00")
    assert {k: v["video_id"] for k, v in got.items()} == {"s1": "v1", "s2": "v2"}


def test_library_keeps_playing_through_bad_manifests(tmp_path):
    ch = CHANNELS["orbitwire"]
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"video")
    manifest = tmp_path / "library.json"
    row = {"channel": "orbitwire", "video_id": "v1", "title": "t", "published_at": "2026-11-01", "url": clip.as_uri()}
    manifest.write_text(json.dumps({"shorts": [row]}))
    lib = sources.Library(ch, tmp_path / "home", manifest.as_uri())
    lib.sync(0)
    assert [m["video_id"] for m in lib.items] == ["v1"] and Path(lib.items[0]["path"]).read_bytes() == b"video"
    for broken in ("{not json", json.dumps({"shorts": []}), json.dumps({"other": 1})):
        manifest.write_text(broken)
        lib.sync(0)
        assert [m["video_id"] for m in lib.items] == ["v1"]
    manifest.write_text(json.dumps({"shorts": [row | {"channel": "palpal"}, row | {"video_id": "v2"}]}))
    lib.sync(0)
    assert [m["video_id"] for m in lib.items] == ["v2"] and not (tmp_path / "home/library/v1.mp4").exists()
    assert [m["video_id"] for m in sources.Library(ch, tmp_path / "home", manifest.as_uri()).items] == ["v2"]


def test_corrupt_caches_start_empty(tmp_path):
    ch = CHANNELS["palpal"]
    (tmp_path / "library").mkdir()
    (tmp_path / "sources.json").write_text('{"headlines": [')
    (tmp_path / "library/index.json").write_text("[{")
    assert sources.Desk(ch, tmp_path).headlines == [] and sources.Library(ch, tmp_path).items == []


@pytest.mark.parametrize("key", sorted(CHANNELS))
def test_frames_render(key):
    ch = CHANNELS[key]
    desk = type("D", (), {"headlines": [{"title": "हेडलाइन one two three " * 4, "outlet": "BBC",
                                         "at": "2026-11-01T14:00:00+00:00"}] * 8,
                          "quakes": [{"mag": 6.1, "place": "Somewhere far", "at": "2026-11-01T12:00:00+00:00"}]})()
    s = short("a", "2026-11-01T11:27:34+00:00")
    for page in segments.short_pages(ch, 40):
        img = look.short_frame(ch, s, page, desk, AT, short("b", "2026-11-01T12:00"))
        assert img.size == (look.W, look.H) and img.getpixel((look.W // 2, look.H // 2))[3] == 0
    for page in segments.update_pages(ch, desk):
        assert look.update_page(ch, page, desk, AT, s).size == (look.W, look.H)
    strip, (label, label_w) = look.ticker_strip(ch, desk), look.ticker_label(ch)
    assert strip.height == label.height == look.TICKER_H and strip.width > look.W and 60 < label_w < 300
    assert look.SHORT_W * 16 == look.SHORT_H * 9 and look.SHORT_W % 2 == 0
