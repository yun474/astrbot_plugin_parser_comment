"""B站个人空间: 用户资料 + 最近投稿, 渲染成空间卡片"""

import asyncio
from asyncio import Task
from datetime import datetime, tzinfo
from pathlib import Path

from msgspec import Struct, convert, field

from astrbot.api import logger

from ...data import ImageContent
from ...exception import DownloadLimitException
from ...html_render import HtmlRenderer, image_data_uri
from ..base import ParseException
from .common import bfs_thumb, fmt_count, settle_path

CARD_API_URL = "https://api.bilibili.com/x/web-interface/card"
SPACE_URL = "https://space.bilibili.com/{mid}"
RECENT_VIDEO_COUNT = 3


class LevelInfo(Struct):
    current_level: int = 0


class Official(Struct):
    role: int = 0
    """0 无认证; 1/2/7 个人认证; 3~6 机构认证"""
    title: str = ""


class VipLabel(Struct):
    text: str = ""


class Vip(Struct):
    status: int = 0
    label: VipLabel = field(default_factory=VipLabel)


class UserCard(Struct):
    mid: str
    name: str
    face: str = ""
    sign: str = ""
    sex: str = ""
    attention: int = 0
    """关注数"""
    level_info: LevelInfo = field(default_factory=LevelInfo)
    official: Official = field(name="Official", default_factory=Official)
    vip: Vip = field(default_factory=Vip)


class SpaceImage(Struct):
    l_img: str = ""
    """空间头图大图"""


class CardData(Struct):
    card: UserCard
    follower: int = 0
    archive_count: int = 0
    like_num: int = 0
    space: SpaceImage | None = None


class SpaceVideo(Struct):
    bvid: str
    title: str
    pic: str = ""
    length: str = ""
    """时长, 形如 03:21"""
    play: int | str = 0
    """播放数, 隐藏时为 "--" """
    created: int = 0


class SpaceInfo(Struct):
    user: CardData
    videos: list[SpaceVideo]

    @property
    def mid(self) -> str:
        return self.user.card.mid

    @property
    def url(self) -> str:
        return SPACE_URL.format(mid=self.mid)


async def fetch_space(parser, mid: int) -> SpaceInfo:
    """用户资料拿不到直接报错; 最近投稿接口风控比较严, 拿不到就不显示"""
    user, videos = await asyncio.gather(
        _fetch_card(parser, mid), _fetch_videos(parser, mid)
    )
    return SpaceInfo(user=user, videos=videos)


async def _fetch_card(parser, mid: int) -> CardData:
    web = parser.web
    headers, _ = await web.headers()
    async with web.session() as session:
        payload = await web.get_json(
            session, CARD_API_URL, {"mid": mid, "photo": "true"}, headers
        )
    if payload.get("code") != 0 or not payload.get("data"):
        message = payload.get("message") or "接口无响应"
        raise ParseException(f"B站用户信息获取失败: {message}")
    return convert(payload["data"], CardData)


async def _fetch_videos(parser, mid: int) -> list[SpaceVideo]:
    from bilibili_api.user import User

    try:
        user = User(mid, credential=await parser.login.credential)
        data = await user.get_videos(ps=RECENT_VIDEO_COUNT)
        vlist = ((data or {}).get("list") or {}).get("vlist") or []
        return convert(vlist[:RECENT_VIDEO_COUNT], list[SpaceVideo])
    except Exception as e:
        logger.info(
            f"[Bilibili] 用户 {mid} 最近投稿获取失败, 卡片不显示投稿: {str(e)[:120]}"
        )
        return []


class BiliSpaceRenderer:
    WIDTH = 720
    TEMPLATE = "bili_space.html"

    def __init__(self, html: HtmlRenderer, tz: tzinfo):
        self.html = html
        self.tz = tz

    def build(
        self,
        info: SpaceInfo,
        *,
        avatar: Path | Task[Path] | None,
        banner: Path | Task[Path] | None,
        covers: list[Path | Task[Path] | None],
    ) -> ImageContent:
        task = asyncio.create_task(
            self._render(info, avatar, banner, covers), name=f"bili_space_{info.mid}"
        )
        # 发送链路异常时可能没人 await 它, 标记异常已取回, 别让 asyncio 刷屏
        task.add_done_callback(lambda t: t.cancelled() or t.exception())
        return ImageContent(task)

    async def _render(self, info: SpaceInfo, avatar, banner, covers) -> Path:
        try:
            avatar_path, banner_path, *cover_paths = await asyncio.gather(
                settle_path(avatar), settle_path(banner), *map(settle_path, covers)
            )
            context = await asyncio.to_thread(
                self._context, info, avatar_path, banner_path, cover_paths
            )
            return await self.html.render_cached(
                self.TEMPLATE, context, f"bili_space_{info.mid}"
            )
        except Exception as e:
            logger.warning(f"[Bilibili] 空间卡片渲染失败 ({type(e).__name__}): {e}")
            raise DownloadLimitException("B站空间卡片渲染失败") from e

    def _context(
        self,
        info: SpaceInfo,
        avatar_path: Path | None,
        banner_path: Path | None,
        cover_paths: list[Path | None],
    ) -> dict:
        data = info.user
        card = data.card
        official = card.official
        vip_text = card.vip.label.text if card.vip.status == 1 else ""
        return {
            "width": self.WIDTH,
            "banner": image_data_uri(banner_path),
            "avatar": image_data_uri(avatar_path),
            "name": card.name,
            "vip": vip_text,
            "level": card.level_info.current_level,
            "sex": card.sex if card.sex in ("男", "女") else "",
            "mid": card.mid,
            # 个人认证黄色闪电, 机构认证蓝色闪电
            "official": official.title if official.role else "",
            "official_org": 3 <= official.role <= 6,
            "sign": card.sign.strip(),
            "stats": [
                ("粉丝", fmt_count(data.follower)),
                ("关注", fmt_count(card.attention)),
                ("获赞", fmt_count(data.like_num)),
                ("投稿", fmt_count(data.archive_count)),
            ],
            "videos": [
                {
                    "cover": image_data_uri(cover),
                    "title": video.title,
                    "length": video.length,
                    "play": fmt_count(video.play)
                    if isinstance(video.play, int)
                    else video.play,
                    "date": datetime.fromtimestamp(video.created, self.tz).strftime(
                        "%Y-%m-%d"
                    ),
                }
                for video, cover in zip(info.videos, cover_paths)
            ],
            "url": info.url,
        }


def space_image_urls(info: SpaceInfo) -> tuple[str, str, list[str]]:
    """头像、头图、投稿封面都按卡片里的显示尺寸取缩略图, 少下点流量"""
    card = info.user.card
    banner = info.user.space.l_img if info.user.space else ""
    return (
        bfs_thumb(card.face, "176w_176h_1c") if card.face else "",
        bfs_thumb(banner, "1440w") if banner else "",
        [
            bfs_thumb(video.pic, "320w_200h_1c") if video.pic else ""
            for video in info.videos
        ],
    )
