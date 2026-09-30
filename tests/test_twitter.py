from __future__ import annotations

import asyncio
import importlib
import sys
import zoneinfo
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from stubs import install_stubs

pytest.importorskip("aiohttp")
pytest.importorskip("yt_dlp")
msgspec = pytest.importorskip("msgspec")


@pytest.fixture
def x(monkeypatch):
    install_stubs(monkeypatch)
    for name in (
        "core.html_render",
        "core.parsers.bilibili",
        "core.parsers.bilibili.common",
        "core.parsers.twitter",
        "core.parsers.twitter.card",
        "core.parsers.twitter.model",
    ):
        monkeypatch.delitem(sys.modules, name, raising=False)
    return SimpleNamespace(
        parser=importlib.import_module("core.parsers.twitter"),
        model=importlib.import_module("core.parsers.twitter.model"),
        card=importlib.import_module("core.parsers.twitter.card"),
        html_render=importlib.import_module("core.html_render"),
        exception=importlib.import_module("core.exception"),
    )


def make_parser(x, *, card=True):
    parser = x.parser.TwitterParser.__new__(x.parser.TwitterParser)
    parser.headers = {}
    parser.cfg = SimpleNamespace(
        proxy=None,
        max_size=50 * 1024 * 1024,
        timezone=zoneinfo.ZoneInfo("Asia/Shanghai"),
    )
    parser.downloader = SimpleNamespace(
        download_img=Mock(side_effect=lambda url, **_: f"img:{url}"),
        download_video=Mock(side_effect=lambda url, **_: f"video:{url}"),
    )
    parser.card = SimpleNamespace(build=Mock(return_value="CARD")) if card else None
    return parser


def tweet(x, **extra):
    data = {
        "url": "https://x.com/jack/status/20",
        "id": "20",
        "text": "hello @bob #tag https://example.com/very/long/path https://t.co/pic",
        "author": {
            "name": "jack",
            "screen_name": "jack",
            "avatar_url": "https://pbs.twimg.com/avatar.jpg",
            "verification": {"verified": True, "type": "individual"},
        },
        "created_timestamp": 1_142_974_214,
        "replies": 18063,
        "retweets": 124664,
        "likes": 308954,
        "bookmarks": 21824,
        "views": None,
        "raw_text": {
            "facets": [
                {
                    "type": "url",
                    "original": "https://t.co/url",
                    "replacement": "https://example.com/very/long/path",
                    "display": "example.com/very/…",
                },
                {"type": "media", "original": "https://t.co/pic"},
            ]
        },
        **extra,
    }
    return msgspec.convert(data, type=x.model.Tweet)


PHOTO = {"type": "photo", "url": "https://pbs.twimg.com/p.jpg?name=orig"}
GIF = {
    "type": "gif",
    "url": "https://video.twimg.com/g.mp4",
    "thumbnail_url": "https://pbs.twimg.com/g.jpg",
}
VIDEO = {
    "type": "video",
    "url": "https://video.twimg.com/1080.mp4",
    "thumbnail_url": "https://pbs.twimg.com/v.jpg",
    "duration": 100.0,
    "variants": [
        {
            "url": "https://video.twimg.com/v.m3u8",
            "content_type": "application/x-mpegURL",
        },
        {
            "url": "https://video.twimg.com/360.mp4",
            "bitrate": 832_000,
            "content_type": "video/mp4",
        },
        {
            "url": "https://video.twimg.com/720.mp4",
            "bitrate": 2_176_000,
            "content_type": "video/mp4",
        },
        {
            "url": "https://video.twimg.com/1080.mp4",
            "bitrate": 10_368_000,
            "content_type": "video/mp4",
        },
    ],
}


@pytest.mark.parametrize(
    ("text", "keyword", "tweet_id"),
    [
        ("https://x.com/jack/status/20", "x.com", "20"),
        (
            "看看 https://www.x.com/a/status/1790000000000000000/photo/1",
            "x.com",
            "1790000000000000000",
        ),
        ("https://x.com/i/web/status/20", "x.com", "20"),
        ("https://mobile.twitter.com/jack/status/20?s=20", "twitter.com", "20"),
    ],
)
def test_match_links(x, text, keyword, tweet_id):
    matched_keyword, searched = x.parser.TwitterParser.search_url(text)
    assert (matched_keyword, searched["id"]) == (keyword, tweet_id)


def test_model_strips_media_link_and_maps_badges(x):
    t = tweet(x)
    assert t.clean_text == "hello @bob #tag https://example.com/very/long/path"
    assert t.author.badge == "blue"
    for kind, badge in (("business", "gold"), ("government", "gray")):
        t.author.verification.type = kind
        assert t.author.badge == badge
    t.author.verification.verified = False
    assert t.author.badge is None


