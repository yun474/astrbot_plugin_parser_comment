import asyncio
import importlib
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from PIL import Image
from stubs import install_stubs


@pytest.fixture
def module(monkeypatch):
    install_stubs(monkeypatch)
    monkeypatch.delitem(sys.modules, "core.html_render", raising=False)
    return importlib.import_module("core.html_render")


def test_invalid_asset_skipped_and_large_asset_resized(module, tmp_path):
    import base64
    from io import BytesIO

    broken = tmp_path / "bad.png"
    broken.write_bytes(b"not an image")
    assert module.image_data_uri(broken) is None
    large = tmp_path / "big.png"
    Image.new("RGB", (3200, 1000), "red").save(large)
    uri = module.image_data_uri(large)
    with Image.open(BytesIO(base64.b64decode(uri.split(",", 1)[1]))) as image:
        assert image.size == (1600, 500)


def test_screenshot_failure_cleans_temp_and_preserves_good_output(module, tmp_path):
    async def run():
        renderer = module.HtmlRenderer(SimpleNamespace(render_engine="playwright"))
        output = tmp_path / "image.jpg"
        output.write_bytes(b"existing good image")
        context = SimpleNamespace(
            new_page=AsyncMock(side_effect=OSError("closed")), close=AsyncMock()
        )
        browser = SimpleNamespace(new_context=AsyncMock(return_value=context))
        renderer._get_browser = AsyncMock(return_value=browser)
        with pytest.raises(OSError):
            await renderer._screenshot("<html/>", output, "#root")
        context.close.assert_awaited_once()
        assert output.read_bytes() == b"existing good image"
        assert list(tmp_path.iterdir()) == [output]

    asyncio.run(run())


def test_remote_renderer_receives_inline_symbol_fonts(module, tmp_path):
    async def run():
        renderer = module.HtmlRenderer(SimpleNamespace(render_engine="astrbot"))
        renderer._render_by_astrbot = AsyncMock()
        await renderer.render("bili_base.html", {"width": 720}, tmp_path / "a.jpg")
        html = renderer._render_by_astrbot.call_args.args[0]
        assert html.count("data:font/ttf;base64,") == 2
        assert all(p.as_uri() not in html for p in module.SYMBOL_FONT_PATHS)

    asyncio.run(run())


def test_cancelled_waiter_does_not_cancel_shared_browser_startup(module):
    async def run():
        renderer = module.HtmlRenderer(SimpleNamespace(render_engine="playwright"))
        started = asyncio.Event()
        release = asyncio.Event()
        browser = SimpleNamespace(close=AsyncMock())

        async def start():
            started.set()
            await release.wait()
            return browser

        renderer._start_browser = AsyncMock(side_effect=start)
        first = asyncio.create_task(renderer._get_browser())
        await started.wait()
        second = asyncio.create_task(renderer._get_browser())
        await asyncio.sleep(0)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        release.set()
        assert await second is browser
        renderer._start_browser.assert_awaited_once()
        await renderer.close()

    asyncio.run(run())


def test_idle_browser_exits_and_reuse_postpones_it(module):
    async def run():
        renderer = module.HtmlRenderer(SimpleNamespace(render_engine="playwright"))
        renderer.IDLE_TIMEOUT = 0.05
        context = SimpleNamespace(close=AsyncMock())
        browser = SimpleNamespace(
            new_context=AsyncMock(return_value=context),
            is_connected=lambda: True,
            close=AsyncMock(),
        )
        playwright = SimpleNamespace(stop=AsyncMock())
        renderer._browser, renderer._playwright = browser, playwright

        async with renderer.browser_context():
            pass
        await asyncio.sleep(0.03)
        async with renderer.browser_context():
            await asyncio.sleep(0.06)
        browser.close.assert_not_called()

        await asyncio.sleep(0.1)
        browser.close.assert_awaited_once()
        playwright.stop.assert_awaited_once()
        assert renderer._browser is None and renderer._playwright is None
        assert context.close.await_count == 2

    asyncio.run(run())
