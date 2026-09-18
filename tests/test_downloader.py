from __future__ import annotations

import asyncio
import importlib
from types import SimpleNamespace

import pytest

pytest.importorskip("aiohttp")
pytest.importorskip("aiofiles")
pytest.importorskip("yt_dlp")

from aiohttp import web
from aiohttp.test_utils import TestServer

from stubs import install_stubs


@pytest.fixture
def download_module(monkeypatch: pytest.MonkeyPatch):
    logs = install_stubs(monkeypatch)
    module = importlib.import_module("core.download")
    exception = importlib.import_module("core.exception")
    return module, exception, logs


def make_app(hits: dict[str, int]):
    payload = b"x" * 2048

    async def handler(request: web.Request):
        name = request.match_info["name"]
        hits[name] = hits.get(name, 0) + 1
        if name == "ok":
            return web.Response(body=payload)
        if name == "slow":
            await asyncio.sleep(0.5)
            return web.Response(body=payload)
        if name == "zero":
            return web.Response(body=b"")
        if name == "big":
            return web.Response(body=b"x" * (2 * 1024 * 1024))
        if name == "html":
            return web.Response(
                text="<html>Please wait...</html>", content_type="text/html"
            )
        return web.Response(status=int(name))

    app = web.Application()
    app.router.add_get("/{name}", handler)
    return app


def run(download_module, tmp_path, scenario):
    module, _, _ = download_module
    hits: dict[str, int] = {}

    async def main():
        server = TestServer(make_app(hits))
        await server.start_server()
        cfg = SimpleNamespace(
            max_size=1024 * 1024,
            download_timeout=10,
            download_retry_times=1,
            cache_dir=tmp_path,
        )
        downloader = module.Downloader(cfg)
        try:
            return await scenario(downloader, str(server.make_url("")).rstrip("/"))
        finally:
            await downloader.close()
            await server.close()

    result = asyncio.run(main())
    return result, hits


def test_backup_url_used_after_primary_fails(download_module, tmp_path):
    async def scenario(downloader, base):
        return await downloader.download_img(
            f"{base}/403", img_name="a.bin", backup_urls=[f"{base}/ok"]
        )

    path, hits = run(download_module, tmp_path, scenario)
    assert path.read_bytes() == b"x" * 2048
    assert hits == {"403": 1, "ok": 1}
    assert not list(tmp_path.glob("*.part"))


def test_all_urls_fail_reports_reason(download_module, tmp_path):
    _, exception, logs = download_module

    async def scenario(downloader, base):
        with pytest.raises(exception.DownloadException) as info:
            await downloader.download_img(
                f"{base}/500", img_name="b.bin", backup_urls=[f"{base}/404"]
            )
        return info.value.message

    message, hits = run(download_module, tmp_path, scenario)
    assert "HTTP 404" in message
    assert hits == {"500": 1, "404": 1}
    assert any(log.startswith("error: 下载失败: HTTP 404") for log in logs)
    assert not (tmp_path / "b.bin").exists()


def test_concurrent_same_file_downloads_once(download_module, tmp_path):
    async def scenario(downloader, base):
        first = downloader.download_img(f"{base}/slow", img_name="c.bin")
        await asyncio.sleep(0.1)
        second = downloader.download_img(f"{base}/slow", img_name="c.bin")
        return await asyncio.gather(first, second)

    (path1, path2), hits = run(download_module, tmp_path, scenario)
    assert path1 == path2
    assert hits == {"slow": 1}


def test_zero_size_and_size_limit(download_module, tmp_path):
    _, exception, _ = download_module

    async def scenario(downloader, base):
        with pytest.raises(exception.ZeroSizeException):
            await downloader.download_img(f"{base}/zero", img_name="d.bin")
        with pytest.raises(exception.SizeLimitException):
            await downloader.download_img(f"{base}/big", img_name="e.bin")

    run(download_module, tmp_path, scenario)
    assert not list(tmp_path.iterdir())


def test_html_page_is_not_saved_as_media(download_module, tmp_path):
    async def scenario(downloader, base):
        return await downloader.download_video(
            f"{base}/html", video_name="f.mp4", backup_urls=[f"{base}/ok"]
        )

    path, hits = run(download_module, tmp_path, scenario)
    assert hits == {"html": 1, "ok": 1}
    assert path.read_bytes() == b"x" * 2048
