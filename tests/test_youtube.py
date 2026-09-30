from __future__ import annotations

import importlib
import sys
from types import SimpleNamespace
from unittest.mock import Mock, sentinel

import pytest
from stubs import install_stubs

pytest.importorskip("aiohttp")
pytest.importorskip("yt_dlp")
msgspec = pytest.importorskip("msgspec")


@pytest.fixture
def yt(monkeypatch):
    install_stubs(monkeypatch)
    for name in (
        "core.html_render",
        "core.parsers.bilibili",
        "core.parsers.bilibili.common",
        "core.parsers.youtube",
        "core.parsers.youtube.card",
    ):
        monkeypatch.delitem(sys.modules, name, raising=False)
    return SimpleNamespace(
        parser=importlib.import_module("core.parsers.youtube"),
        card=importlib.import_module("core.parsers.youtube.card"),
        html_render=importlib.import_module("core.html_render"),
        data=importlib.import_module("core.data"),
        download=importlib.import_module("core.download"),
    )


def make_parser(yt, *, card=True):
    parser = yt.parser.YouTubeParser.__new__(yt.parser.YouTubeParser)
    parser.headers = {}
    parser.cfg = SimpleNamespace(proxy=None, max_duration=600)
    parser.downloader = SimpleNamespace(
        download_img=Mock(side_effect=lambda url, **_: f"img:{url}")
    )
    parser.card = SimpleNamespace(build=Mock(return_value="CARD")) if card else None
    return parser


def info(yt, **extra):
    raw = {
        "title": "Never Gonna Give You Up",
        "channel": "Rick Astley",
        "uploader": "Rick Astley",
        "duration": 213,
        "thumbnail": "https://i.ytimg.com/maxres.webp",
        "description": " 简介 \n",
        "channel_id": "UC1",
        # 老旧 UA 拿到的残缺数据里 timestamp 为 null, 不能让整个解析失败
        "timestamp": None,
        "upload_date": "20091025",
        "uploader_id": "@RickAstleyYT",
        "channel_is_verified": True,
        "channel_follower_count": 4_550_000,
        "view_count": 1_821_405_860,
        "like_count": 19_437_806,
        "comment_count": 2_400_000,
        "webpage_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "formats": [],
        **extra,
    }
    return msgspec.convert(raw, type=yt.download.VideoInfo)


@pytest.mark.parametrize(
    ("text", "keyword"),
    [
        ("ymhttps://youtu.be/dQw4w9WgXcQ", "ymhttps://"),
        ("ymhttps://www.youtube.com/watch?v=dQw4w9WgXcQ", "ymhttps://"),
        ("https://youtu.be/dQw4w9WgXcQ?si=abc", "youtu"),
        ("https://www.youtube.com/shorts/qOUPrKb0aYY", "youtube"),
    ],
)
def test_audio_prefix_is_not_swallowed_by_video_patterns(yt, text, keyword):
    assert yt.parser.YouTubeParser.search_url(text)[0] == keyword


def test_video_card_reuses_cover_task(yt):
    parser = make_parser(yt)
    video = parser.create_video_content(
        sentinel.video, "https://i.ytimg.com/maxres.webp", 213
    )
    author = yt.data.Author(name="Rick Astley", avatar="AVATAR")
    result = parser._build_result(info(yt), author, [video])

    card_group, media_group = result.send_groups
    assert card_group.contents == ["CARD"]
    assert (card_group.force_merge, card_group.render_card) == (False, False)
    assert media_group.contents == [video] and media_group.render_card is False
    assert parser.card.build.call_args.args[1:] == ("AVATAR", video.cover)
    assert parser.downloader.download_img.call_count == 1
    assert result.timestamp is None
    assert result.url == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


def test_too_long_video_sends_card_only(yt):
    parser = make_parser(yt)
    result = parser._build_result(info(yt), yt.data.Author(name="Rick"), [])
    assert [g.contents for g in result.send_groups] == [["CARD"]]
    assert parser.card.build.call_args.args[2] == "img:https://i.ytimg.com/maxres.webp"


def test_card_switch_off_sends_cover_with_audio(yt):
    parser = make_parser(yt, card=False)
    audio = parser.create_audio_content(sentinel.audio, duration=213)
    result = parser._build_result(info(yt), yt.data.Author(name="Rick"), [audio])
    assert result.send_groups == []
    cover, sent_audio = result.contents
    assert cover.path_task == "img:https://i.ytimg.com/maxres.webp"
    assert sent_audio is audio


def test_card_context(yt):
    renderer = yt.card.YouTubeCardRenderer(make_parser(yt))
    ctx = renderer._context(info(yt), None, None)
    assert ctx["duration"] == "3:33"
    assert ctx["subscribers"] == "455万位订阅者"
    assert (ctx["likes"], ctx["comments"]) == ("1943.8万", "240万")
    assert ctx["views"] == "18.2亿次观看"
    assert ctx["date"] == "2009年10月25日"
    assert ctx["desc"] == "简介"
    assert ctx["url"] == "youtube.com/watch?v=dQw4w9WgXcQ"
    assert ctx["verified"] is True and ctx["shorts"] is False

    shorts = renderer._context(
        info(
            yt,
            webpage_url="https://www.youtube.com/shorts/abc",
            channel_follower_count=None,
            upload_date=None,
        ),
        None,
        None,
    )
    assert shorts["shorts"] is True
    assert shorts["subscribers"] == "@RickAstleyYT"
    assert shorts["date"] == ""


def test_template_renders_video_card(yt):
    renderer = yt.card.YouTubeCardRenderer(make_parser(yt))
    context = renderer._context(
        info(yt, webpage_url="https://www.youtube.com/shorts/abc"), None, None
    )
    html = yt.html_render.TEMPLATES.get_template(renderer.TEMPLATE).render(context)
    assert 'class="shorts"' in html and ">3:33<" in html
    assert "455万位订阅者" in html and "18.2亿次观看" in html
    assert 'class="placeholder"' in html and 'class="avatar-fallback">R<' in html
