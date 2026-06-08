from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

from astrbot.api import logger


@dataclass(slots=True)
class BiliCommentRenderItem:
    uname: str
    message: str
    avatar_path: Path | None = None
    pic_path: Path | None = None


@dataclass(slots=True)
class _PreparedComment:
    item: BiliCommentRenderItem
    text_lines: list[str]
    pic: Image.Image | None
    height: int


class BiliCommentRenderer:
    """Render a compact Bilibili comment board as one image.

    移植说明：这里来自 Menkelo/astrbot_plugin_r_parser 的评论图思路，
    但改成 Pillow 渲染，避免给原版解析器新增 Playwright 依赖。后续跟
    原版上游同步时，通常保留本文件和 parse_video 里的发送分组接线即可。
    """

    WIDTH = 760
    PADDING = 28
    CARD_PADDING = 20
    CARD_GAP = 16
    AVATAR_SIZE = 48
    AVATAR_GAP = 14
    HEADER_COVER_SIZE = (112, 70)
    MAX_COMMENT_PIC_HEIGHT = 560
    FOOTER_HEIGHT = 34

    BG_COLOR = (241, 242, 243)
    CARD_COLOR = (255, 255, 255)
    TEXT_COLOR = (35, 35, 35)
    SUB_TEXT_COLOR = (115, 115, 115)
    BILI_BLUE = (0, 161, 214)
    SOFT_BORDER = (224, 226, 229)

    FONT_PATH = Path(__file__).resolve().parents[2] / "resources" / "HYSongYunLangHeiW-1.ttf"

    def __init__(self):
        self.title_font = self._load_font(28)
        self.name_font = self._load_font(21)
        self.text_font = self._load_font(22)
        self.footer_font = self._load_font(15)

    def _load_font(self, size: int) -> ImageFont.ImageFont:
        try:
            return ImageFont.truetype(self.FONT_PATH, size)
        except Exception:
            logger.warning(f"[Bilibili-comments] 字体加载失败，使用默认字体: {self.FONT_PATH}")
            return ImageFont.load_default()

    @staticmethod
    def _text_height(font: ImageFont.ImageFont) -> int:
        try:
            top, bottom = font.getbbox("Ag测试")[1], font.getbbox("Ag测试")[3]
            return max(bottom - top + 8, 18)
        except Exception:
            return 24

    @staticmethod
    def _measure(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> float:
        try:
            return draw.textlength(text, font=font)
        except Exception:
            return len(text) * 12

    def _wrap_text(
        self,
        draw: ImageDraw.ImageDraw,
        text: str,
        font: ImageFont.ImageFont,
        max_width: int,
    ) -> list[str]:
        lines: list[str] = []
        current = ""

        for char in text:
            if char == "\n":
                if current:
                    lines.append(current)
                    current = ""
                continue

            trial = current + char
            if self._measure(draw, trial, font) <= max_width:
                current = trial
                continue

            if current:
                lines.append(current)
            current = char

        if current:
            lines.append(current)

        return lines or [""]

    @staticmethod
    def _load_local_image(path: Path | None) -> Image.Image | None:
        if path is None or not path.exists():
            return None

        try:
            with Image.open(path) as img:
                img = ImageOps.exif_transpose(img)
                return img.convert("RGBA")
        except Exception as e:
            logger.debug(f"[Bilibili-comments] 图片加载失败: {path}, {e}")
            return None

    @staticmethod
    def _resize_contain(img: Image.Image, max_width: int, max_height: int) -> Image.Image:
        ratio = min(max_width / img.width, max_height / img.height, 1)
        new_size = (max(1, int(img.width * ratio)), max(1, int(img.height * ratio)))
        return img.resize(new_size, Image.Resampling.LANCZOS)

    def _prepare_avatar(self, item: BiliCommentRenderItem) -> Image.Image:
        avatar = self._load_local_image(item.avatar_path)
        if avatar is None:
            avatar = Image.new("RGBA", (self.AVATAR_SIZE, self.AVATAR_SIZE), self.BILI_BLUE)
            draw = ImageDraw.Draw(avatar)
            initial = (item.uname or "?")[:1]
            bbox = draw.textbbox((0, 0), initial, font=self.name_font)
            draw.text(
                (
                    (self.AVATAR_SIZE - (bbox[2] - bbox[0])) / 2,
                    (self.AVATAR_SIZE - (bbox[3] - bbox[1])) / 2 - 2,
                ),
                initial,
                fill=(255, 255, 255),
                font=self.name_font,
            )
        else:
            avatar = avatar.resize((self.AVATAR_SIZE, self.AVATAR_SIZE), Image.Resampling.LANCZOS)

        mask = Image.new("L", (self.AVATAR_SIZE, self.AVATAR_SIZE), 0)
        ImageDraw.Draw(mask).ellipse((0, 0, self.AVATAR_SIZE - 1, self.AVATAR_SIZE - 1), fill=255)
        avatar.putalpha(mask)
        return avatar

    def _prepare_comments(self, comments: list[BiliCommentRenderItem]) -> list[_PreparedComment]:
        probe = Image.new("RGB", (self.WIDTH, 10))
        draw = ImageDraw.Draw(probe)

        prepared: list[_PreparedComment] = []
        text_width = self.WIDTH - self.PADDING * 2 - self.CARD_PADDING * 2
        body_text_width = text_width - self.AVATAR_SIZE - self.AVATAR_GAP
        text_line_height = self._text_height(self.text_font)
        name_line_height = self._text_height(self.name_font)

        for item in comments:
            lines = self._wrap_text(draw, item.message, self.text_font, body_text_width)
            pic = self._load_local_image(item.pic_path)
            if pic is not None:
                pic = self._resize_contain(pic, body_text_width, self.MAX_COMMENT_PIC_HEIGHT)

            text_height = len(lines) * text_line_height
            body_height = name_line_height + 8 + text_height
            if pic is not None:
                body_height += 12 + pic.height

            height = self.CARD_PADDING * 2 + max(self.AVATAR_SIZE, body_height)
            prepared.append(_PreparedComment(item=item, text_lines=lines, pic=pic, height=height))

        return prepared

    def _render_header(
        self,
        canvas: Image.Image,
        draw: ImageDraw.ImageDraw,
        video_title: str,
        cover_path: Path | None,
        y: int,
    ) -> int:
        x = self.PADDING
        cover = self._load_local_image(cover_path)
        if cover is not None:
            cover = ImageOps.fit(cover, self.HEADER_COVER_SIZE, Image.Resampling.LANCZOS)
            canvas.paste(cover, (x, y), cover)
            text_x = x + self.HEADER_COVER_SIZE[0] + 16
        else:
            text_x = x

        title_width = self.WIDTH - text_x - self.PADDING
        title_lines = self._wrap_text(draw, video_title, self.title_font, title_width)[:2]
        title_y = y + 3
        for line in title_lines:
            draw.text((text_x, title_y), line, fill=self.TEXT_COLOR, font=self.title_font)
            title_y += self._text_height(self.title_font)

        label = "B站评论区精选"
        draw.text((text_x, title_y + 2), label, fill=self.BILI_BLUE, font=self.footer_font)
        return y + self.HEADER_COVER_SIZE[1] + 24

    def _render_comment(
        self,
        canvas: Image.Image,
        draw: ImageDraw.ImageDraw,
        prepared: _PreparedComment,
        y: int,
    ) -> int:
        x = self.PADDING
        card_width = self.WIDTH - self.PADDING * 2
        card_bottom = y + prepared.height

        draw.rounded_rectangle(
            (x, y, x + card_width, card_bottom),
            radius=18,
            fill=self.CARD_COLOR,
            outline=self.SOFT_BORDER,
            width=1,
        )

        content_x = x + self.CARD_PADDING
        content_y = y + self.CARD_PADDING
        avatar = self._prepare_avatar(prepared.item)
        canvas.paste(avatar, (content_x, content_y), avatar)

        text_x = content_x + self.AVATAR_SIZE + self.AVATAR_GAP
        text_y = content_y
        draw.text((text_x, text_y), prepared.item.uname or "B站用户", fill=self.TEXT_COLOR, font=self.name_font)
        text_y += self._text_height(self.name_font) + 8

        line_height = self._text_height(self.text_font)
        for line in prepared.text_lines:
            draw.text((text_x, text_y), line, fill=self.TEXT_COLOR, font=self.text_font)
            text_y += line_height

        if prepared.pic is not None:
            text_y += 12
            draw.rounded_rectangle(
                (text_x - 1, text_y - 1, text_x + prepared.pic.width + 1, text_y + prepared.pic.height + 1),
                radius=10,
                fill=(247, 248, 250),
            )
            canvas.paste(prepared.pic, (text_x, text_y), prepared.pic)

        return card_bottom + self.CARD_GAP

    def _render_sync(
        self,
        out_path: Path,
        comments: list[BiliCommentRenderItem],
        video_title: str,
        cover_path: Path | None,
    ) -> None:
        prepared = self._prepare_comments(comments)
        header_height = self.HEADER_COVER_SIZE[1] + 24
        total_height = self.PADDING + header_height
        total_height += sum(item.height + self.CARD_GAP for item in prepared)
        total_height += self.FOOTER_HEIGHT

        canvas = Image.new("RGB", (self.WIDTH, total_height), self.BG_COLOR)
        draw = ImageDraw.Draw(canvas)

        y = self.PADDING
        y = self._render_header(canvas, draw, video_title, cover_path, y)
        for item in prepared:
            y = self._render_comment(canvas, draw, item, y)

        footer = "Rendered from Bilibili comments"
        footer_width = self._measure(draw, footer, self.footer_font)
        draw.text(
            ((self.WIDTH - footer_width) / 2, max(y, total_height - self.FOOTER_HEIGHT)),
            footer,
            fill=self.SUB_TEXT_COLOR,
            font=self.footer_font,
        )

        out_path.parent.mkdir(parents=True, exist_ok=True)
        canvas.save(out_path, format="PNG")

    async def render_merged_comments(
        self,
        out_path: Path,
        comments: list[BiliCommentRenderItem],
        video_title: str,
        cover_path: Path | None,
    ) -> Path:
        await asyncio.to_thread(self._render_sync, out_path, comments, video_title, cover_path)
        return out_path
