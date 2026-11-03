import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from livedesk import air, look, segments, sources, trending, voice  # noqa: E402
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
    played = [p.next() for _ in range(5)]
    assert [k for k, _ in played] == ["short"] * air.SHORTS_PER_UPDATE + ["update"]
    assert [s["video_id"] for _, s in played[:3]] == ["b", "a", "b"]
    just_now = datetime.now(timezone.utc).isoformat()
    lib.items = [short("c", just_now), short("old", "2026-10-12T12:00")] + lib.items
    assert p.next()[1]["video_id"] == "c"
    assert list(p.order)[-1] == "old"  # the back catalogue joins the end of the rotation


def test_updates_show_the_lead_storys_own_footage(tmp_path):
    clips = []
    for vid, title in (("x", "Cricket final tonight"), ("y", "Magnitude 7 earthquake strikes Japan coast")):
        (tmp_path / f"{vid}.mp4").write_bytes(b"0")
        clips.append({"video_id": vid, "title": title, "path": str(tmp_path / f"{vid}.mp4")})
    desk = type("D", (), {"headlines": [{"title": "Japan coast earthquake magnitude 7 felt in Tokyo"}]})()
    assert segments.footage_for(desk, clips, 0)["video_id"] == "y"
    desk.headlines = [{"title": "Parliament debates budget"}]
    assert [segments.footage_for(desk, clips, t)["video_id"] for t in (0, 1)] == ["x", "y"]
    assert segments.footage_for(desk, [], 0) is None


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


def test_widely_covered_trending_stories_lead():
    now = datetime(2026, 11, 3, 19, 0, tzinfo=timezone.utc)
    at = lambda h: (now.timestamp() - h * 3600)  # noqa: E731
    iso = lambda h: datetime.fromtimestamp(at(h), timezone.utc).isoformat()  # noqa: E731
    feeds = [[{"title": "Local bakery wins regional bread award", "outlet": "A", "at": iso(0.1)},
              {"title": "Earthquake of magnitude 7 strikes off Japan coast", "outlet": "A", "at": iso(0.5)}],
             [{"title": "Powerful magnitude 7 earthquake strikes Japan coast, tsunami warning", "outlet": "B",
               "at": iso(0.4)},
              {"title": "Senate passes budget bill after long debate", "outlet": "B", "at": iso(5)}],
             [{"title": "Japan coast earthquake: magnitude 7 tremor felt in Tokyo", "outlet": "C", "at": iso(0.3)}]]
    top = [{"title": "Senate passes budget bill in late vote", "outlet": "D", "at": iso(4), "top": 1.0}]
    trends = [{"term": "budget bill", "traffic": 200000, "news": []}]
    ranked = trending.rank(feeds, top, trends, 10, "en", now=now.timestamp())
    # Google's lead story with a search spike leads; the fresh quake three outlets carry is next and breaking
    assert [r["coverage"] for r in ranked] == [2, 3, 1]
    assert "Senate" in ranked[0]["title"] and ranked[0]["outlet"] == "B" and not ranked[0]["breaking"]
    assert "earthquake" in ranked[1]["title"].lower() and ranked[1]["breaking"] and ranked[1]["outlet"] in "ABC"
    assert "bakery" in ranked[2]["title"]


def test_google_feeds_parse():
    top = trending.parse_top_stories(b"""<rss><channel>
      <item><title>Big story here - Reuters</title><source url="x">Reuters</source>
        <pubDate>Tue, 03 Nov 2026 18:00:00 GMT</pubDate></item>
      <item><title>Second story - BBC</title><pubDate>Tue, 03 Nov 2026 17:00:00 GMT</pubDate></item>
    </channel></rss>""")
    assert [(r["title"], r["outlet"], r["top"]) for r in top] == [("Big story here", "Reuters", 1.0),
                                                                   ("Second story", "BBC", 0.5)]
    trends = trending.parse_trends("""<rss xmlns:ht="https://trends.google.com/trending/rss"><channel><item>
      <title>man utd vs roma</title><ht:approx_traffic>10,000+</ht:approx_traffic>
      <ht:news_item><ht:news_item_title>Man Utd v Roma live</ht:news_item_title>
        <ht:news_item_source>BBC</ht:news_item_source></ht:news_item>
    </item></channel></rss>""".encode())
    assert trends == [{"term": "man utd vs roma", "traffic": 10000,
                       "news": [{"title": "Man Utd v Roma live", "outlet": "BBC"}]}]
    assert trending.traffic("2M+") == 2_000_000 and trending.traffic("") == 0


def test_bookish_hindi_is_spoken_in_everyday_english():
    assert voice.everyday_hi("भारतीय अंतरिक्ष अनुसंधान संगठन का नया उपग्रह, उच्चतम न्यायालय ने रोकी उड़ान") == \
        "इसरो का नया सैटेलाइट, सुप्रीम कोर्ट ने रोकी फ्लाइट"
    assert voice.everyday_hi("परमाणुओं पर अनुसंधानकर्ता") == "परमाणुओं पर अनुसंधानकर्ता"
    assert voice.everyday_hi("ज्ञानेश कुमार पर सांकेतिक महाभियोग") == "ज्ञानेश कुमार पर सिंबॉलिक इंपीचमेंट"
    desk = type("D", (), {"headlines": [{"title": "मंत्रिमंडल ने विधेयक को दी मंजूरी", "outlet": "आज तक"}] * 6,
                          "quakes": []})()
    ch = CHANNELS["palpal"]
    spoken = voice.script(ch, segments.update_pages(ch, desk), desk, AT, None)[0]
    assert "कैबिनेट ने बिल को दी मंजूरी" in spoken and "मंत्रिमंडल" not in spoken


def test_headlines_read_like_a_newsreader():
    assert voice.clean("LIVE: Japan’s bank holds rates... | markets") == "Japan's bank holds rates"
    assert voice.clean("“पीतल नगरी”... जहां ताकत है बराबर!") == "पीतल नगरी, जहां ताकत है बराबर!"
    assert voice.spoken_place("45 km NE of Hualien City, Taiwan") == "45 kilometres north-east of Hualien City, Taiwan"
    assert voice.spoken_place("Fiji region") == "near Fiji region"
    assert [voice._part_of_day(h) for h in (0, 4, 5, 13, 19, 23)] == \
        ["night", "night", "morning", "afternoon", "evening", "night"]


@pytest.mark.parametrize("key", sorted(CHANNELS))
def test_update_script_has_one_part_per_page(key):
    ch = CHANNELS[key]
    desk = type("D", (), {"headlines": [{"title": f"Story number {i} is long enough!", "outlet": "BBC"}
                                        for i in range(6)],
                          "quakes": [{"mag": 6.1, "place": "10 km S of Lima, Peru"}]})()
    pages = segments.update_pages(ch, desk)
    parts = voice.script(ch, pages, desk, AT, {"title": "Next one"})
    assert len(parts) == len(pages) and all(parts[:2]) and "Next one" in parts[-1]
    assert "Story number 0" in parts[0] and "Story number 2" not in parts[0] and "Story number 3" in parts[1]
    assert not any(m in " ".join(parts) for m in ("!।", "!.", "?।", ".."))
    assert voice.script(ch, pages, desk, AT, None) == voice.script(ch, pages, desk, AT, None)
