from __future__ import annotations

import asyncio
import importlib
from unittest.mock import AsyncMock

import pytest
from stubs import install_stubs


@pytest.fixture
def module(monkeypatch):
    install_stubs(monkeypatch)
    return importlib.import_module("core.comment_utils")


def test_cache_shares_build_and_survives_cancelled_waiter(module, tmp_path):
    async def run():
        cache = module.CommentImageCache()
        ready = asyncio.Event()
        release = asyncio.Event()
        path = tmp_path / "image.jpg"
        path.write_bytes(b"image")
        calls = 0

        async def build():
            nonlocal calls
            calls += 1
            ready.set()
            await release.wait()
            return path

        first = asyncio.create_task(cache.get((1,), build))
        await ready.wait()
        second = asyncio.create_task(cache.get((1,), build))
        await asyncio.sleep(0)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        release.set()
        assert await second == path
        assert await cache.get((1,), build) == path
        assert calls == 1
        assert not cache._pending
        await cache.close()

    asyncio.run(run())


def test_cache_expiry_missing_file_and_failure_retry(module, tmp_path, monkeypatch):
    async def run():
        cache = module.CommentImageCache()
        clock = [0]
        monkeypatch.setattr(module, "monotonic", lambda: clock[0])
        path = tmp_path / "a.jpg"

        async def build():
            path.write_bytes(b"image")
            return path

        render = AsyncMock(side_effect=build)
        await cache.get((1,), render)
        clock[0] = cache.TTL + 1
        await cache.get((1,), render)
        path.unlink()
        await cache.get((1,), render)
        assert render.await_count == 3
        failed = AsyncMock(side_effect=ValueError("temporary"))
        with pytest.raises(ValueError):
            await cache.get((2,), failed)
        assert await cache.get((2,), render) == path
        await cache.close()

    asyncio.run(run())


def test_cache_deadline_and_close_cancel_workers(module, tmp_path):
    async def run():
        cache = module.CommentImageCache()
        cache.BUILD_TIMEOUT = 0.01
        with pytest.raises(TimeoutError):
            await cache.get((1,), lambda: asyncio.sleep(5))
        assert not cache._pending
        cache.BUILD_TIMEOUT = 60
        started = asyncio.Event()

        async def build():
            started.set()
            await asyncio.sleep(5)

        waiter = asyncio.create_task(cache.get((1,), build))
        await started.wait()
        await cache.close()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        assert not cache._pending

    asyncio.run(run())


def test_disk_cache_expires_so_failed_assets_can_be_retried(module, tmp_path):
    import os
    from time import time

    cache = module.CommentImageCache()
    path = tmp_path / "image.jpg"
    assert not cache.is_fresh(path)
    path.write_bytes(b"image" * 30)
    assert cache.is_fresh(path)
    old = time() - cache.TTL - 1
    os.utime(path, (old, old))
    assert not cache.is_fresh(path)


def test_assets_dedup_deadline_concurrency_and_partial_success(module):
    async def run():
        active = peak = 0
        calls = []
        cancelled = []

        async def download(url):
            nonlocal active, peak
            calls.append(url)
            active += 1
            peak = max(peak, active)
            try:
                if url == "ok":
                    return "image"
                if url == "bad":
                    raise OSError("broken")
                await asyncio.sleep(5)
            except asyncio.CancelledError:
                cancelled.append(url)
                raise
            finally:
                active -= 1

        results = await module.download_comment_assets(
            ["ok", "ok", "bad", "slow1", "slow2", "queued"],
            download,
            timeout=0.02,
            concurrency=2,
        )
        assert results == {"ok": "image", "bad": None}
        assert calls.count("ok") == 1
        assert peak <= 2 and active == 0
        assert set(cancelled) == {"slow1", "slow2"}
        assert "queued" not in calls

    asyncio.run(run())
