from __future__ import annotations

import asyncio
import importlib
import sys
from datetime import timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from stubs import install_stubs


@pytest.fixture
def module(monkeypatch):
    install_stubs(monkeypatch)
    for name in ("core.html_render", "core.parsers.douyin.comments"):
        monkeypatch.delitem(sys.modules, name, raising=False)
    return importlib.import_module("core.parsers.douyin.comments")


def service(module, tmp_path, **settings):
    cfg = {
        "comment_render_enable": True,
        "comment_limit": 9,
        "comment_show_replies": True,
        "comment_merge_with_video": False,
    }
    cfg.update(settings)
    parser = SimpleNamespace(
        mycfg=SimpleNamespace(**cfg),
        cfg=SimpleNamespace(cache_dir=tmp_path, timezone=timezone.utc),
        proxy=None,
        cookiejar=SimpleNamespace(cookies=[]),
        downloader=SimpleNamespace(download_img=AsyncMock(side_effect=OSError)),
    )
    return module.DouyinCommentService(parser)


def renderer_with(browser):
    from core.html_render import HtmlRenderer

    renderer = HtmlRenderer(SimpleNamespace(render_engine="playwright"))
    renderer._get_browser = AsyncMock(return_value=browser)
    return renderer


def comment(cid="1", **kwargs):
    return {"cid": cid, "text": "测试评论", "user": {"nickname": "用户"}, **kwargs}


def test_parse_pictures_replies_and_badges(module, tmp_path):
    svc = service(module, tmp_path)
    c = svc._parse_comment(
        comment(
            text="",
            image_list=[{"origin_url": {"url_list": ["https://example.com/a.jpg"]}}],
            label_text="作者",
            is_author_digged=True,
            stick_position=1,
            reply_comment=[comment("2")],
            reply_comment_total=4,
        )
    )
    assert c["picture_urls"] == ["https://example.com/a.jpg"]
    assert c["author"] and c["liked"] and c["pinned"]
    assert c["replies"][0]["id"] == "2"
    svc.show_replies = False
    assert not svc._parse_comment(comment(reply_comment=[comment("2")]))["replies"]
    assert svc._parse_comment(comment(text="")) is None


def test_reject_blocked_payload_not_empty_comments(module, tmp_path):
    svc = service(module, tmp_path)
    assert svc._validate_page({"status_code": 0, "comments": None, "total": 0}) == []
    for payload in ({}, {"status_code": 1}, {"status_code": 0, "total": 10}):
        with pytest.raises((ValueError, TypeError)):
            svc._validate_page(payload)


@pytest.mark.parametrize("merge", [True, False])
def test_background_send_groups(module, tmp_path, merge):
    async def run():
        svc = service(module, tmp_path, comment_merge_with_video=merge)
        svc._build_image = AsyncMock(return_value=tmp_path / "comments.jpg")
        media = object()
        groups = svc.build_send_groups([media], "123", "title", "author")
        assert len(groups) == (1 if merge else 2)
        assert groups[0].contents[0] is media
        assert groups[-1].force_merge is True
        if merge:
            assert groups[0].preserve_order
        else:
            assert groups[1].render_card is False
        await asyncio.sleep(0)
        svc._build_image.assert_awaited_once()

    asyncio.run(run())


@pytest.mark.parametrize(
    "settings", [{"comment_render_enable": False}, {"comment_limit": 0}]
)
def test_disabled_does_not_start_task(module, tmp_path, settings):
    svc = service(module, tmp_path, **settings)
    assert svc.build_send_groups([], "123", "title", "author") == []


def test_render_escapes_comment_and_failed_assets(module, tmp_path):
    async def run():
        svc = service(module, tmp_path)
        c = svc._parse_comment(
            comment(
                text="<script>alert(1)</script>{{danger}}", user={"nickname": "<用户>"}
            )
        )
        await svc._attach_assets([c])
        from core.html_render import TEMPLATES

        html = TEMPLATES.get_template("douyin_comments.html").render(
            width=720,
            title="作品",
            author="作者",
            total=1,
            comments=[c],
            show_replies=True,
        )
        assert "抖音 · 评论区" in html
        assert "&lt;script&gt;" in html and "<script>" not in html
        assert "&#123;&#123;danger&#125;&#125;" in html
        assert "LV0" not in html and "bilibili" not in html

    asyncio.run(run())


def test_fetch_failure_becomes_optional_content_failure(module, tmp_path):
    async def run():
        svc = service(module, tmp_path)
        svc._fetch_comments = AsyncMock(side_effect=TimeoutError)
        with pytest.raises(module.DownloadLimitException):
            await svc._build_image("123", "作品", "作者")

    asyncio.run(run())


