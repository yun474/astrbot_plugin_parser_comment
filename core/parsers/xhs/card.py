"""小红书笔记卡片: 首图 + 作者 + 标题 + 正文 + 互动数据, 热评接在下面; 视频笔记只渲染热评"""

from __future__ import annotations

import asyncio
import re
import uuid
from asyncio import Task
from datetime import datetime
from pathlib import Path

from astrbot.api import logger
from markupsafe import Markup, escape

from ...comment_utils import download_comment_assets
from ...data import Author, ImageContent
from ...exception import DownloadLimitException
from ...html_render import image_data_uri
from ..bilibili.common import fmt_relative_time, settle_path
from .model import Comment, Note, NotePage


class XHSCardRenderer:
    WIDTH = 720
    TEMPLATE = "xhs_note.html"
    _TOPIC_RE = re.compile(r"#([^#\n]+?)\[话题\]#")

    def __init__(self, parser, *, show_replies: bool):
        self.parser = parser
        self.show_replies = show_replies

    def build(
        self,
        page: NotePage,
        comments: list[Comment],
        author: Author,
        *,
        show_note: bool,
        cover: Task[Path] | None = None,
    ) -> ImageContent:
        """后台渲染, 不拖住图集和视频的下载"""
        task = asyncio.create_task(
            self._render(page, comments, author, show_note, cover),
            name=f"xhs_card_{page.noteData.noteId}",
        )
        task.add_done_callback(lambda t: t.cancelled() or t.exception())
        return ImageContent(task)

    async def _render(self, page, comments, author, show_note, cover) -> Path:
        note = page.noteData
        try:
            cover_path, avatar_path, assets = await asyncio.gather(
                settle_path(cover),
                settle_path(author.avatar),
                self._download_assets(comments),
            )
            context = await asyncio.to_thread(
                self._context,
                page,
                comments,
                cover_path,
                avatar_path,
                assets,
                show_note,
            )
            kind = "note" if show_note else "comments"
            out_path = (
                self.parser.cfg.cache_dir
                / f"xhs_{kind}_{note.noteId}_{uuid.uuid4().hex[:8]}.jpg"
            )
            return await self.parser.html_renderer.render(
                self.TEMPLATE, context, out_path
            )
        except Exception as e:
            logger.warning(
                f"[小红书] 卡片渲染失败 ({type(e).__name__}): {str(e)[:200]}"
            )
            raise DownloadLimitException("小红书卡片渲染失败") from e

    def _replies(self, comment: Comment) -> list[Comment]:
        return comment.subComments if self.show_replies else []

    async def _download_assets(self, comments: list[Comment]) -> dict[str, str | None]:
        async def download(url: str) -> str | None:
            path = await self.parser.downloader.download_img(
                url, headers=self.parser.headers, proxy=self.parser.proxy
            )
            return await asyncio.to_thread(image_data_uri, path)

        urls = [
            url
            for root in comments
            for c in (root, *self._replies(root))
            for url in (c.user.image, *(p.originUrl or p.url for p in c.pictures))
        ]
        return await download_comment_assets(urls, download)

    def _context(self, page, comments, cover_path, avatar_path, assets, show_note):
        note = page.noteData
        return {
            "width": self.WIDTH,
            "note": self._note_context(note, cover_path, avatar_path)
            if show_note
            else None,
            "title": note.title or self._TOPIC_RE.sub(r"#\1", note.desc).split("\n")[0],
            "author": note.user.nickName,
            "total": page.commentData.commentCount,
            "comments": [
                self._comment_context(c, note.user.userId, assets) for c in comments
            ],
            "show_replies": self.show_replies,
        }

    def _note_context(self, note: Note, cover_path, avatar_path) -> dict:
        info = note.interactInfo
        return {
            "avatar": image_data_uri(avatar_path),
            "nickname": note.user.nickName,
            "date": datetime.fromtimestamp(
                note.time / 1000, self.parser.cfg.timezone
            ).strftime("%Y-%m-%d %H:%M")
            if note.time
            else "",
            "cover": image_data_uri(cover_path),
            "image_count": len(note.imageList),
            "title": note.title,
            "desc": self._desc_html(note.desc),
            "stats": [
                ("heart", "点赞", info.likedCount or "0"),
                ("favorite", "收藏", info.collectedCount or "0"),
                ("reply", "评论", info.commentCount or "0"),
                ("share", "分享", info.shareCount or "0"),
            ],
        }

    def _comment_context(self, c: Comment, author_id: str, assets: dict) -> dict:
        replies = self._replies(c)
        return {
            "name": c.user.nickname,
            "avatar": assets.get(c.user.image),
            "message": c.content,
            "pictures": [
                uri for p in c.pictures if (uri := assets.get(p.originUrl or p.url))
            ],
            "date": fmt_relative_time(c.time // 1000, self.parser.cfg.timezone)
            if c.time
            else "",
            "location": c.ipLocation,
            "likes": c.likeViewCount,
            "author": bool(author_id) and c.user.userId == author_id,
            "replies": [self._comment_context(r, author_id, assets) for r in replies],
            "more": max(0, c.subCommentCount - len(replies))
            if self.show_replies
            else 0,
        }

    @classmethod
    def _desc_html(cls, desc: str) -> Markup:
        """话题 #xxx[话题]# 标蓝, 其余原样转义 (换行交给 pre-wrap)"""
        parts: list[str] = []
        pos = 0
        for match in cls._TOPIC_RE.finditer(desc):
            parts.append(str(escape(desc[pos : match.start()])))
            parts.append(f'<span class="topic">#{escape(match.group(1))}</span>')
            pos = match.end()
        parts.append(str(escape(desc[pos:])))
        return Markup("".join(parts).strip())
