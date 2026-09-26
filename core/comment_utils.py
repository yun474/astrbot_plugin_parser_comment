"""评论图的短期缓存与可选配图下载，不拖住主媒体发送。"""

from __future__ import annotations

import asyncio
from pathlib import Path
from time import monotonic, time


class CommentImageCache:
    TTL = 120
    MAX_ENTRIES = 128
    BUILD_TIMEOUT = 60

    def __init__(self):
        self._ready: dict[tuple, tuple[float, Path]] = {}
        self._pending: dict[tuple, asyncio.Task] = {}

    def is_fresh(self, path: Path) -> bool:
        try:
            stat = path.stat()
            return stat.st_size > 100 and time() - stat.st_mtime < self.TTL
        except FileNotFoundError:
            return False

    async def get(self, key: tuple, build) -> Path:
        if cached := self._ready.get(key):
            expires, path = cached
            if monotonic() < expires and path.is_file():
                return path
            self._ready.pop(key, None)
        if key not in self._pending:
            task = asyncio.create_task(self._build(key, build))
            task.add_done_callback(lambda t: t.cancelled() or t.exception())
            self._pending[key] = task
        # 一个调用者取消发送，不影响同作品的其他调用者。
        return await asyncio.shield(self._pending[key])

    async def _build(self, key, build):
        try:
            async with asyncio.timeout(self.BUILD_TIMEOUT):
                path = await build()
            if len(self._ready) >= self.MAX_ENTRIES:
                self._ready.pop(next(iter(self._ready)))
            self._ready[key] = (monotonic() + self.TTL, path)
            return path
        finally:
            self._pending.pop(key, None)

    async def close(self):
        tasks = list(self._pending.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._ready.clear()


async def download_comment_assets(urls, download, *, timeout=8, concurrency=6):
    """按 URL 去重；整批最多等 8 秒，保留成功的图，取消未完成的下载。"""
    semaphore = asyncio.Semaphore(concurrency)
    results = {}

    async def fetch(url):
        async with semaphore:
            try:
                results[url] = await download(url)
            except Exception:  # noqa: BLE001 - 配图失败不影响评论文字
                results[url] = None

    tasks = [asyncio.create_task(fetch(url)) for url in dict.fromkeys(urls) if url]
    if tasks:
        try:
            await asyncio.wait(tasks, timeout=timeout)
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
    return results