def test_repeat_requests_reuse_image_without_browser(module, tmp_path):
    async def run():
        svc = service(module, tmp_path)
        svc._fetch_comments = AsyncMock(
            side_effect=lambda *a, **k: ([svc._parse_comment(comment())], 1)
        )

        async def render(template, context, path):
            path.write_bytes(b"image" * 30)
            return path

        renderer = AsyncMock(side_effect=render)
        svc.parser.html_renderer = SimpleNamespace(render=renderer)
        first, second = await asyncio.gather(
            svc._build_image("123", "作品", "作者"),
            svc._build_image("123", "作品", "作者"),
        )
        assert first == second
        assert await svc._build_image("123", "作品", "作者") == first
        svc._fetch_comments.assert_awaited_once()
        renderer.assert_awaited_once()
        await svc.image_cache.close()

    asyncio.run(run())


@pytest.mark.parametrize("fail_next", [False, True])
@pytest.mark.parametrize("initial_cursor", [0, 20])
def test_browser_pagination_deduplicates_and_closes(
    module, tmp_path, fail_next, initial_cursor
):
    async def run():
        svc = service(module, tmp_path, comment_limit=3)
        first = {
            "status_code": 0,
            "comments": [comment("1"), comment("2")],
            "total": 50,
            "cursor": 2,
            "has_more": 1,
        }
        second = {
            "status_code": 0,
            "comments": [comment("2"), comment("3")],
            "total": 50,
            "cursor": 4,
            "has_more": 0,
        }
        response = SimpleNamespace(
            url=f"https://www-hj.douyin.com/aweme/v1/web/comment/list/?aweme_id=123&cursor={initial_cursor}",
            json=AsyncMock(return_value=first),
        )

        class Event:
            async def __aenter__(self):
                self.value = asyncio.get_running_loop().create_future()
                self.value.set_result(response)
                return self

            async def __aexit__(self, *args):
                return False

        page = SimpleNamespace(
            route=AsyncMock(),
            goto=AsyncMock(),
            expect_response=lambda predicate, **kw: Event(),
            evaluate=AsyncMock(return_value=second),
        )
        page.evaluate.side_effect = ([first] if initial_cursor else []) + [
            ValueError("empty response") if fail_next else second
        ]
        context = SimpleNamespace(
            new_page=AsyncMock(return_value=page), close=AsyncMock()
        )
        browser = SimpleNamespace(new_context=AsyncMock(return_value=context))
        svc.parser.html_renderer = renderer_with(browser)
        comments, total = await svc._fetch_comments("123", kind="note")
        assert page.goto.call_args.args[0] == "https://www.douyin.com/note/123"
        if initial_cursor:
            assert page.evaluate.call_args_list[0].args[1]["cursor"] == 0
        assert [c["id"] for c in comments] == (
            ["1", "2"] if fail_next else ["1", "2", "3"]
        )
        assert total == 50
        context.close.assert_awaited_once()
        assert svc._matches_response(response, "123")
        assert not svc._matches_response(response, "456")

    asyncio.run(run())


def test_browser_context_closed_on_navigation_failure(module, tmp_path):
    async def run():
        svc = service(module, tmp_path)
        context = SimpleNamespace(
            new_page=AsyncMock(side_effect=OSError), close=AsyncMock()
        )
        browser = SimpleNamespace(new_context=AsyncMock(return_value=context))
        svc.parser.html_renderer = renderer_with(browser)
        with pytest.raises(OSError):
            await svc._fetch_comments("123")
        context.close.assert_awaited_once()

    asyncio.run(run())


@pytest.mark.parametrize("kind", ["video", "note"])
def test_parser_attaches_comment_group(module, tmp_path, kind):
    async def run():
        from core.parsers.douyin import DouyinParser

        parser = DouyinParser.__new__(DouyinParser)
        parser.mycfg = SimpleNamespace(gallery_merge_video=False)
        parser.ensure_ttwid = AsyncMock()
        data = SimpleNamespace(
            author=SimpleNamespace(nickname="作者"),
            desc="作品",
            images=[],
            video=None,
            avatar_url=None,
            create_time=1,
        )
        parser._load_share_data = AsyncMock(return_value=("https://example.com", data))
        parser.create_author = lambda *args, **kw: "author"
        parser.ios_headers = {}
        parser.result = lambda **kwargs: kwargs
        svc = service(module, tmp_path)
        svc._build_image = AsyncMock(return_value=tmp_path / "comments.jpg")
        parser.comment_service = svc
        result = await parser.parse_video(kind, "123")
        assert len(result["send_groups"]) == 2
        assert result["title"] == "作品"
        await asyncio.sleep(0)
        svc._build_image.assert_awaited_once_with("123", "作品", "作者", kind=kind)

    asyncio.run(run())
