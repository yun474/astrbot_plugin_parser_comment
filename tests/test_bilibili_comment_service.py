from __future__ import annotations

import asyncio
import importlib
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def comment_module(monkeypatch: pytest.MonkeyPatch):
    root = Path(__file__).resolve().parents[1]
    warnings: list[str] = []
    logger = SimpleNamespace(
        debug=lambda *args, **kwargs: None,
        warning=lambda message, *args, **kwargs: warnings.append(str(message)),
    )

    astrbot_pkg = types.ModuleType("astrbot")
    astrbot_pkg.__path__ = []
    astrbot_api = types.ModuleType("astrbot.api")
    astrbot_api.logger = logger

    core_pkg = types.ModuleType("core")
    core_pkg.__path__ = [str(root / "core")]
    parsers_pkg = types.ModuleType("core.parsers")
    parsers_pkg.__path__ = [str(root / "core" / "parsers")]
    bilibili_pkg = types.ModuleType("core.parsers.bilibili")
    bilibili_pkg.__path__ = [str(root / "core" / "parsers" / "bilibili")]

    data_module = types.ModuleType("core.data")
    data_module.ImageContent = type("ImageContent", (), {})

    exception_module = types.ModuleType("core.exception")
    exception_module.DownloadLimitException = type(
        "DownloadLimitException",
        (Exception,),
        {},
    )

    renderer_module = types.ModuleType("core.parsers.bilibili.comment_renderer")
    renderer_module.BiliCommentRenderer = type("BiliCommentRenderer", (), {})
    renderer_module.BiliCommentRenderItem = type("BiliCommentRenderItem", (), {})

    aiohttp_module = types.ModuleType("aiohttp")
    aiohttp_module.ClientTimeout = type(
        "ClientTimeout",
        (),
        {"__init__": lambda self, *args, **kwargs: None},
    )

    modules = {
        "astrbot": astrbot_pkg,
        "astrbot.api": astrbot_api,
        "core": core_pkg,
        "core.parsers": parsers_pkg,
        "core.parsers.bilibili": bilibili_pkg,
        "core.data": data_module,
        "core.exception": exception_module,
        "core.parsers.bilibili.comment_renderer": renderer_module,
        "aiohttp": aiohttp_module,
    }
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)

    monkeypatch.delitem(
        sys.modules,
        "core.parsers.bilibili.comment_service",
        raising=False,
    )
    module = importlib.import_module("core.parsers.bilibili.comment_service")
    return module, warnings


def make_parser(*, credential=None, raw_cookies: str | None = None):
    class FakeLogin:
        @property
        async def credential(self):
            return credential

    return SimpleNamespace(
        headers={"User-Agent": "test"},
        login=FakeLogin(),
        mycfg=SimpleNamespace(cookies=raw_cookies),
        proxy=None,
    )


def test_comment_headers_use_saved_qr_login_credential(comment_module):
    module, _ = comment_module
    credential = SimpleNamespace(
        get_cookies=lambda: {
            "SESSDATA": "saved-session",
            "bili_jct": "saved-csrf",
        }
    )
    parser = make_parser(credential=credential)
    service = module.BiliCommentService(parser, renderer=object())

    headers, authenticated = asyncio.run(service._build_request_headers())

    assert authenticated is True
    assert "SESSDATA=saved-session" in headers["Cookie"]
    assert "bili_jct=saved-csrf" in headers["Cookie"]


def test_comment_headers_fall_back_to_config_cookie(comment_module):
    module, _ = comment_module
    parser = make_parser(
        credential=None,
        raw_cookies="SESSDATA=config-session; bili_jct=config-csrf",
    )
    service = module.BiliCommentService(parser, renderer=object())

    headers, authenticated = asyncio.run(service._build_request_headers())

    assert authenticated is True
    assert headers["Cookie"] == "SESSDATA=config-session; bili_jct=config-csrf"


def test_fetch_comments_includes_top_replies(comment_module, monkeypatch):
    module, _ = comment_module
    parser = make_parser()
    service = module.BiliCommentService(
        parser,
        renderer=object(),
        comment_limit=4,
        enable_text_ad_filter=False,
    )

    class FakeResponse:
        status_code = 200

    class FakeSession:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, *args, **kwargs):
            return FakeResponse()

    def reply(rpid: str, message: str):
        return {
            "rpid": rpid,
            "content": {"message": message, "pictures": []},
            "member": {"uname": f"user-{rpid}", "avatar": ""},
        }

    async def fake_headers():
        return {"User-Agent": "test"}, False

    async def fake_page(*args, **kwargs):
        return {
            "top_replies": [reply("top", "置顶评论")],
            "replies": [
                reply("1", "普通评论"),
                reply("2", "@某人 回复评论"),
                reply("3", "另一条评论"),
            ],
            "cursor": {"is_end": True, "next": 1, "all_count": 4},
        }

    monkeypatch.setattr(module, "CurlAsyncSession", FakeSession)
    monkeypatch.setattr(service, "_build_request_headers", fake_headers)
    monkeypatch.setattr(service, "_fetch_comment_page", fake_page)

    comments = asyncio.run(service._fetch_comments(123, 1))

    assert [comment.rpid for comment in comments] == ["top", "1", "3", "2"]


def test_legacy_page_normalization_keeps_total_count(comment_module):
    module, _ = comment_module
    block = {
        "replies": [{"rpid": "1"}],
        "page": {"num": 1, "size": 20, "count": 41},
    }

    normalized = module.BiliCommentService._normalize_legacy_reply_page(block, 1)

    assert normalized["cursor"] == {
        "is_end": False,
        "next": 2,
        "all_count": 41,
    }
