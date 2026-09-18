"""B站海报 / 评论区渲染的数据组装测试 (不起浏览器, 只验证模板输入和 HTML 片段)"""

from __future__ import annotations

import asyncio
import importlib
import sys
import types
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from stubs import install_stubs


@pytest.fixture
def bili(monkeypatch: pytest.MonkeyPatch):
    install_stubs(monkeypatch)
    bilibili_pkg = types.ModuleType("core.parsers.bilibili")
    bilibili_pkg.__path__ = [
        str(Path(__file__).resolve().parents[1] / "core" / "parsers" / "bilibili")
    ]
    monkeypatch.setitem(sys.modules, "core.parsers.bilibili", bilibili_pkg)
    for name in (
        "core.html_render",
        "core.parsers.bilibili.common",
        "core.parsers.bilibili.video",
        "core.parsers.bilibili.poster",
        "core.parsers.bilibili.comment_renderer",
        "core.parsers.bilibili.comment_service",
    ):
        monkeypatch.delitem(sys.modules, name, raising=False)
    return SimpleNamespace(
        html_render=importlib.import_module("core.html_render"),
        common=importlib.import_module("core.parsers.bilibili.common"),
        video=importlib.import_module("core.parsers.bilibili.video"),
        poster=importlib.import_module("core.parsers.bilibili.poster"),
        renderer=importlib.import_module("core.parsers.bilibili.comment_renderer"),
        service=importlib.import_module("core.parsers.bilibili.comment_service"),
    )


class FakeHtml:
    def __init__(self):
        self.calls = []

    async def render(self, template, context, out_path, *, selector="#root"):
        self.calls.append((template, context))
        return out_path


def reply(rpid, message, *, mid=1, sub=(), **extra):
    item = {
        "rpid": rpid,
        "mid": mid,
        "like": 12,
        "ctime": 1_700_000_000,
        "rcount": len(sub),
        "content": {"message": message, "pictures": [], "emote": None},
        "member": {
            "uname": f"user-{rpid}",
            "avatar": "//i0.hdslb.com/bfs/face/a.jpg",
            "level_info": {"current_level": 6},
            "vip": {"vipStatus": 0},
        },
        "replies": list(sub),
    }
    item.update(extra)
    return item


def test_fmt_helpers(bili):
    common = bili.common
    assert common.fmt_count(999) == "999"
    assert common.fmt_count(12_345) == "1.2万"
    assert common.fmt_count(100_000) == "10万"
    assert common.fmt_count(105_835_908) == "1.1亿"
    assert common.fmt_duration(213) == "03:33"
    assert common.fmt_duration(3661) == "1:01:01"
    assert common.bfs_thumb("https://i0.hdslb.com/bfs/face/a.jpg", "96w_96h_1c") == (
        "https://i0.hdslb.com/bfs/face/a.jpg@96w_96h_1c.webp"
    )
    # 已带参数或非图床链接原样返回
    assert common.bfs_thumb("https://i0.hdslb.com/bfs/a.jpg@1c.webp", "96w") == (
        "https://i0.hdslb.com/bfs/a.jpg@1c.webp"
    )
    assert (
        common.bfs_thumb("https://example.com/a.jpg", "96w")
        == "https://example.com/a.jpg"
    )


def test_relative_time(bili):
    tz = ZoneInfo("Asia/Shanghai")
    now = datetime.now(tz).timestamp()
    fmt = bili.common.fmt_relative_time
    assert fmt(int(now) - 10, tz) == "刚刚"
    assert fmt(int(now) - 300, tz) == "5分钟前"
    assert fmt(int(now) - 7200, tz) == "2小时前"
    assert fmt(int(now) - 3 * 86400, tz) == "3天前"
    assert (
        fmt(int(datetime(2020, 1, 1, tzinfo=timezone.utc).timestamp()), tz)
        == "2020-01-01"
    )


def test_parse_reply_reads_native_fields(bili):
    service = bili.service.BiliCommentService(SimpleNamespace(), renderer=object())
    item = reply(
        "1",
        "回复 @小明 :好耶[doge]",
        mid=42,
        sub=[reply("2", "楼中楼"), reply("3", "加微信 vx: abc123 领福利")],
        up_action={"like": True},
        reply_control={"location": "IP属地：广东"},
    )
    item["content"]["emote"] = {
        "[doge]": {"url": "//i0.hdslb.com/bfs/emote/doge.png", "meta": {"size": 1}}
    }
    item["content"]["at_name_to_mid"] = {"小明": 7}
    item["content"]["pictures"] = [{"img_src": "http://i0.hdslb.com/bfs/new_dyn/p.jpg"}]
    item["member"]["vip"] = {"vipStatus": 1}

    comment = service._parse_reply(item, upper_mid=42, is_top=True)

    assert comment.is_up and comment.is_top and comment.up_liked and comment.vip
    assert comment.level == 6
    assert comment.location == "IP属地：广东"
    assert comment.emotes == {"[doge]": ("https://i0.hdslb.com/bfs/emote/doge.png", 1)}
    assert comment.has_at
    assert comment.pic_urls == ["http://i0.hdslb.com/bfs/new_dyn/p.jpg"]

    subs = service._parse_sub_replies(item, upper_mid=42)
    # 广告文本的楼中楼被过滤掉
    assert [s.rpid for s in subs] == ["2"]
    assert service._parse_reply(reply("9", ""), upper_mid=0) is None


