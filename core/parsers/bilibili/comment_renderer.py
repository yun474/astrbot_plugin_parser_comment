"""B站评论区图: 照着 B站网页的样子渲染, 带楼中楼、表情、@ 高亮和等级 / 大会员 / UP / 置顶标记"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from datetime import tzinfo
from pathlib import Path

from markupsafe import Markup, escape

from ...html_render import HtmlRenderer, image_data_uri
from .common import fmt_count, fmt_relative_time


@dataclass(slots=True)
class BiliComment:
    rpid: str
    mid: int
    uname: str
    message: str
    avatar_url: str
    level: int = 0
    vip: bool = False
    like: int = 0
    ctime: int = 0
    location: str = ""
    is_up: bool = False
    is_top: bool = False
    up_liked: bool = False
    rcount: int = 0
    """楼中楼总数"""
    pic_urls: list[str] = field(default_factory=list)
    emotes: dict[str, tuple[str, int]] = field(default_factory=dict)
    """表情文本 -> (图片链接, 尺寸档位)"""
    has_at: bool = False
    replies: list[BiliComment] = field(default_factory=list)
    """楼中楼 (接口给的前几条)"""
    avatar_path: Path | None = None
    pic_paths: list[Path] = field(default_factory=list)
    emote_paths: dict[str, Path] = field(default_factory=dict)

    def iter_all(self):
        yield self
        yield from self.replies


class BiliCommentRenderer:
    WIDTH = 720
    TEMPLATE = "bili_comments.html"
    _TOKEN_RE = re.compile(r"\[[^\[\]]{1,20}\]|@[^\s:@]+")

    def __init__(self, html: HtmlRenderer, tz: tzinfo):
        self.html = html
        self.tz = tz

    async def render(
        self,
        out_path: Path,
        comments: list[BiliComment],
        *,
        title: str,
        up_name: str,
        total: int,
        cover_path: Path | None,
    ) -> Path:
        context = await asyncio.to_thread(
            self._context, comments, title, up_name, total, cover_path
        )
        return await self.html.render(self.TEMPLATE, context, out_path)

    def _context(
        self,
        comments: list[BiliComment],
        title: str,
        up_name: str,
        total: int,
        cover_path: Path | None,
    ) -> dict:
        return {
            "width": self.WIDTH,
            "cover": image_data_uri(cover_path),
            "title": title,
            "up_name": up_name,
            "total": fmt_count(total) if total else "",
            "comments": [self._comment_context(c) for c in comments],
            "note": f"仅展示 {len(comments)} 条热评 · 楼中楼为部分回复",
        }

    def _comment_context(self, c: BiliComment) -> dict:
        return {
            "uname": c.uname,
            "avatar": image_data_uri(c.avatar_path),
            "level": c.level,
            "vip": c.vip,
            "is_up": c.is_up,
            "is_top": c.is_top,
            "up_liked": c.up_liked,
            "html": self._message_html(c),
            "pictures": [uri for p in c.pic_paths if (uri := image_data_uri(p))],
            "time": fmt_relative_time(c.ctime, self.tz),
            "location": c.location,
            "like": fmt_count(c.like),
            "replies": [self._comment_context(r) for r in c.replies],
            "more": c.rcount if c.rcount > len(c.replies) else 0,
        }

    def _message_html(self, c: BiliComment) -> Markup:
        """表情换成图片, @ 用户标蓝, 其余原样转义 (换行交给 pre-wrap)"""
        parts: list[str] = []
        pos = 0
        for match in self._TOKEN_RE.finditer(c.message):
            parts.append(str(escape(c.message[pos : match.start()])))
            token = match.group(0)
            if token in c.emote_paths and (uri := image_data_uri(c.emote_paths[token])):
                size = c.emotes[token][1]
                cls = "emote emote-large" if size >= 2 else "emote"
                parts.append(f'<img class="{cls}" src="{uri}" alt="{escape(token)}">')
            elif token.startswith("@") and c.has_at:
                parts.append(f'<span class="at">{escape(token)}</span>')
            else:
                parts.append(str(escape(token)))
            pos = match.end()
        parts.append(str(escape(c.message[pos:])))
        return Markup("".join(parts))
