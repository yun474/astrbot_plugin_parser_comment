"""X 推文卡片: 头像 + 昵称认证 + 正文 + 配图宫格 + 引用推文 + 发布时间 / 浏览 + 互动数据"""

from __future__ import annotations

import asyncio
import re
from asyncio import Task
from datetime import datetime
from pathlib import Path

from astrbot.api import logger
from markupsafe import Markup, escape

from ...data import ImageContent
from ...exception import DownloadLimitException
from ...html_render import image_data_uri
from ..bilibili.common import fmt_count, fmt_duration, settle_path
from .model import Tweet


class TweetCardRenderer:
    WIDTH = 640
    TEMPLATE = "x_tweet.html"
    _ENTITY_RE = re.compile(r"https?://\S+|(?<!\w)[@#]\w+")

    def __init__(self, parser):
        self.parser = parser

    def build(
        self,
        tweet: Tweet,
        avatar: Path | Task[Path] | None,
        previews: dict[str, Task[Path] | None],
    ) -> ImageContent:
        """previews: 媒体链接 -> 已在下载的图片 / 视频封面任务, 卡片和正文共用"""
        task = asyncio.create_task(
            self._render(tweet, avatar, previews), name=f"x_card_{tweet.id}"
        )
        task.add_done_callback(lambda t: t.cancelled() or t.exception())
        return ImageContent(task)

    async def _render(self, tweet: Tweet, avatar, previews) -> Path:
        quote = tweet.quote
        medias = [*tweet.medias, *(quote.medias if quote else [])]
        try:
            paths = await asyncio.gather(
                settle_path(avatar),
                settle_path(self._download(quote.author.avatar_url) if quote else None),
                *(
                    settle_path(previews.get(m.url) or self._download(m.preview_url))
                    for m in medias
                ),
            )
            avatar_uri, quote_avatar_uri, *media_uris = await asyncio.to_thread(
                lambda: [image_data_uri(p) for p in paths]
            )
            srcs = dict(zip((m.url for m in medias), media_uris))
            context = {
                "width": self.WIDTH,
                "tweet": self._tweet_context(tweet, avatar_uri, srcs),
                "quote": self._tweet_context(quote, quote_avatar_uri, srcs)
                if quote
                else None,
            }
            return await self.parser.html_renderer.render_cached(
                self.TEMPLATE, context, f"x_tweet_{tweet.id}"
            )
        except Exception as e:
            logger.warning(f"[推特] 卡片渲染失败 ({type(e).__name__}): {str(e)[:200]}")
            raise DownloadLimitException("推特卡片渲染失败") from e

    def _download(self, url: str | None) -> Task[Path] | None:
        if not url:
            return None
        return self.parser.downloader.download_img(
            url, headers=self.parser.headers, proxy=self.parser.proxy
        )

    def _tweet_context(self, tweet: Tweet, avatar: str | None, srcs: dict) -> dict:
        user = tweet.author
        moment = datetime.fromtimestamp(
            tweet.created_timestamp, self.parser.cfg.timezone
        )
        date = f"{moment.year}年{moment.month}月{moment.day}日"
        noon = "上午" if moment.hour < 12 else "下午"
        return {
            "name": user.name,
            "handle": user.screen_name,
            "avatar": avatar,
            "badge": user.badge,
            "reply_to": tweet.replying_to,
            "text": self._text_html(tweet),
            # 推特一条最多 4 个媒体, 宫格按数量排版
            "media": [
                {
                    "type": m.type,
                    "src": srcs.get(m.url),
                    "duration": fmt_duration(m.duration).removeprefix("0"),
                }
                for m in tweet.medias[:4]
            ],
            "date": date,
            "time": f"{noon}{moment.hour % 12 or 12}:{moment.minute:02d} · {date}",
            "views": fmt_count(tweet.views) if tweet.views else "",
            "stats": [
                ("reply", fmt_count(tweet.replies)),
                ("retweet", fmt_count(tweet.retweets)),
                ("like", fmt_count(tweet.likes)),
                ("bookmark", fmt_count(tweet.bookmarks)),
            ],
        }

    @classmethod
    def _text_html(cls, tweet: Tweet) -> Markup:
        """链接、@ 和话题标蓝, 链接显示成推特缩略后的样子"""
        text = tweet.clean_text
        display = {
            f.replacement: f.display
            for f in tweet.facets
            if f.type == "url" and f.replacement and f.display
        }
        parts: list[str] = []
        pos = 0
        for match in cls._ENTITY_RE.finditer(text):
            token = match.group()
            parts.append(str(escape(text[pos : match.start()])))
            parts.append(
                f'<span class="link">{escape(display.get(token, token))}</span>'
            )
            pos = match.end()
        parts.append(str(escape(text[pos:])))
        return Markup("".join(parts))
