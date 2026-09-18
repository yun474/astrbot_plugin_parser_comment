from typing import Any
from urllib.parse import parse_qs, urlparse

from msgspec import Struct, field

from ..base import ParseException


class Avatar(Struct):
    url_list: list[str]


class Author(Struct):
    nickname: str
    avatar_thumb: Avatar | None = None
    avatar_medium: Avatar | None = None


class PlayAddr(Struct):
    uri: str | None = None
    url_list: list[str] = field(default_factory=list)


class Cover(Struct):
    url_list: list[str]


class Video(Struct):
    play_addr: PlayAddr
    cover: Cover
    duration: int
    """时长, 单位: 毫秒"""


class Image(Struct):
    video: Video | None = None
    url_list: list[str] = field(default_factory=list)


class VideoData(Struct):
    create_time: int
    author: Author
    desc: str
    images: list[Image] | None = None
    video: Video | None = None

    @property
    def image_url_lists(self) -> list[list[str]]:
        """每张图片的镜像地址列表, 顺序即优先级"""
        return [image.url_list for image in self.images] if self.images else []

    @property
    def video_urls(self) -> list[str]:
        """play_addr 里的直链, 去水印"""
        if not self.video:
            return []
        return [url.replace("playwm", "play") for url in self.video.play_addr.url_list]

    @property
    def play_token(self) -> str | None:
        if not self.video:
            return None

        play_addr = self.video.play_addr
        if play_addr.uri:
            return play_addr.uri

        for url in play_addr.url_list:
            query = parse_qs(urlparse(url).query)
            if video_id := query.get("video_id"):
                return video_id[0]
        return None

    @property
    def cover_url(self) -> str | None:
        if self.video and self.video.cover.url_list:
            return self.video.cover.url_list[0]
        return None

    @property
    def avatar_url(self) -> str | None:
        for avatar in (self.author.avatar_thumb, self.author.avatar_medium):
            if avatar and avatar.url_list:
                return avatar.url_list[0]
        return None


class FilterItem(Struct):
    """视频不可用时的原因说明"""

    filter_reason: str = ""
    notice: str = ""
    detail_msg: str = ""

    @property
    def message(self) -> str:
        return self.notice or self.detail_msg or self.filter_reason


class VideoInfoRes(Struct):
    item_list: list[VideoData] = field(default_factory=list)
    filter_list: list[FilterItem] = field(default_factory=list)

    @property
    def unavailable_reason(self) -> str | None:
        """视频被删除/屏蔽时平台给出的原因"""
        if self.filter_list:
            return self.filter_list[0].message or "unknown"
        return None

    @property
    def video_data(self) -> VideoData:
        if self.item_list:
            return self.item_list[0]
        if reason := self.unavailable_reason:
            raise ParseException(f"视频不可用: {reason}")
        raise ParseException("can't find data in videoInfoRes")


class VideoOrNotePage(Struct):
    video_info_res: VideoInfoRes = field(
        name="videoInfoRes", default_factory=VideoInfoRes
    )


class LoaderData(Struct):
    video_page: VideoOrNotePage | None = field(name="video_(id)/page", default=None)
    note_page: VideoOrNotePage | None = field(name="note_(id)/page", default=None)


class RouterData(Struct):
    loader_data: LoaderData = field(name="loaderData", default_factory=LoaderData)
    errors: dict[str, Any] | None = None

    @property
    def video_info_res(self) -> VideoInfoRes:
        if page := self.loader_data.video_page or self.loader_data.note_page:
            return page.video_info_res
        raise ParseException(
            "can't find video_(id)/page or note_(id)/page in router data"
        )

    @property
    def video_data(self) -> VideoData:
        return self.video_info_res.video_data