def test_message_html_renders_emote_and_at(bili, tmp_path):
    emote = tmp_path / "doge.png"
    emote.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 8)
    comment = bili.renderer.BiliComment(
        rpid="1",
        mid=1,
        uname="u",
        message="回复 @小明 :<b>好耶</b>[doge][没有的表情] {{ x }}",
        avatar_url="",
        emotes={"[doge]": ("https://e/doge.png", 2)},
        emote_paths={"[doge]": emote},
        has_at=True,
    )
    renderer = bili.renderer.BiliCommentRenderer(FakeHtml(), ZoneInfo("Asia/Shanghai"))

    html = str(renderer._message_html(comment))

    assert '<span class="at">@小明</span>' in html
    assert '<img class="emote emote-large" src="data:image/png;base64,' in html
    assert "&lt;b&gt;好耶&lt;/b&gt;" in html
    assert "[没有的表情]" in html
    assert "<b>" not in html


def test_template_output_escapes_braces_for_remote_raw_block(bili):
    html = bili.html_render.TEMPLATES.from_string("<p>{{ text }}</p>").render(
        text="{% endraw %}<i>{{ x }}</i>"
    )
    assert "{%" not in html and "{{" not in html
    assert "&#123;% endraw %&#125;&lt;i&gt;" in html


def test_comment_context_and_render(bili, monkeypatch, tmp_path):
    html = FakeHtml()
    renderer = bili.renderer.BiliCommentRenderer(html, ZoneInfo("Asia/Shanghai"))
    sub = bili.renderer.BiliComment(
        rpid="2", mid=2, uname="b", message="楼中楼", avatar_url=""
    )
    root = bili.renderer.BiliComment(
        rpid="1",
        mid=1,
        uname="a",
        message="hi",
        avatar_url="",
        rcount=5,
        replies=[sub],
        like=23456,
    )

    out = asyncio.run(
        renderer.render(
            tmp_path / "c.jpg",
            [root],
            title="t",
            up_name="up",
            total=757,
            cover_path=None,
        )
    )

    assert out == tmp_path / "c.jpg"
    template, context = html.calls[0]
    assert template == "bili_comments.html"
    assert context["total"] == "757"
    first = context["comments"][0]
    assert first["like"] == "2.3万"
    assert first["more"] == 5
    assert first["replies"][0]["uname"] == "b"
    # 真正把模板跑一遍, 确认没有语法问题
    page = bili.html_render.TEMPLATES.get_template(template).render(context)
    assert "共 5 条回复" in page and "楼中楼" in page


def test_poster_context(bili, tmp_path):
    video = bili.video.VideoInfo(
        aid=1,
        bvid="BV1xx411c7XX",
        title="标题",
        desc="-",
        duration=213,
        owner=bili.common.Upper(mid=1, name="UP", face=""),
        stat=bili.video.Stats(
            view=105_835_908,
            danmaku=149_447,
            reply=10,
            favorite=20,
            coin=30,
            share=40,
            like=50,
        ),
        pubdate=1_577_835_803,
        ctime=1_577_835_803,
        pic="https://i0.hdslb.com/bfs/archive/c.jpg",
        pages=[
            bili.video.Page(part="上", ctime=1, duration=100),
            bili.video.Page(part="下", ctime=2, duration=113, first_frame=None),
        ],
        honor_reply={"honor": [{"desc": "热门收录"}, {"desc": ""}]},
    )
    page = video.extract_info_with_page(2)
    assert page.cover == video.pic  # 分 P 没首帧时退回视频封面
    assert video.honors == ["热门收录"]

    html = FakeHtml()
    poster = bili.poster.BiliPosterRenderer(html, tmp_path, ZoneInfo("Asia/Shanghai"))
    out = asyncio.run(
        poster.render(
            video,
            page,
            cover=None,
            avatar=None,
            url="https://b23.tv/x",
            ai_summary="AI总结: 摘要",
        )
    )

    assert out.parent == tmp_path and out.name.startswith("bili_poster_BV1xx411c7XX_")
    template, context = html.calls[0]
    assert template == "bili_video.html"
    assert context["desc"] == ""
    assert context["page_label"] == "P2/2"
    assert context["duration"] == "01:53"
    assert context["stats"][0] == ("play", "播放", "1.1亿")
    assert context["ai_summary"] == "摘要"
    rendered = bili.html_render.TEMPLATES.get_template(template).render(context)
    assert "热门收录" in rendered and "P2/2" in rendered
