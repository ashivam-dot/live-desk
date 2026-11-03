"""The two live channels: who they are, what they read, how they look."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

ASSETS = Path(__file__).parent / "assets"
FONTS = ASSETS / "fonts"


@dataclass(frozen=True)
class Channel:
    key: str
    name: str
    channel_id: str
    handle: str
    lang: str
    logo: Path
    # (UTC offset label, IANA zone) shown as clocks
    clocks: tuple[tuple[str, str], ...]
    # (outlet, RSS feed) read for the headline pages; outlets the channel's own desk already trusts
    feeds: tuple[tuple[str, str], ...]
    quakes: bool
    bg: tuple[int, int, int]
    panel: tuple[int, int, int, int]
    accent: tuple[int, int, int]
    hot: tuple[int, int, int]
    text: tuple[int, int, int]
    head_font: Path
    body_font: Path
    ticker_band: tuple[int, int, int]
    ticker_ink: tuple[int, int, int]
    words: dict = field(default_factory=dict)
    # importance signals: Google News top-story feeds (in their ranked order) and Google Trends daily searches
    top_stories: tuple[str, ...] = ()
    trends: tuple[str, ...] = ()
    # the channel's own Shorts from before this date belong to an earlier identity and never air
    since: str = ""


ORBITWIRE = Channel(
    key="orbitwire", name="Orbitwire", channel_id="UCfqAy1IxE2Cwu9pmZGMambA", handle="@orbitwirenews", lang="en",
    logo=ASSETS / "orbitwire.png",
    clocks=(("LONDON", "Europe/London"), ("NEW YORK", "America/New_York"), ("DELHI", "Asia/Kolkata"),
            ("TOKYO", "Asia/Tokyo")),
    feeds=(("BBC", "https://feeds.bbci.co.uk/news/world/rss.xml"),
           ("Al Jazeera", "https://www.aljazeera.com/xml/rss/all.xml"),
           ("The Guardian", "https://www.theguardian.com/world/rss"),
           ("DW", "https://rss.dw.com/rdf/rss-en-all"),
           ("NPR", "https://feeds.npr.org/1004/rss.xml"),
           ("UN News", "https://news.un.org/feed/subscribe/en/news/all/rss.xml"),
           ("France 24", "https://www.france24.com/en/rss")),
    quakes=True,
    bg=(8, 18, 34), panel=(10, 24, 46, 222), accent=(79, 195, 247), hot=(255, 90, 78), text=(240, 246, 252),
    head_font=FONTS / "Anton-Regular.ttf", body_font=FONTS / "Montserrat.ttf",
    ticker_band=(240, 246, 252), ticker_ink=(8, 18, 34),
    words={"live": "LIVE", "ticker": "LATEST", "now": "NOW PLAYING", "headlines": "WORLD HEADLINES", "quakes": "EARTHQUAKES · LAST 24 H",
           "update": "LIVE UPDATE", "follow": "Follow Orbitwire for the world's biggest stories",
           "full": "Full story on the channel", "published": "Published", "clocks": "WORLD TIME",
           "source": "Headlines: {outlets}. Quakes: USGS.", "next": "Coming up", "subscribe": "SUBSCRIBE"},
    top_stories=("https://news.google.com/rss/headlines/section/topic/WORLD?hl=en-US&gl=US&ceid=US:en",
                 "https://news.google.com/rss/headlines/section/topic/WORLD?hl=en-GB&gl=GB&ceid=GB:en",
                 "https://news.google.com/rss?hl=en-US&gl=US&ceid=US:en"),
    trends=("https://trends.google.com/trending/rss?geo=US", "https://trends.google.com/trending/rss?geo=GB",
            "https://trends.google.com/trending/rss?geo=IN"),
    since="2026-10-09T00:00:00+00:00",
)

PALPAL = Channel(
    key="palpal", name="पल पल न्यूज़", channel_id="UCS-lEPHkz_dftfUjjeTuxOA", handle="@palpalnewshindi", lang="hi",
    logo=ASSETS / "palpal.png",
    clocks=(("भारत", "Asia/Kolkata"),),
    feeds=(("BBC हिंदी", "https://feeds.bbci.co.uk/hindi/rss.xml"),
           ("आज तक", "https://www.aajtak.in/rssfeeds/?id=home"),
           ("अमर उजाला", "https://www.amarujala.com/rss/breaking-news.xml"),
           ("एबीपी", "https://www.abplive.com/home/feed"),
           ("एनडीटीवी", "https://feeds.feedburner.com/ndtvkhabar-latest"),
           ("लाइव हिंदुस्तान", "https://api.livehindustan.com/feeds/rss/national/rssfeed.xml")),
    quakes=False,
    bg=(120, 8, 10), panel=(24, 6, 8, 225), accent=(255, 196, 0), hot=(225, 20, 20), text=(255, 255, 255),
    head_font=FONTS / "Mukta-ExtraBold.ttf", body_font=FONTS / "Mukta-SemiBold.ttf",
    ticker_band=(255, 196, 0), ticker_ink=(40, 4, 6),
    words={"live": "लाइव", "ticker": "ब्रेकिंग", "now": "अभी देखिए", "headlines": "ताज़ा खबरें", "update": "पल पल अपडेट",
           "follow": "हर बड़ी खबर सबसे पहले, पल पल न्यूज़ पर", "full": "पूरी खबर चैनल पर",
           "published": "प्रकाशित", "clocks": "भारत समय", "source": "स्रोत: {outlets}", "next": "आगे",
           "subscribe": "सब्सक्राइब करें", "zone": ""},
    top_stories=("https://news.google.com/rss?hl=hi&gl=IN&ceid=IN:hi",
                 "https://news.google.com/rss/headlines/section/topic/NATION?hl=hi&gl=IN&ceid=IN:hi"),
    trends=("https://trends.google.com/trending/rss?geo=IN",),
)

CHANNELS = {c.key: c for c in (ORBITWIRE, PALPAL)}
