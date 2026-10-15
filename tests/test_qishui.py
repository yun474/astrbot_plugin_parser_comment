"""汽水音乐分享页解析"""

from __future__ import annotations

import asyncio
import importlib
import json
import sys
from types import SimpleNamespace

import pytest
from stubs import install_stubs


@pytest.fixture
def qishui(monkeypatch: pytest.MonkeyPatch):
    install_stubs(monkeypatch)
    monkeypatch.delitem(sys.modules, "core.parsers.douyin.qishui", raising=False)
    return importlib.import_module("core.parsers.douyin.qishui")


def make_audio(**overrides) -> dict:
    audio = {
        "status_code": 0,
        "hasCopyright": True,
        "url": "https://cdn.example/audio/?sign=abc&mime_type=audio_mp4",
        "encrypt": False,
        "duration": 206,
        "previewStart": 0,
        "previewEnd": 206,
        "trackName": "可惜火影的影",
        "artistName": "苗天天",
        "coverURL": "https://img.example/cover~c5_375x375.jpg",
        "trackInfo": {
            "name": "可惜火影的影",
            "duration": 206000,
            "album": {
                "name": "可惜火影的影",
                "release_date": 1784505602,
                "url_cover": {"uri": "tos/cover", "urls": ["https://img.example/"]},
            },
            "artists": [
                {
                    "name": "苗天天",
                    "url_avatar": {
                        "uri": "tos/avatar",
                        "urls": ["https://img.example/"],
                    },
                }
            ],
            "stats": {
                "count_collected": 59050,
                "count_comment": 10879,
                "count_shared": 93303,
            },
        },
    }
    audio.update(overrides)
    return audio


def make_html(audio: dict | None) -> str:
    data = {"loaderData": {"track_layout": None, "track_page": {"track_id": "1"}}}
    if audio is not None:
        data["loaderData"]["track_page"]["audioWithLyricsOption"] = audio
    # 真实页面里 JSON 后面紧跟着别的脚本, 不能整段当 JSON 解
    return (
        '<script data-script-src="modern-inline">_ROUTER_DATA = '
        f"{json.dumps(data, ensure_ascii=False)};_ROUTER_DATA.s = function s(r,e){{}}"
        "</script>"
    )


def test_extract_full_track(qishui):
    audio = qishui.extract_audio_option(make_html(make_audio()))

    assert audio.track_info.name == "可惜火影的影"
    assert audio.playable_seconds == 206
    assert not audio.is_preview
    assert audio.track_info.album.url_cover.sized(720) == (
        "https://img.example/tos/cover~c5_720x720.jpg"
    )


def test_extract_preview_track(qishui):
    audio = qishui.extract_audio_option(
        make_html(make_audio(previewStart=40.8, previewEnd=70.8))
    )

    assert audio.is_preview
    assert audio.playable_seconds == pytest.approx(30)


@pytest.mark.parametrize(
    ("audio", "message"),
    [
        (None, "不存在或已下架"),
        (make_audio(status_code=1), "不存在或已下架"),
        (make_audio(hasCopyright=False), "暂无版权"),
        (make_audio(encrypt=True), "已加密"),
        (make_audio(url=""), "已加密"),
    ],
)
def test_extract_rejects_unplayable(qishui, audio, message):
    exception = importlib.import_module("core.exception")
    with pytest.raises(exception.ParseException, match=message):
        qishui.extract_audio_option(make_html(audio))


def test_extract_without_router_data(qishui):
    exception = importlib.import_module("core.exception")
    with pytest.raises(exception.ParseException):
        qishui.extract_audio_option("<html>WAF</html>")


class FakeResponse:
    status = 200

    def __init__(self, text: str):
        self._text = text

    async def text(self):
        return self._text

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def test_parse_track_builds_audio_with_card(qishui):
    html = make_html(make_audio())
    downloads = []

    def download(kind):
        def run(url, **kwargs):
            downloads.append((kind, url, kwargs.get("audio_name")))
            return f"{kind}-task"

        return run

    parser = SimpleNamespace(
        ios_headers={"User-Agent": "test", "Cookie": "secret"},
        session=SimpleNamespace(get=lambda url, **kwargs: FakeResponse(html)),
        proxy=None,
        downloader=SimpleNamespace(
            download_img=download("img"), download_audio=download("audio")
        ),
        create_author=lambda name, avatar, headers=None: SimpleNamespace(
            name=name, avatar=avatar
        ),
    )

    result = asyncio.run(qishui.parse_track(parser, "7664509149698361370"))

    assert result.platform.display_name == "汽水音乐"
    assert result.title == "可惜火影的影"
    assert result.author.name == "苗天天"
    assert result.author.avatar == "https://img.example/tos/avatar~c5_300x300.jpg"
    assert "专辑：可惜火影的影" in result.text
    assert "收藏 5.9万" in result.text
    (audio,) = result.contents
    assert audio.cover == "img-task"
    assert audio.duration == 206
    # 光一个音频文件看不出是哪首歌, 必须带卡片
    (group,) = result.send_groups
    assert group.render_card is True
    assert ("audio", make_audio()["url"], "qishui_7664509149698361370.m4a") in downloads
