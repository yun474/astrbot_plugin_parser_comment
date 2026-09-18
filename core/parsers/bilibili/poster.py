"""B站视频海报: 封面 + 标题 + UP 主 + 播放/弹幕/点赞/投币/收藏/转发/评论 + 简介"""

import asyncio
import uuid
from asyncio import Task
from datetime import datetime, tzinfo
from pathlib import Path

from ...html_render import HtmlRenderer, image_data_uri
from .common import fmt_count, fmt_duration, settle_path
from .video import PageInfo, VideoInfo


class BiliPosterRenderer:
    WIDTH = 720
    TEMPLATE = "bili_video.html"

    def __init__(self, html: HtmlRenderer, cache_dir: Path, tz: tzinfo):
        self.html = html
        self.cache_dir = cache_dir
        self.tz = tz

    async def render(
        self,
        video: VideoInfo,
        page: PageInfo,
        *,
        cover: Path | Task[Path] | None,
        avatar: Path | Task[Path] | None,
        url: str,
        ai_summary: str = "",
    ) -> Path:
        cover_path, avatar_path = await asyncio.gather(
            settle_path(cover), settle_path(avatar)
        )
        context = await asyncio.to_thread(
            self._context, video, page, cover_path, avatar_path, url, ai_summary
        )
        out_path = (
            self.cache_dir / f"bili_poster_{video.bvid}_{uuid.uuid4().hex[:8]}.jpg"
        )
        return await self.html.render(self.TEMPLATE, context, out_path)

    def _context(
        self,
        video: VideoInfo,
        page: PageInfo,
        cover_path: Path | None,
        avatar_path: Path | None,
        url: str,
        ai_summary: str,
    ) -> dict:
        stat = video.stat
        desc = video.desc.strip()
        page_count = len(video.pages or [])
        return {
            "width": self.WIDTH,
            "cover": image_data_uri(cover_path),
            "avatar": image_data_uri(avatar_path),
            "title": page.title,
            "duration": fmt_duration(page.duration),
            "up_name": video.owner.name,
            "pubdate": datetime.fromtimestamp(page.timestamp, self.tz).strftime(
                "%Y-%m-%d %H:%M"
            ),
            "tname": video.tname,
            "page_label": f"P{page.index + 1}/{page_count}" if page_count > 1 else "",
            "bvid": video.bvid,
            "stats": [
                ("play", "播放", fmt_count(stat.view)),
                ("danmaku", "弹幕", fmt_count(stat.danmaku)),
                ("like", "点赞", fmt_count(stat.like)),
                ("coin", "投币", fmt_count(stat.coin)),
                ("favorite", "收藏", fmt_count(stat.favorite)),
                ("share", "转发", fmt_count(stat.share)),
                ("reply", "评论", fmt_count(stat.reply)),
            ],
            # B站没写简介时给的占位符
            "desc": "" if desc == "-" else desc,
            "honors": video.honors,
            "ai_summary": ai_summary.removeprefix("AI总结:").strip()
            if ai_summary.startswith("AI总结:")
            else "",
            "url": url,
        }
