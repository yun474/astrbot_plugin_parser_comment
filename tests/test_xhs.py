from __future__ import annotations

import asyncio
import importlib
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from stubs import install_stubs

pytest.importorskip("aiohttp")
msgspec = pytest.importorskip("msgspec")

SHARE_TEXT = (
    "86 【典中典 - yly | 小红书 - 你的生活兴趣社区】 😆 TUtu38yiWucZBfr 😆 "
    "https://www.xiaohongshu.com/discovery/item/6a94f90e00000000070068fb"
    "?source=webshare&xhsshare=pc_web&xsec_token=ABaaek-koj5c=&xsec_source=pc_share"
)


@pytest.fixture
def xhs(monkeypatch):
    install_stubs(monkeypatch)
    for name in (
        "core.html_render",
        "core.parsers.xhs",
        "core.parsers.xhs.card",
        "core.parsers.xhs.model",
    ):
        monkeypatch.delitem(sys.modules, name, raising=False)
    return SimpleNamespace(
        parser=importlib.import_module("core.parsers.xhs"),
        model=importlib.import_module("core.parsers.xhs.model"),
        card=importlib.import_module("core.parsers.xhs.card"),
    )


def make_parser(xhs, *, comment_enable=True):
    parser = xhs.parser.XHSParser.__new__(xhs.parser.XHSParser)
    parser.headers = {}
    parser.ios_headers = {}
    parser.cfg = SimpleNamespace(proxy=None)
    parser.downloader = SimpleNamespace(
        download_img=Mock(side_effect=lambda url, **_: f"img:{url}"),
        download_video=Mock(side_effect=lambda url, **_: f"video:{url}"),
    )
    parser.card = SimpleNamespace(build=Mock(return_value="CARD"))
    parser.comment_enable = comment_enable
    return parser


def page(xhs, *, video=False, comments=1):
    note = {
        "noteId": "n1",
        "type": "video" if video else "normal",
        "title": "标题",
        "desc": "正文",
        "time": 1_700_000_000_000,
        "user": {"nickName": "作者", "avatar": "https://a/avatar.jpg"},
        "imageList": [{"fileId": "f1"}, {"fileId": "f2"}],
    }
    if video:
        note["video"] = {
            "media": {
                "video": {"duration": 81},
                "stream": {
                    "h264": [{"masterUrl": "https://v/264.mp4"}],
                    "h265": [
                        {
                            "masterUrl": "https://v/265.mp4",
                            "backupUrls": ["https://b/265.mp4"],
                        }
                    ],
                },
            }
        }
    data = {
        "noteData": note,
        "commentData": {
            "commentCount": 3,
            "comments": [{"id": str(i), "content": "评论"} for i in range(comments)],
        },
    }
    return msgspec.convert(data, type=xhs.model.NotePage)


@pytest.mark.parametrize(
    ("text", "keyword"),
    [
        (SHARE_TEXT, "xiaohongshu.com"),
        (
            "https://www.xiaohongshu.com/explore/6a94f90e00000000070068fb",
            "xiaohongshu.com",
        ),
        ("复制打开 http://xhslink.com/o/8AbCdEf 看看", "xhslink.com"),
        ("http://xhslink.cn/m/8AbCdEf", "xhslink.cn"),
    ],
)
def test_match_share_links(xhs, text, keyword):
    matched_keyword, searched = xhs.parser.XHSParser.search_url(text)
    assert matched_keyword == keyword
    if keyword == "xiaohongshu.com":
        assert searched.group("xhs_id") == "6a94f90e00000000070068fb"


def test_short_link_follows_encoded_redirect_path(xhs):
    parser = make_parser(xhs)
    parser.get_redirect_url = AsyncMock(
        return_value="https://www.xiaohongshu.com/404/sec_x?redirectPath="
        "https%3A%2F%2Fwww.xiaohongshu.com%2Fexplore%2Fabc123%3Fxsec_token%3Dtok%253D"
    )
    parser.parse = AsyncMock(return_value="RESULT")
    _, searched = xhs.parser.XHSParser.search_url("http://xhslink.com/o/8AbCdEf")

    assert asyncio.run(parser._parse_short_link(searched)) == "RESULT"
    keyword, matched = parser.parse.await_args.args
    assert keyword == "xiaohongshu.com"
    assert matched.group("xhs_id", "query") == ("abc123", "xsec_token=tok%3D")


def test_initial_state_only_replaces_undefined_values(xhs):
    parser = make_parser(xhs)
    html = (
        '<script>window.__INITIAL_STATE__={"a":undefined,"b":[undefined,1],'
        '"desc":"undefined behavior"}</script>'
    )
    assert parser._extract_initial_state(html) == {
        "a": None,
        "b": [None, 1],
        "desc": "undefined behavior",
    }


def test_model_prefers_watermark_free_media(xhs):
    note = page(xhs, video=True).noteData
    assert note.image_urls[0] == (
        "https://sns-na-i1.xhscdn.com/f1?imageView2/2/format/jpg"
    )
    assert note.video.urls == ["https://v/265.mp4", "https://b/265.mp4"]
    assert note.video.duration == 81


def test_gallery_sends_note_card_then_images(xhs):
    parser = make_parser(xhs)
    result = parser._build_result(page(xhs))

    card_group, image_group = result.send_groups
    assert card_group.contents == ["CARD"]
    assert (card_group.force_merge, card_group.render_card) == (False, False)
    assert image_group.contents == result.contents and len(result.contents) == 2
    # 卡片复用图集首图的下载任务
    kwargs = parser.card.build.call_args.kwargs
    assert kwargs["show_note"] is True
    assert kwargs["cover"] is result.contents[0].path_task
    assert parser.downloader.download_img.call_count == 3  # 两张图 + 头像


@pytest.mark.parametrize(("comments", "groups"), [(2, 2), (0, 1)])
def test_video_sends_comments_separately(xhs, comments, groups):
    parser = make_parser(xhs)
    result = parser._build_result(page(xhs, video=True, comments=comments))

    assert len(result.send_groups) == groups
    assert result.send_groups[0].contents == result.contents
    assert result.contents[0].duration == 81
    if comments:
        assert result.send_groups[1].contents == ["CARD"]
        assert result.send_groups[1].force_merge is True
        assert parser.card.build.call_args.kwargs["show_note"] is False
    else:
        parser.card.build.assert_not_called()


def test_comment_switch_keeps_note_card(xhs):
    parser = make_parser(xhs, comment_enable=False)
    parser._build_result(page(xhs, comments=3))
    assert parser.card.build.call_args.args[1] == []

    parser.card.build.reset_mock()
    parser._build_result(page(xhs, video=True, comments=3))
    parser.card.build.assert_not_called()


def test_desc_highlights_topics_and_escapes(xhs):
    html = xhs.card.XHSCardRenderer._desc_html("<b>hi</b>\n#拱出去[话题]# #猪猪[话题]#")
    assert "&lt;b&gt;hi&lt;/b&gt;" in html
    assert '<span class="topic">#拱出去</span>' in html
    assert "[话题]" not in html