@pytest.mark.parametrize(
    ("max_mb", "expected"),
    [(200, "1080.mp4"), (50, "720.mp4"), (20, "360.mp4"), (1, "1080.mp4")],
)
def test_video_picks_largest_variant_under_limit(x, max_mb, expected):
    media = msgspec.convert(VIDEO, type=x.model.Media)
    assert media.video_url(max_mb * 1024 * 1024).endswith(expected)


def test_card_first_then_media_sharing_download_tasks(x):
    parser = make_parser(x)
    data = msgspec.to_builtins(tweet(x))
    data["media"] = {"all": [PHOTO, VIDEO]}
    data["quote"] = {**msgspec.to_builtins(tweet(x)), "media": {"all": [GIF]}}
    result = parser._build_result(msgspec.convert(data, type=x.model.Tweet))

    card_group, media_group = result.send_groups
    assert card_group.contents == ["CARD"]
    assert (card_group.force_merge, card_group.render_card) == (False, False)
    assert media_group.contents == result.contents
    assert media_group.render_card is False
    photo, video, gif = result.contents
    assert video.path_task == "video:https://video.twimg.com/720.mp4"
    assert gif.path_task == "video:https://video.twimg.com/g.mp4"

    _, avatar, previews = parser.card.build.call_args.args
    assert avatar == "img:https://pbs.twimg.com/avatar.jpg"
    # 卡片复用正文图片和视频封面的下载任务, GIF 没有现成封面交给卡片自己下
    assert previews == {
        PHOTO["url"]: photo.path_task,
        VIDEO["url"]: video.cover,
        GIF["url"]: None,
    }
    assert result.text == "hello @bob #tag https://example.com/very/long/path"


def test_text_only_tweet_sends_card_alone(x):
    result = make_parser(x)._build_result(tweet(x))
    assert [g.contents for g in result.send_groups] == [["CARD"]]
    assert result.contents == []


def test_card_switch_off_keeps_default_flow(x):
    data = {**msgspec.to_builtins(tweet(x)), "media": {"all": [PHOTO]}}
    result = make_parser(x, card=False)._build_result(
        msgspec.convert(data, type=x.model.Tweet)
    )
    assert result.send_groups == []
    assert len(result.contents) == 1
    assert result.author.name == "jack"


def test_api_error_is_readable(x):
    class Resp:
        status = 404

        async def read(self):
            return b'{"code":404,"message":"NOT_FOUND","tweet":null}'

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

    parser = make_parser(x)
    parser._session = SimpleNamespace(closed=False, get=Mock(return_value=Resp()))
    _, searched = x.parser.TwitterParser.search_url("https://x.com/a/status/404")
    with pytest.raises(x.exception.ParseException, match="不存在"):
        asyncio.run(parser._parse(searched))
    assert parser._session.get.call_args.args[0].endswith("/status/404")


def test_text_html_highlights_entities_and_escapes(x):
    data = msgspec.to_builtins(tweet(x))
    data["text"] = "<b>hi</b> @bob #话题 a#b https://example.com/very/long/path"
    html = x.card.TweetCardRenderer._text_html(
        msgspec.convert(data, type=x.model.Tweet)
    )
    assert "&lt;b&gt;hi&lt;/b&gt;" in html
    assert '<span class="link">@bob</span>' in html
    assert '<span class="link">#话题</span>' in html
    assert "a#b" in html
    assert '<span class="link">example.com/very/…</span>' in html


def test_tweet_context_formats_time_and_counts(x):
    renderer = x.card.TweetCardRenderer(make_parser(x))
    data = {
        **msgspec.to_builtins(tweet(x)),
        "views": 5_073_427,
        "media": {"all": [VIDEO]},
    }
    ctx = renderer._tweet_context(
        msgspec.convert(data, type=x.model.Tweet), None, {VIDEO["url"]: "data:v"}
    )
    assert ctx["time"] == "上午4:50 · 2006年3月22日"
    assert ctx["views"] == "507.3万"
    assert ctx["stats"][2] == ("like", "30.9万")
    assert ctx["media"] == [{"type": "video", "src": "data:v", "duration": "1:40"}]


def test_template_renders_tweet_with_quote(x):
    renderer = x.card.TweetCardRenderer(make_parser(x))
    data = {**msgspec.to_builtins(tweet(x)), "media": {"all": [PHOTO, GIF]}}
    t = msgspec.convert(data, type=x.model.Tweet)
    context = {
        "width": renderer.WIDTH,
        "tweet": renderer._tweet_context(t, None, {}),
        "quote": renderer._tweet_context(tweet(x), "data:a", {}),
    }
    html = x.html_render.TEMPLATES.get_template(renderer.TEMPLATE).render(context)
    assert 'class="media n2"' in html and ">GIF<" in html
    assert 'class="quote"' in html and "@jack · 2006年3月22日" in html
    assert html.count('href="#x-verified"') == 2
