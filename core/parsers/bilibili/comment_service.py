from __future__ import annotations

import asyncio
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from aiohttp import ClientTimeout
from msgspec import json as msgjson

from astrbot.api import logger

from ...data import ImageContent
from ...exception import DownloadLimitException
from .comment_renderer import BiliCommentRenderer, BiliCommentRenderItem


@dataclass(slots=True)
class _RawComment:
    rpid: str
    uname: str
    avatar_url: str
    message: str
    pic_url: str | None = None
    avatar_path: Path | None = None
    pic_path: Path | None = None


class BiliCommentService:
    """Fetch, filter, and render Bilibili comments.

    移植说明：这里把 R 版评论区功能适配到 Zhalslar 原版解析器。
    返回值是带后台任务的 ImageContent，让 parse_video 立刻返回，
    主视频解析/下载链路仍然保持原版逻辑。
    """

    API_URL = "https://api.bilibili.com/x/v2/reply/main"
    COMMENT_TYPE_VIDEO = 1
    MAX_FETCH_PAGES = 5
    COMMENT_PAGE_SIZE = 20

    def __init__(
        self,
        parser,
        renderer: BiliCommentRenderer,
        *,
        enabled: bool = True,
        comment_limit: int = 9,
        enable_text_ad_filter: bool = True,
        enable_qr_filter: bool = False,
        qr_check_max: int = 4,
        fetch_timeout: float = 8.0,
    ):
        self.parser = parser
        self.renderer = renderer
        self.enabled = enabled
        self.comment_limit = max(0, min(int(comment_limit or 0), 20))
        self.enable_text_ad_filter = enable_text_ad_filter
        self.enable_qr_filter = enable_qr_filter
        self.qr_check_max = max(0, int(qr_check_max or 0))
        self.fetch_timeout = fetch_timeout

        self._ad_kw_re = re.compile(
            r"(微信|v信|vx|加微|私信|进群|福利|代理|兼职|看片|资源|加我|联系我|返利|推广|引流|合作)",
            re.IGNORECASE,
        )
        self._contact_re = re.compile(
            r"(wx[:：]?\s*[a-zA-Z][-_a-zA-Z0-9]{4,}|qq[:：]?\s*\d{5,}|tg[:：]?\s*[a-zA-Z0-9_]{4,})",
            re.IGNORECASE,
        )
        self._shortlink_re = re.compile(
            r"(https?://)?([a-zA-Z0-9-]+\.)?(t\.cn|u\.jd\.com|dwz\.cn|v\.douyin\.com|b23\.tv)/",
            re.IGNORECASE,
        )
        self._qr_detect_cache: dict[str, bool] = {}

    @property
    def headers(self) -> dict[str, str]:
        headers = self.parser.headers.copy()
        cookies = getattr(self.parser.mycfg, "cookies", None)
        if cookies:
            headers["Cookie"] = str(cookies).strip()
        return headers

    def build_comment_image_content(
        self,
        oid: int,
        type_: int = COMMENT_TYPE_VIDEO,
        *,
        video_title: str,
        video_cover: str | None,
    ) -> list[ImageContent]:
        if not self.enabled or self.comment_limit <= 0:
            return []

        task = asyncio.create_task(
            self._build_comment_image(
                oid,
                type_,
                video_title=video_title,
                video_cover=video_cover,
            ),
            name=f"bili_comment_render_{oid}",
        )
        return [ImageContent(task)]

    @staticmethod
    def _normalise_url(url: str | None) -> str | None:
        if not url:
            return None
        if url.startswith("//"):
            return f"https:{url}"
        return url

    def _is_ad_like_text(self, text: str) -> bool:
        if not text:
            return False
        return bool(
            self._ad_kw_re.search(text)
            or self._contact_re.search(text)
            or self._shortlink_re.search(text)
        )

    @staticmethod
    def _clean_message(raw_msg: str) -> str:
        message = re.sub(r"\[.*?\]", "", raw_msg or "").strip()
        if len(message) > 320:
            message = f"{message[:320].rstrip()}..."
        return message

    async def _has_qr_in_image(self, img_url: str) -> bool:
        if img_url in self._qr_detect_cache:
            return self._qr_detect_cache[img_url]

        if len(self._qr_detect_cache) > 512:
            self._qr_detect_cache.clear()

        try:
            async with self.parser.session.get(
                img_url,
                headers=self.headers,
                proxy=self.parser.proxy,
                timeout=ClientTimeout(total=self.fetch_timeout),
            ) as resp:
                if resp.status != 200:
                    self._qr_detect_cache[img_url] = False
                    return False
                body = await resp.read()

            try:
                import cv2
                import numpy as np

                arr = np.frombuffer(body, dtype=np.uint8)
                img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
                if img is None:
                    self._qr_detect_cache[img_url] = False
                    return False
                detector = cv2.QRCodeDetector()
                decoded, points, _ = detector.detectAndDecode(img)
                has_qr = bool(decoded) or points is not None
            except Exception:
                # 二维码过滤是可选增强：缺 cv2/numpy 时不能影响评论图。
                has_qr = False

            self._qr_detect_cache[img_url] = has_qr
            return has_qr
        except Exception:
            self._qr_detect_cache[img_url] = False
            return False

    async def _should_skip_comment(
        self,
        message: str,
        pic_url: str | None,
        qr_check_counter: list[int],
    ) -> bool:
        if self.enable_text_ad_filter and self._is_ad_like_text(message):
            return True

        if (
            self.enable_qr_filter
            and pic_url
            and qr_check_counter[0] < self.qr_check_max
            and (not message or len(message.strip()) <= 8)
        ):
            qr_check_counter[0] += 1
            if await self._has_qr_in_image(pic_url):
                return True

        return False

    async def _fetch_comments(self, oid: int, type_: int) -> list[_RawComment]:
        strict_list: list[_RawComment] = []
        relaxed_list: list[_RawComment] = []
        seen: set[str] = set()
        next_cursor = 0
        is_end = False
        qr_check_counter = [0]

        for _ in range(self.MAX_FETCH_PAGES):
            if is_end or len(strict_list) >= self.comment_limit:
                break

            params = {
                "oid": oid,
                "type": type_,
                "mode": 3,
                "next": next_cursor,
                "ps": self.COMMENT_PAGE_SIZE,
            }

            try:
                async with self.parser.session.get(
                    self.API_URL,
                    params=params,
                    headers=self.headers,
                    proxy=self.parser.proxy,
                    timeout=ClientTimeout(total=self.fetch_timeout),
                ) as resp:
                    if resp.status != 200:
                        break
                    payload = msgjson.decode(await resp.read())

                if payload.get("code") != 0:
                    break

                block = payload.get("data") or {}
                replies = block.get("replies") or []
                cursor = block.get("cursor") or {}
                is_end = bool(cursor.get("is_end"))
                next_cursor = cursor.get("next", next_cursor + 1)

                for item in replies:
                    rpid = str(item.get("rpid") or "")
                    if not rpid or rpid in seen:
                        continue
                    seen.add(rpid)

                    content = item.get("content") or {}
                    member = item.get("member") or {}
                    message = self._clean_message(content.get("message") or "")

                    pics = content.get("pictures") or []
                    pic_url = self._normalise_url(pics[0].get("img_src")) if pics else None
                    avatar_url = self._normalise_url(member.get("avatar")) or ""

                    if not message and not pic_url:
                        continue

                    if await self._should_skip_comment(message, pic_url, qr_check_counter):
                        continue

                    comment = _RawComment(
                        rpid=rpid,
                        uname=str(member.get("uname") or "B站用户"),
                        avatar_url=avatar_url,
                        message=message,
                        pic_url=pic_url,
                    )

                    if "@" in message:
                        relaxed_list.append(comment)
                    else:
                        strict_list.append(comment)
            except Exception as e:
                logger.warning(f"[Bilibili-comments] 评论抓取失败: {e}")
                break

        if len(strict_list) < self.comment_limit:
            strict_list.extend(relaxed_list[: self.comment_limit - len(strict_list)])

        return strict_list[: self.comment_limit]

    async def _download_image(self, url: str | None) -> Path | None:
        url = self._normalise_url(url)
        if not url:
            return None
        try:
            return await self.parser.downloader.download_img(
                url,
                headers=self.headers,
                proxy=self.parser.proxy,
            )
        except Exception as e:
            logger.debug(f"[Bilibili-comments] 图片下载失败: {url}, {e}")
            return None

    async def _attach_assets(
        self,
        comments: list[_RawComment],
        video_cover: str | None,
    ) -> Path | None:
        cover_task = self._download_image(video_cover)
        asset_jobs = []
        for comment in comments:
            asset_jobs.append(("avatar", comment, self._download_image(comment.avatar_url)))
            if comment.pic_url:
                asset_jobs.append(("pic", comment, self._download_image(comment.pic_url)))

        cover_result, asset_results = await asyncio.gather(
            cover_task,
            asyncio.gather(*(job[2] for job in asset_jobs), return_exceptions=True),
        )

        for (kind, comment, _), result in zip(asset_jobs, asset_results):
            if not isinstance(result, Path):
                continue
            if kind == "avatar":
                comment.avatar_path = result
            elif kind == "pic":
                comment.pic_path = result

        return cover_result if isinstance(cover_result, Path) else None

    @staticmethod
    def _cache_key(oid: int, comments: list[_RawComment], limit: int) -> str:
        seed = "|".join(f"{c.rpid}:{c.message}:{c.pic_url or ''}" for c in comments)
        return hashlib.md5(f"{oid}:{limit}:{seed}".encode("utf-8")).hexdigest()[:10]

    async def _build_comment_image(
        self,
        oid: int,
        type_: int,
        *,
        video_title: str,
        video_cover: str | None,
    ) -> Path:
        try:
            comments = await self._fetch_comments(oid, type_)
            if not comments:
                raise DownloadLimitException("评论区为空或不可见")

            cache_name = f"bili_comments_{oid}_{self._cache_key(oid, comments, self.comment_limit)}.png"
            out_path = self.parser.cfg.cache_dir / cache_name
            if out_path.exists() and out_path.stat().st_size > 100:
                return out_path

            cover_path = await self._attach_assets(comments, video_cover)
            render_items = [
                BiliCommentRenderItem(
                    uname=c.uname,
                    message=c.message,
                    avatar_path=c.avatar_path,
                    pic_path=c.pic_path,
                )
                for c in comments
            ]
            return await self.renderer.render_merged_comments(
                out_path=out_path,
                comments=render_items,
                video_title=video_title,
                cover_path=cover_path,
            )
        except DownloadLimitException:
            raise
        except Exception as e:
            logger.warning(f"[Bilibili-comments] 评论区渲染跳过: {e}")
            raise DownloadLimitException("评论区渲染失败") from e
