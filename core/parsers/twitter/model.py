"""fxtwitter 接口 api.fxtwitter.com/status/{id} 返回的推文结构"""

from __future__ import annotations

from msgspec import Struct


class Verification(Struct):
    verified: bool = False
    type: str | None = None
    """individual 蓝标 / business 金标 / government 灰标"""


class User(Struct):
    name: str
    screen_name: str
    avatar_url: str | None = None
    verification: Verification | None = None

    @property
    def badge(self) -> str | None:
        v = self.verification
        if not (v and v.verified):
            return None
        return {"business": "gold", "government": "gray"}.get(v.type or "", "blue")


class Facet(Struct):
    type: str
    original: str | None = None
    replacement: str | None = None
    display: str | None = None


class RawText(Struct):
    facets: list[Facet] = []


class Variant(Struct):
    url: str
    bitrate: int = 0
    content_type: str = ""


class Media(Struct):
    type: str
    """photo / video / gif"""
    url: str
    thumbnail_url: str | None = None
    duration: float = 0.0
    variants: list[Variant] = []

    @property
    def preview_url(self) -> str:
        return self.thumbnail_url or self.url

    def video_url(self, max_bytes: int) -> str:
        """不超过体积上限的最高码率 mp4; 都超了就给原链, 交给下载器报超限"""
        best = self.url
        for v in sorted(self.variants, key=lambda v: v.bitrate):
            if (
                v.content_type == "video/mp4"
                and v.bitrate * self.duration / 8 <= max_bytes
            ):
                best = v.url
        return best


class MediaSet(Struct):
    all: list[Media] = []


class Tweet(Struct):
    url: str
    id: str
    text: str
    author: User
    created_timestamp: int
    replies: int = 0
    retweets: int = 0
    likes: int = 0
    bookmarks: int = 0
    views: int | None = None
    replying_to: str | None = None
    raw_text: RawText | None = None
    media: MediaSet | None = None
    quote: Tweet | None = None

    @property
    def medias(self) -> list[Media]:
        return self.media.all if self.media else []

    @property
    def facets(self) -> list[Facet]:
        return self.raw_text.facets if self.raw_text else []

    @property
    def clean_text(self) -> str:
        """去掉正文末尾指向配图的 t.co 短链"""
        text = self.text
        for facet in self.facets:
            if facet.type == "media" and facet.original:
                text = text.replace(facet.original, "")
        return text.strip()


class Response(Struct):
    code: int
    message: str = ""
    tweet: Tweet | None = None
