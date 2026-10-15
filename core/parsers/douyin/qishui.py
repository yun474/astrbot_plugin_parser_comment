"""汽水音乐单曲解析

抖音里分享的歌曲短链会跳到 music.douyin.com/qishui/share/track, 分享页 SSR 出来的
_ROUTER_DATA 里直接带完整音频直链、歌曲信息和封面, 不需要登录。
"""

import json
import re
from typing import TYPE_CHECKING

import msgspec
from aiohttp import ClientError
from msgspec import Struct, field

from ...data import AudioContent, ParseResult, Platform, SendGroup
from ..base import ParseException

if TYPE_CHECKING:
    from . import DouyinParser

PLATFORM = Platform(name="qishui", display_name="汽水音乐")
SHARE_URL = "https://music.douyin.com/qishui/share/track?track_id={track_id}"
ROUTER_DATA_PATTERN = re.compile(r"_ROUTER_DATA\s*=\s*")


class ImageUrl(Struct):
    uri: str = ""
    urls: list[str] = field(default_factory=list)

    def sized(self, size: int) -> str | None:
        """图床地址是 前缀 + uri + 缩放后缀, 按需拼出指定尺寸的 jpg"""
        if not self.uri or not self.urls:
            return None
        return f"{self.urls[0]}{self.uri}~c5_{size}x{size}.jpg"


class Artist(Struct):
    name: str = ""
    url_avatar: ImageUrl | None = None


class Album(Struct):
    name: str = ""
    release_date: int = 0
    """发行时间戳, 单位: 秒"""
    url_cover: ImageUrl | None = None


class TrackStats(Struct):
    count_collected: int = 0
    count_comment: int = 0
    count_shared: int = 0


class TrackInfo(Struct):
    name: str = ""
    duration: int = 0
    """时长, 单位: 毫秒"""
    album: Album | None = None
    artists: list[Artist] = field(default_factory=list)
    stats: TrackStats | None = None


class AudioOption(Struct):
    status_code: int = 0
    has_copyright: bool = field(name="hasCopyright", default=True)
    url: str = ""
    encrypt: bool = False
    duration: float = 0
    """时长, 单位: 秒"""
    preview_start: float = field(name="previewStart", default=0)
    preview_end: float = field(name="previewEnd", default=0)
    track_name: str = field(name="trackName", default="")
    artist_name: str = field(name="artistName", default="")
    cover_url: str = field(name="coverURL", default="")
    track_info: TrackInfo | None = field(name="trackInfo", default=None)

    @property
    def playable_seconds(self) -> float:
        """可播放的时长; 会员歌曲只给一段试听时比完整时长短"""
        preview = self.preview_end - self.preview_start
        return preview if preview > 0 else self.duration

    @property
    def is_preview(self) -> bool:
        return 0 < self.playable_seconds < self.duration - 1


class TrackPage(Struct):
    audio: AudioOption | None = field(name="audioWithLyricsOption", default=None)


class LoaderData(Struct):
    track_page: TrackPage | None = None


class RouterData(Struct):
    loader_data: LoaderData = field(name="loaderData", default_factory=LoaderData)


def fmt_count(value: int) -> str:
    return (
        f"{value / 10000:.1f}万".replace(".0万", "万") if value >= 10000 else str(value)
    )


def extract_audio_option(html: str) -> AudioOption:
    """分享页里 _ROUTER_DATA 后面紧跟着别的脚本, 只解码第一个 JSON 对象"""
    matched = ROUTER_DATA_PATTERN.search(html)
    if not matched:
        raise ParseException("汽水音乐分享页未包含 _ROUTER_DATA")
    try:
        raw, _ = json.JSONDecoder().raw_decode(html, matched.end())
        router = msgspec.convert(raw, RouterData)
    except (ValueError, msgspec.ValidationError) as e:
        raise ParseException(f"汽水音乐分享页数据解析失败: {e}") from e

    page = router.loader_data.track_page
    audio = page.audio if page else None
    if audio is None or audio.status_code != 0 or audio.track_info is None:
        raise ParseException("汽水音乐歌曲不存在或已下架")
    if not audio.has_copyright:
        raise ParseException("汽水音乐歌曲暂无版权")
    if audio.encrypt or not audio.url:
        raise ParseException("汽水音乐歌曲音频已加密或没有播放地址, 暂不支持")
    return audio


async def parse_track(parser: "DouyinParser", track_id: str):
    url = SHARE_URL.format(track_id=track_id)
    headers = parser.ios_headers.copy()
    headers.pop("Cookie", None)
    try:
        async with parser.session.get(url, headers=headers) as resp:
            if resp.status != 200:
                raise ParseException(f"汽水音乐分享页 status: {resp.status}")
            html = await resp.text()
    except ClientError as e:
        raise ParseException(f"汽水音乐分享页请求失败: {e}") from e

    audio = extract_audio_option(html)
    track = audio.track_info or TrackInfo()
    album = track.album or Album()
    artist = track.artists[0] if track.artists else Artist()

    title = track.name or audio.track_name
    artist_name = " / ".join(a.name for a in track.artists if a.name) or (
        audio.artist_name
    )
    cover_url = (album.url_cover and album.url_cover.sized(720)) or audio.cover_url
    avatar_url = artist.url_avatar.sized(300) if artist.url_avatar else None

    lines = []
    if album.name:
        lines.append(f"专辑：{album.name}")
    if stats := track.stats:
        lines.append(
            f"收藏 {fmt_count(stats.count_collected)} · "
            f"评论 {fmt_count(stats.count_comment)} · "
            f"分享 {fmt_count(stats.count_shared)}"
        )

    cover_task = (
        parser.downloader.download_img(cover_url, headers=headers, proxy=parser.proxy)
        if cover_url
        else None
    )
    if audio.is_preview:
        lines.append(f"会员歌曲, 仅能获取 {audio.playable_seconds:.0f} 秒试听片段")
    audio_task = parser.downloader.download_audio(
        audio.url,
        # 直链路径里带签名, 按歌曲 ID 命名才能复用缓存
        audio_name=f"qishui_{track_id}{'_preview' if audio.is_preview else ''}.m4a",
        headers=headers,
        proxy=parser.proxy,
    )
    audio_content = AudioContent(audio_task, audio.playable_seconds, cover_task)

    return ParseResult(
        platform=PLATFORM,
        title=title,
        text="\n".join(lines) or None,
        author=parser.create_author(artist_name, avatar_url, headers=headers),
        contents=[audio_content],
        # 光一个音频文件看不出是哪首歌, 固定先发信息卡片
        send_groups=[
            SendGroup(contents=[audio_content], force_merge=False, render_card=True)
        ],
        timestamp=album.release_date or None,
        url=url,
    )
