"""按标题搜 B站视频, 给只有标题没有链接的分享卡片用"""

from __future__ import annotations

import asyncio

from astrbot.api import logger

from .common import pick_video_by_title
from .web import BiliWebClient


class BiliSearch:
    # WBI 版搜索接口挂在风控网关后面, 访客态时不时会收到只带 v_voucher 的空响应,
    # 普通版接口目前没有这个问题, 所以先用普通版, WBI 版兜底。
    ENDPOINTS = (
        ("https://api.bilibili.com/x/web-interface/search/type", False),
        ("https://api.bilibili.com/x/web-interface/wbi/search/type", True),
    )
    WARM_UP_URL = "https://www.bilibili.com/"
    ATTEMPTS = 3

    def __init__(self, web: BiliWebClient):
        self.web = web

    async def find_video(self, title: str) -> str | None:
        """返回同名视频的 bvid; 没有同名视频返回 None, 接口一直被风控则抛异常

        两个接口的索引和排序不完全一样 (普通版有时缺最新的视频), 所以一个没找到
        还要问另一个; 被风控的接口隔一会儿再问, 最多 ATTEMPTS 轮。
        """
        headers, _ = await self.web.headers()
        params = {"search_type": "video", "keyword": title, "page": 1, "page_size": 42}
        pending = list(self.ENDPOINTS)
        answered = False

        async with self.web.session() as session:
            await self.web.warm_up(session, headers, self.WARM_UP_URL)
            for attempt in range(self.ATTEMPTS):
                if attempt:
                    await asyncio.sleep(1.5)
                blocked = []
                for url, wbi in pending:
                    results = await self._search(session, url, params, headers, wbi)
                    if results is None:
                        blocked.append((url, wbi))
                        continue
                    answered = True
                    if bvid := pick_video_by_title(title, results):
                        return bvid
                    logger.info(
                        f"[Bilibili-search] {len(results)} 条结果里没有同名视频: {title}"
                    )
                if not blocked:
                    break
                pending = blocked

        if answered:
            return None
        raise RuntimeError("搜索接口被风控, 请稍后再试或配置 B站 Cookie")

    async def _search(
        self, session, url: str, params: dict, headers: dict[str, str], wbi: bool
    ) -> list[dict] | None:
        """返回结果列表; 接口出错或被风控 (只回 v_voucher, 没有 result) 时返回 None"""
        try:
            payload = await self.web.get_json(session, url, params, headers, wbi=wbi)
        except Exception as e:
            logger.debug(f"[Bilibili-search] 接口异常: {url}, {e}")
            return None
        data = payload.get("data") or {}
        if (
            payload.get("code") != 0
            or "v_voucher" in data
            or not ("result" in data or "numResults" in data)
        ):
            logger.debug(
                f"[Bilibili-search] 接口无有效结果: {url}, code={payload.get('code')}, "
                f"message={payload.get('message')}, voucher={'v_voucher' in data}"
            )
            return None
        return data.get("result") or []
