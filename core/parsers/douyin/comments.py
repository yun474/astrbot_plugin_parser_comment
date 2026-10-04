"""通过抖音网页的签名请求读取评论，异步生成独立评论卡片。"""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime
from time import monotonic
from urllib.parse import parse_qs, urlparse

from astrbot.api import logger

from ...comment_utils import CommentImageCache, download_comment_assets
from ...data import DynamicContent, ImageContent, SendGroup
from ...exception import DownloadLimitException
from ...html_render import image_data_uri
from ...utils import stable_url


class DouyinCommentService:
    API_PATH = "/aweme/v1/web/comment/list/"
    MAX_PAGES = 3
    # 使用网页已经生成的设备参数；fetch 必须传字符串，才能经过网站 SDK 签名。
    FETCH_PAGE = """async ({original, cursor}) => {
        const url = new URL(original);
        url.searchParams.delete('a_bogus');
        url.searchParams.delete('X-Bogus');
        url.searchParams.set('cursor', String(cursor));
        url.searchParams.set('count', '20');
        const response = await fetch(url.toString(), {
            credentials: 'include', signal: AbortSignal.timeout(8000)
        });
        if (!response.ok) throw new Error(`评论接口 HTTP ${response.status}`);
        return await response.json();
    }"""

    def __init__(self, parser):
        self.parser = parser
        cfg = parser.mycfg
        self.enabled = cfg.comment_render_enable is not False
        self.limit = max(
            0, min(20, 9 if cfg.comment_limit is None else int(cfg.comment_limit))
        )
        self.show_replies = cfg.comment_show_replies is not False
        self.merge = bool(cfg.comment_merge_with_video)
        self._lock = asyncio.Semaphore(2)
        self.image_cache = CommentImageCache()

    def build_send_groups(
        self, contents, vid: str, title: str, author: str, *, kind: str = "video"
    ):
        preserve_order = any(isinstance(c, DynamicContent) for c in contents)
        if not self.enabled or not self.limit:
            return (
                [SendGroup(contents=contents, preserve_order=True)]
                if preserve_order
                else []
            )
        task = asyncio.create_task(
            self._build_image(vid, title, author, kind=kind),
            name=f"douyin_comments_{vid}",
        )
        task.add_done_callback(lambda t: t.cancelled() or t.exception())
        comments = [ImageContent(task)]
        if self.merge:
            return [
                SendGroup(
                    contents=[*contents, *comments],
                    force_merge=True,
                    preserve_order=True,
                )
            ]
        return [
            SendGroup(contents=contents, preserve_order=preserve_order),
            SendGroup(contents=comments, force_merge=True, render_card=False),
        ]

    @classmethod
    def _matches_response(cls, response, vid: str) -> bool:
        url = urlparse(response.url)
        return (
            (url.hostname or "").endswith(".douyin.com")
            and url.path == cls.API_PATH
            and parse_qs(url.query).get("aweme_id") == [vid]
        )

    @staticmethod
    def _validate_page(payload: dict) -> list[dict]:
        if not isinstance(payload, dict) or payload.get("status_code") != 0:
            raise ValueError("评论接口返回失败，可能需要更新抖音 Cookies")
        comments = payload.get("comments")
        if comments is None and payload.get("total") == 0:
            return []
        if not isinstance(comments, list):
            raise TypeError("评论接口未返回评论列表")
        return comments

    async def _fetch_comments(
        self, vid: str, *, kind: str = "video"
    ) -> tuple[list[dict], int]:
        # 浏览器由共享 HTML 渲染器统一管理（空闲自动退出），每次读取用独立 context 隔离 Cookie。
        options = {"locale": "zh-CN"}
        if self.parser.proxy:
            options["proxy"] = {"server": self.parser.proxy}
        async with self.parser.html_renderer.browser_context(**options) as context:
            cookies = [
                {
                    "name": c.name,
                    "value": c.value,
                    "domain": c.domain,
                    "path": c.path,
                    "secure": c.secure,
                }
                for c in self.parser.cookiejar.cookies
                if not c.is_expired()
                and c.domain.lstrip(".") in ("douyin.com", "www.douyin.com")
            ]
            if cookies:
                await context.add_cookies(cookies)
            page = await context.new_page()

            # 阻止后台视频下载；图文页需加载图片后才会触发评论请求。
            async def route_request(route):
                if route.request.resource_type == "media":
                    await route.abort()
                else:
                    await route.continue_()

            await page.route("**/*", route_request)
            async with asyncio.timeout(35):
                async with page.expect_response(
                    lambda r: self._matches_response(r, vid), timeout=25000
                ) as event:
                    await page.goto(
                        f"https://www.douyin.com/{kind}/{vid}",
                        wait_until="domcontentloaded",
                        timeout=25000,
                    )
                response = await event.value
                # 图文页可能已内嵌首屏，首次网络请求从后续页开始；回到第 0 页取热评。
                if parse_qs(urlparse(response.url).query).get("cursor", ["0"]) != ["0"]:
                    payload = await page.evaluate(
                        self.FETCH_PAGE, {"original": response.url, "cursor": 0}
                    )
                else:
                    payload = await response.json()
                result = []
                seen = set()
                cursors = set()
                total = 0
                for page_no in range(self.MAX_PAGES):
                    items = self._validate_page(payload)
                    total = int(payload.get("total") or total)
                    for item in items:
                        comment = self._parse_comment(item)
                        if comment and comment["id"] not in seen:
                            seen.add(comment["id"])
                            result.append(comment)
                    if (
                        len(result) >= self.limit
                        or not payload.get("has_more")
                        or not items
                        or page_no + 1 == self.MAX_PAGES
                    ):
                        break
                    cursor = payload.get("cursor")
                    if cursor is None or cursor in cursors:
                        break
                    cursors.add(cursor)
                    try:
                        payload = await page.evaluate(
                            self.FETCH_PAGE,
                            {"original": response.url, "cursor": cursor},
                        )
                        self._validate_page(payload)
                    except Exception as e:  # noqa: BLE001 - 后续页失败仍可展示已有评论
                        logger.debug(
                            f"[抖音评论] 后续页不可用，保留已获取的评论: {type(e).__name__}"
                        )
                        break
                return result[: self.limit], total

    @staticmethod
    def _image_url(image) -> str:
        if not isinstance(image, dict):
            return ""
        for url in image.get("url_list") or []:
            if isinstance(url, str) and url.startswith(("https://", "http://")):
                return url
        return ""

    def _parse_comment(self, item: dict, *, nested=False) -> dict | None:
        if not isinstance(item, dict) or not item.get("cid"):
            return None
        pictures = [
            url
            for image in (item.get("image_list") or [])[:9]
            if (url := self._image_url(image.get("origin_url") or image))
        ]
        if emoji := self._image_url((item.get("sticker") or {}).get("static_url")):
            pictures.append(emoji)
        message = str(item.get("text") or "").strip()
        if not message and not pictures:
            return None
        user = item.get("user") or {}
        replies = []
        if self.show_replies and not nested:
            replies = [
                reply
                for raw in (item.get("reply_comment") or [])[:3]
                if (reply := self._parse_comment(raw, nested=True))
            ]
        return {
            "id": str(item["cid"]),
            "name": str(user.get("nickname") or "抖音用户"),
            "message": message[:500] + ("…" if len(message) > 500 else ""),
            "avatar_url": self._image_url(user.get("avatar_thumb")),
            "picture_urls": pictures,
            "likes": int(item.get("digg_count") or 0),
            "time": int(item.get("create_time") or 0),
            "location": str(item.get("ip_label") or ""),
            "author": item.get("label_text") == "作者",
            "liked": bool(item.get("is_author_digged")),
            "pinned": bool(item.get("stick_position")),
            "reply_count": int(item.get("reply_comment_total") or 0),
            "replies": replies,
        }

    @classmethod
    def _seed_comment(cls, comment: dict) -> dict:
        """图片链接每次都带新的签名参数, 去掉后再参与摘要, 否则缓存永远不命中"""
        return {
            **comment,
            "avatar_url": stable_url(comment["avatar_url"]),
            "picture_urls": [stable_url(u) for u in comment["picture_urls"]],
            "replies": [cls._seed_comment(r) for r in comment["replies"]],
        }

    async def _attach_assets(self, comments):
        async def download(url):
            if not url:
                return None
            try:
                path = await self.parser.downloader.download_img(
                    url,
                    headers={"Referer": "https://www.douyin.com/"},
                    proxy=self.parser.proxy,
                )
                return await asyncio.to_thread(image_data_uri, path)
            except Exception:  # noqa: BLE001 - 单张图片失败不影响评论文字
                return None

        all_comments = [c for root in comments for c in [root, *root["replies"]]]
        assets = await download_comment_assets(
            [
                url
                for c in all_comments
                for url in [c["avatar_url"], *c["picture_urls"]]
            ],
            download,
        )
        for comment in all_comments:
            comment["avatar"] = assets.get(comment["avatar_url"])
            comment["pictures"] = [
                uri for url in comment["picture_urls"] if (uri := assets.get(url))
            ]
            comment["date"] = (
                datetime.fromtimestamp(
                    comment["time"], self.parser.cfg.timezone
                ).strftime("%Y-%m-%d")
                if comment["time"]
                else ""
            )

    async def _build_image(
        self, vid: str, title: str, author: str, *, kind: str = "video"
    ):
        try:
            return await self.image_cache.get(
                (vid, kind, title, author, self.limit, self.show_replies),
                lambda: self._render_image(vid, title, author, kind=kind),
            )
        except DownloadLimitException:
            raise
        except Exception as e:
            logger.warning(
                f"[抖音评论] 跳过评论区 ({type(e).__name__}): {str(e)[:200]}"
            )
            raise DownloadLimitException("抖音评论区暂不可用") from e

    async def _render_image(self, vid, title, author, *, kind):
        started = monotonic()
        stage = "获取评论"
        try:
            async with self._lock:
                comments, total = await self._fetch_comments(vid, kind=kind)
            fetched = monotonic()
            if not comments:
                raise DownloadLimitException("抖音评论区为空或不可见")
            seed = json.dumps(
                [
                    2,
                    title,
                    author,
                    total,
                    self.show_replies,
                    [self._seed_comment(c) for c in comments],
                ],
                ensure_ascii=False,
                sort_keys=True,
            )
            digest = hashlib.sha256(seed.encode()).hexdigest()[:16]
            path = self.parser.cfg.cache_dir / f"douyin_comments_{vid}_{digest}.jpg"
            if self.image_cache.is_cached(path):
                return path
            stage = "下载配图"
            await self._attach_assets(comments)
            attached = monotonic()
            stage = "截图"
            result = await self.parser.html_renderer.render(
                "douyin_comments.html",
                {
                    "width": 720,
                    "title": title,
                    "author": author,
                    "total": total,
                    "comments": comments,
                    "show_replies": self.show_replies,
                },
                path,
            )
            logger.debug(
                f"[抖音评论] {vid} 获取 {fetched - started:.2f}s / "
                f"配图 {attached - fetched:.2f}s / 截图 {monotonic() - attached:.2f}s"
            )
            return result
        finally:
            logger.debug(
                f"[抖音评论] {vid} 结束于{stage}，耗时 {monotonic() - started:.2f}s"
            )
