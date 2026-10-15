"""B站个人空间: 数据获取的退路和卡片模板输入"""

from __future__ import annotations

import asyncio
import importlib
import re
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from stubs import install_stubs

ROOT = Path(__file__).resolve().parents[1]

CARD_PAYLOAD = {
    "code": 0,
    "data": {
        "card": {
            "mid": "546195",
            "name": "老番茄",
            "face": "http://i0.hdslb.com/bfs/face/abc.jpg",
            "sign": "我的微博：@_老番茄_ ",
            "sex": "男",
            "attention": 6,
            "level_info": {"current_level": 6},
            "Official": {"role": 1, "title": "2025百大UP主"},
            "vip": {"status": 1, "label": {"text": "年度大会员"}},
        },
        "follower": 20842649,
        "archive_count": 681,
        "like_num": 196074827,
        "space": {"l_img": "https://i0.hdslb.com/bfs/space/top.png"},
    },
}

VIDEOS_PAYLOAD = {
    "list": {
        "vlist": [
            {
                "bvid": f"BV{i}",
                "title": f"视频{i}",
                "pic": f"http://i0.hdslb.com/bfs/archive/{i}.jpg",
                "length": "03:21",
                "play": play,
                "created": 1784505602,
            }
            for i, play in enumerate([123456, "--", 8, 9])
        ]
    },
    "page": {"count": 681},
}


@pytest.fixture
def space(monkeypatch: pytest.MonkeyPatch):
    install_stubs(monkeypatch)
    bilibili_pkg = types.ModuleType("core.parsers.bilibili")
    bilibili_pkg.__path__ = [str(ROOT / "core" / "parsers" / "bilibili")]
    monkeypatch.setitem(sys.modules, "core.parsers.bilibili", bilibili_pkg)
    for name in (
        "core.html_render",
        "core.parsers.bilibili.common",
        "core.parsers.bilibili.space",
    ):
        monkeypatch.delitem(sys.modules, name, raising=False)
    return importlib.import_module("core.parsers.bilibili.space")


class FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def make_parser(monkeypatch, card_payload, videos):
    """videos 为异常时模拟投稿接口被风控"""

    class FakeUser:
        def __init__(self, mid, credential=None):
            self.mid = mid

        async def get_videos(self, ps):
            if isinstance(videos, Exception):
                raise videos
            return videos

    user_module = types.ModuleType("bilibili_api.user")
    user_module.User = FakeUser
    monkeypatch.setitem(sys.modules, "bilibili_api.user", user_module)

    async def headers():
        return {}, False

    async def get_json(session, url, params, headers):
        return card_payload

    async def credential():
        return None

    return SimpleNamespace(
        web=SimpleNamespace(headers=headers, session=FakeSession, get_json=get_json),
        login=SimpleNamespace(credential=credential()),
    )


def test_fetch_space_keeps_card_when_videos_blocked(space, monkeypatch):
    parser = make_parser(monkeypatch, CARD_PAYLOAD, RuntimeError("412"))

    info = asyncio.run(space.fetch_space(parser, 546195))

    assert info.user.card.name == "老番茄"
    assert info.videos == []
    assert info.url == "https://space.bilibili.com/546195"


def test_fetch_space_limits_recent_videos(space, monkeypatch):
    parser = make_parser(monkeypatch, CARD_PAYLOAD, VIDEOS_PAYLOAD)

    info = asyncio.run(space.fetch_space(parser, 546195))

    assert [v.bvid for v in info.videos] == ["BV0", "BV1", "BV2"]


def test_fetch_space_raises_when_user_missing(space, monkeypatch):
    exception = importlib.import_module("core.exception")
    parser = make_parser(monkeypatch, {"code": -404, "message": "啥都木有"}, {})

    with pytest.raises(exception.ParseException, match="啥都木有"):
        asyncio.run(space.fetch_space(parser, 1))


def test_space_card_context(space, monkeypatch):
    info = asyncio.run(
        space.fetch_space(
            make_parser(monkeypatch, CARD_PAYLOAD, VIDEOS_PAYLOAD), 546195
        )
    )
    renderer = space.BiliSpaceRenderer(None, ZoneInfo("Asia/Shanghai"))

    ctx = renderer._context(info, None, None, [None, None, None])

    assert ctx["name"] == "老番茄"
    assert ctx["vip"] == "年度大会员"
    assert ctx["level"] == 6
    assert ctx["official"] == "2025百大UP主"
    assert ctx["official_org"] is False
    assert ctx["sign"] == "我的微博：@_老番茄_"
    assert ctx["stats"][0] == ("粉丝", "2084.3万")
    assert [v["play"] for v in ctx["videos"]] == ["12.3万", "--", "8"]

    avatar, banner, covers = space.space_image_urls(info)
    assert avatar.endswith("@176w_176h_1c.webp")
    assert banner.endswith("@1440w.webp")
    assert len(covers) == 3


@pytest.mark.parametrize(
    ("url", "mid"),
    [
        ("https://space.bilibili.com/546195", "546195"),
        ("https://space.bilibili.com/546195/upload/video", "546195"),
        ("https://m.bilibili.com/space/3747571004608077?plat_id=1", "3747571004608077"),
        # 收藏夹也挂在空间路径下, 要留给收藏夹规则
        ("https://space.bilibili.com/546195/favlist?fid=789", None),
    ],
)
def test_space_url_patterns(url, mid):
    source = (ROOT / "core" / "parsers" / "bilibili" / "__init__.py").read_text(
        encoding="utf-8"
    )
    patterns = re.findall(r'@handle\("(?:space\.bili|/space/)", r"(.+?)"\)', source)
    assert len(patterns) == 2
    matched = next(
        (m for p in patterns if (m := re.search(p, url))),
        None,
    )
    assert (matched and matched.group("mid")) == mid
