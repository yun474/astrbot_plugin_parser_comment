"""油管视频卡片: 封面 + 标题 + 频道 / 订阅数 + 点赞评论 + 观看数日期简介框"""

from __future__ import annotations

import asyncio
import uuid
from asyncio import Task
from pathlib import Path

from astrbot.api import logger

from ...data import ImageContent
from ...download import VideoInfo
from ...exception import DownloadLimitException
from ...html_render import image_data_uri
from ..bilibili.common import fmt_count, fmt_duration, settle_path


class YouTubeCardRenderer:
    WIDTH = 720
    TEMPLATE = "yt_video.html"

    def __init__(self, parser):
        self.parser = parser

    def build(
        self,
        info: VideoInfo,
        avatar: Path | Task[Path] | None,
        cover: Path | Task[Path] | None,
    ) -> ImageContent:
        task = asyncio.create_task(self._render(info, avatar, cover), name="yt_card")
        task.add_done_callback(lambda t: t.cancelled() or t.exception())
        return ImageContent(task)

    async def _render(self, info: VideoInfo, avatar, cover) -> Path:
        try:
            avatar_path, cover_path = await asyncio.gather(
                settle_path(avatar), settle_path(cover)
            )
            context = await asyncio.to_thread(
                self._context, info, avatar_path, cover_path
            )
            out_path = self.parser.cfg.cache_dir / f"yt_video_{uuid.uuid4().hex}.jpg"
            return await self.parser.html_renderer.render(
                self.TEMPLATE, context, out_path
            )
        except Exception as e:
            logger.warning(f"[油管] 卡片渲染失败 ({type(e).__name__}): {str(e)[:200]}")
            raise DownloadLimitException("油管卡片渲染失败") from e

    def _context(self, info: VideoInfo, avatar_path, cover_path) -> dict:
        date = info.upload_date or ""
        url = info.webpage_url or ""
        return {
            "width": self.WIDTH,
            "cover": image_data_uri(cover_path),
            "avatar": image_data_uri(avatar_path),
            "shorts": "/shorts/" in url,
            "duration": fmt_duration(info.duration).removeprefix("0"),
            "title": info.title,
            "channel": info.channel,
            "verified": bool(info.channel_is_verified),
            "subscribers": f"{fmt_count(info.channel_follower_count)}位订阅者"
            if info.channel_follower_count
            else info.uploader_id or "",
            "likes": fmt_count(info.like_count) if info.like_count else "",
            "comments": fmt_count(info.comment_count) if info.comment_count else "",
            "views": f"{fmt_count(info.view_count)}次观看"
            if info.view_count is not None
            else "",
            "date": f"{date[:4]}年{int(date[4:6])}月{int(date[6:])}日"
            if len(date) == 8
            else "",
            "desc": info.description.strip(),
            "url": url.removeprefix("https://").removeprefix("www."),
        }
