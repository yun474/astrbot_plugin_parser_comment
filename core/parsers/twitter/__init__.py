import re
from asyncio import Task
from itertools import chain
from pathlib import Path
from typing import ClassVar

import msgspec
from aiohttp import ClientError

from ...config import PluginConfig
from ...data import MediaContent, ParseResult, Platform, SendGroup
from ...download import Downloader
from ...exception import ParseException
from ..base import BaseParser, handle
from .card import TweetCardRenderer
from .model import Media, Response, Tweet


class TwitterParser(BaseParser):
    platform: ClassVar[Platform] = Platform(name="twitter", display_name="推特")
    # fxtwitter 免登录返回推文全文、作者、互动数据和媒体直链, 纯文字推文也能拿到
    API: ClassVar[str] = "https://api.fxtwitter.com/status/{}"
    ERRORS: ClassVar[dict[str, str]] = {
        "NOT_FOUND": "推文不存在或已被删除",
        "PRIVATE_TWEET": "推文来自受保护的账号, 无法解析",
    }

    def __init__(self, config: PluginConfig, downloader: Downloader):
        super().__init__(config, downloader)
        self.mycfg = config.parser.twitter
        self.card = (
            TweetCardRenderer(self)
            if self.mycfg.poster_render_enable is not False
            else None
        )

    @handle(
        "twitter.com",
        (
            r"(?<![A-Za-z0-9.-])(?:(?:www|mobile)\.)?twitter\.com/"
            r"(?:[A-Za-z0-9_]+/)*status/(?P<id>\d+)"
        ),
    )
    @handle(
        "x.com",
        (
            r"(?<![A-Za-z0-9.-])(?:www\.)?x\.com/"
            r"(?:[A-Za-z0-9_]+/)*status/(?P<id>\d+)"
        ),
    )
    async def _parse(self, searched: re.Match[str]) -> ParseResult:
        async with self.session.get(
            self.API.format(searched["id"]), headers=self.headers
        ) as resp:
            if resp.status >= 500:
                raise ClientError(f"fxtwitter API {resp.status} {resp.reason}")
            data = msgspec.json.decode(await resp.read(), type=Response)
        if data.tweet is None:
            raise ParseException(
                self.ERRORS.get(data.message, f"推文获取失败: {data.message}")
            )
        return self._build_result(data.tweet)

    def _build_result(self, tweet: Tweet) -> ParseResult:
        contents: list[MediaContent] = []
        # 媒体链接 -> 图片 / 视频封面的下载任务, 卡片直接复用, 同一张图不下两遍
        previews: dict[str, Task[Path] | None] = {}
        quote_medias = tweet.quote.medias if tweet.quote else []
        for media in chain(tweet.medias, quote_medias):
            content, previews[media.url] = self._media_content(media)
            contents.append(content)

        author = self.create_author(tweet.author.name, tweet.author.avatar_url)
        send_groups = []
        if self.card is not None:
            card = self.card.build(tweet, author.avatar, previews)
            send_groups = [
                SendGroup(contents=[card], force_merge=False, render_card=False)
            ]
            if contents:
                send_groups.append(SendGroup(contents=contents, render_card=False))

        return self.result(
            url=tweet.url,
            text=tweet.clean_text or None,
            author=author,
            contents=contents,
            send_groups=send_groups,
            timestamp=tweet.created_timestamp,
        )

    def _media_content(self, media: Media):
        match media.type:
            case "photo":
                image = self.create_image_contents([media.url])[0]
                return image, image.path_task
            case "gif":
                return self.create_dynamic_contents([media.url])[0], None
            case _:
                video = self.create_video_content(
                    media.video_url(self.cfg.max_size),
                    media.thumbnail_url,
                    media.duration,
                )
                return video, video.cover
