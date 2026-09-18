from __future__ import annotations

import asyncio
import importlib
from types import SimpleNamespace

import pytest

pytest.importorskip("aiohttp")
pytest.importorskip("msgspec")
pytest.importorskip("yt_dlp")

from stubs import install_stubs

MB = 1024 * 1024


class FakeResponse:
    def __init__(self, status: int, size: int, url: str):
        self.status = status
        self.headers = {"Content-Range": f"bytes 0-1/{size}"} if size else {}
        self.url = url
        self.content_type = "video/mp4" if status < 400 else "text/html"

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeSession:
    """按 ratio 返回预设的 (status, size)"""

    closed = False

    def __init__(self, table: dict[str, tuple[int, int]]):
        self.table = table
        self.requested: list[str] = []

    def get(self, url: str, **kwargs):
        ratio = url.split("ratio=")[1].split("&")[0]
        self.requested.append(ratio)
        status, size = self.table.get(ratio, (404, 0))
        return FakeResponse(status, size, f"https://cdn.example/{ratio}")


@pytest.fixture
def douyin_module(monkeypatch: pytest.MonkeyPatch):
    install_stubs(monkeypatch)
    return importlib.import_module("core.parsers.douyin")


def make_parser(douyin_module, quality: str, max_mb: int, table, lowest=False):
    parser = douyin_module.DouyinParser.__new__(douyin_module.DouyinParser)
    parser.cfg = SimpleNamespace(max_size=max_mb * MB, force_lowest_quality=lowest)
    parser.mycfg = SimpleNamespace(video_quality=quality)
    parser.ios_headers = {"User-Agent": "test"}
    parser._session = FakeSession(table)
    return parser


TABLE = {"1080p": (206, 24 * MB), "720p": (206, 20 * MB), "540p": (206, 14 * MB)}


def test_quality_ladder_starts_at_configured_quality(douyin_module):
    assert make_parser(douyin_module, "720p", 90, TABLE).quality_ladder == (
        "720p",
        "540p",
    )
    assert make_parser(douyin_module, "", 90, TABLE).quality_ladder == (
        "1080p",
        "720p",
        "540p",
    )


def test_official_mode_forces_lowest_quality(douyin_module):
    parser = make_parser(douyin_module, "1080p", 90, TABLE, lowest=True)
    assert parser.quality_ladder == ("540p",)
    probed = asyncio.run(parser.probe_video_url("vid", "https://ref/"))
    assert probed.ratio == "540p"
    assert parser._session.requested == ["540p"]


def test_probe_picks_configured_quality(douyin_module):
    parser = make_parser(douyin_module, "720p", 90, TABLE)
    probed = asyncio.run(parser.probe_video_url("vid", "https://ref/"))
    assert (probed.ratio, probed.size) == ("720p", 20 * MB)
    assert parser._session.requested == ["720p"]


def test_probe_steps_down_when_over_size_limit(douyin_module):
    parser = make_parser(douyin_module, "1080p", 15, TABLE)
    probed = asyncio.run(parser.probe_video_url("vid", "https://ref/"))
    assert probed.ratio == "540p"
    assert parser._session.requested == ["1080p", "720p", "540p"]


def test_probe_returns_smallest_when_all_over_limit(douyin_module):
    parser = make_parser(douyin_module, "1080p", 10, TABLE)
    probed = asyncio.run(parser.probe_video_url("vid", "https://ref/"))
    assert probed.ratio == "540p"


def test_probe_skips_failed_ratio(douyin_module):
    table = {**TABLE, "1080p": (403, 0)}
    parser = make_parser(douyin_module, "1080p", 90, table)
    probed = asyncio.run(parser.probe_video_url("vid", "https://ref/"))
    assert probed.ratio == "720p"


def test_probe_raises_when_nothing_available(douyin_module):
    exception = importlib.import_module("core.exception")
    parser = make_parser(douyin_module, "1080p", 90, {})
    with pytest.raises(exception.ParseException):
        asyncio.run(parser.probe_video_url("vid", "https://ref/"))


def test_video_data_reports_filter_reason(douyin_module):
    import msgspec

    video = importlib.import_module("core.parsers.douyin.video")
    exception = importlib.import_module("core.exception")
    raw = {
        "loaderData": {
            "video_(id)/page": {
                "videoInfoRes": {
                    "item_list": [],
                    "filter_list": [{"filter_reason": "SYSTEM_ITEM_NOT_EXIST"}],
                }
            }
        }
    }
    router = msgspec.convert(raw, video.RouterData)
    with pytest.raises(exception.ParseException, match="SYSTEM_ITEM_NOT_EXIST"):
        router.video_data


def test_mirror_lists_are_split_into_primary_and_backups(douyin_module):
    base = importlib.import_module("core.parsers.base")
    assert base.BaseParser._split_mirrors("https://a") == ("https://a", [])
    assert base.BaseParser._split_mirrors(["https://a", "https://b"]) == (
        "https://a",
        ["https://b"],
    )
