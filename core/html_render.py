"""HTML 转图片

优先用本地 Playwright 截图 (清晰、离线), 没装浏览器或起不来时退回 AstrBot 自带的
网络文转图接口, 没有浏览器的环境也能出图, 只是分辨率低一些。
"""

import asyncio
import base64
import sys
import uuid
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from typing import ClassVar

from astrbot.api import logger
from jinja2 import Environment, FileSystemLoader
from markupsafe import Markup, escape
from PIL import Image, ImageChops, UnidentifiedImageError

from .config import PluginConfig
from .utils import safe_unlink

TEMPLATES_DIR = Path(__file__).parent / "resources" / "templates"
FONT_PATH = Path(__file__).parent / "resources" / "HYSongYunLangHeiW-1.ttf"
SYMBOL_FONT_PATHS = [
    FONT_PATH.parent / "fonts" / "NotoSansMath-Regular.ttf",
    FONT_PATH.parent / "fonts" / "NotoSansSymbols2-Regular.ttf",
]

PAGE_BG = (241, 242, 243)
"""页面底色 (B站 #F1F2F3), 网络渲染裁边时靠它找内容范围"""

ENGINES = ("auto", "playwright", "astrbot")


def _finalize(value):
    """模板输出统一转义, 顺带把花括号转成实体: 网络渲染会把整页 HTML 包在
    {% raw %} 里再交给远端 Jinja, 评论里出现的花括号不能有机会拼出模板语法"""
    if value is None:
        return ""
    return Markup(str(escape(value)).replace("{", "&#123;").replace("}", "&#125;"))


TEMPLATES = Environment(
    loader=FileSystemLoader(TEMPLATES_DIR),
    autoescape=True,
    finalize=_finalize,
    trim_blocks=True,
    lstrip_blocks=True,
)
TEMPLATES.globals["font_url"] = FONT_PATH.as_uri()
TEMPLATES.globals["symbol_font_urls"] = [p.as_uri() for p in SYMBOL_FONT_PATHS]


@lru_cache(maxsize=1)
def inline_symbol_fonts():
    """网络渲染无法访问插件目录，符号字体随 HTML 一起发送。"""
    return [
        "data:font/ttf;base64," + base64.b64encode(p.read_bytes()).decode()
        for p in SYMBOL_FONT_PATHS
    ]


def image_data_uri(path: Path | None) -> str | None:
    """把本地图片内联成 data URI, 模板里不用管文件路径, 两种渲染引擎都能读"""
    if path is None or not path.exists():
        return None
    try:
        with Image.open(path) as img:
            # 评论原图可能有数千万像素；卡片里最多显示几百像素。
            if max(img.size) > 1600:
                img.thumbnail((1600, 1600))
                buffer = BytesIO()
                img.convert("RGB").save(buffer, "JPEG", quality=85)
                return (
                    "data:image/jpeg;base64,"
                    + base64.b64encode(buffer.getvalue()).decode()
                )
            img.verify()
        data = path.read_bytes()
    except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError):
        return None
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        mime = "image/png"
    elif data[:6] in (b"GIF87a", b"GIF89a"):
        mime = "image/gif"
    elif data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        mime = "image/webp"
    elif data[4:12] in (b"ftypavif", b"ftypavis"):
        mime = "image/avif"
    else:
        mime = "image/jpeg"
    return f"data:{mime};base64,{base64.b64encode(data).decode()}"


class HtmlRenderer:
    LAUNCH_ARGS: ClassVar[list[str]] = [
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--disable-gpu",
    ]
    SCALE = 2
    """Playwright 截图倍率, 文字在手机上看也清晰"""
    JPEG_QUALITY = 90

    def __init__(self, cfg: PluginConfig):
        self.cfg = cfg
        self.engine = cfg.render_engine if cfg.render_engine in ENGINES else "auto"
        self._playwright = None
        self._browser = None
        self._startup_task = None
        self._lock = asyncio.Lock()
        self._render_slots = asyncio.Semaphore(2)
        self._install_tried = False
        self._browser_unavailable = False

    async def render(
        self, template: str, context: dict, out_path: Path, *, selector: str = "#root"
    ) -> Path:
        """渲染模板并截图, 返回 out_path (JPEG)"""
        if self.engine != "astrbot" and not self._browser_unavailable:
            try:
                browser = await self._get_browser()
            except Exception as e:
                if self.engine == "playwright":
                    raise
                self._browser_unavailable = True
                logger.warning(
                    f"[HtmlRender] 本地 Playwright 不可用, 改用 AstrBot 网络渲染: {e}"
                    " (Linux 请执行 playwright install --with-deps chromium)"
                )
            else:
                html = TEMPLATES.get_template(template).render(context)
                async with self._render_slots:
                    return await self._screenshot(browser, html, out_path, selector)
        html = TEMPLATES.get_template(template).render(
            {**context, "symbol_font_urls": inline_symbol_fonts()}
        )
        return await self._render_by_astrbot(html, out_path)

    async def close(self):
        if self._startup_task is not None and not self._startup_task.done():
            self._startup_task.cancel()
            await asyncio.gather(self._startup_task, return_exceptions=True)
        if self._browser is not None:
            await self._browser.close()
            self._browser = None
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None

    async def _get_browser(self):
        if self._browser is not None and self._browser.is_connected():
            return self._browser
        if self._startup_task is None or self._startup_task.done():
            self._startup_task = asyncio.create_task(self._start_browser())
            self._startup_task.add_done_callback(
                lambda t: t.cancelled() or t.exception()
            )
        # 首次下载浏览器可以继续进行，评论任务超时不能打断共享浏览器启动。
        return await asyncio.shield(self._startup_task)

    async def _start_browser(self):
        async with self._lock:
            if self._browser is not None and self._browser.is_connected():
                return self._browser
            from playwright.async_api import async_playwright

            if self._playwright is None:
                self._playwright = await async_playwright().start()
            try:
                self._browser = await self._playwright.chromium.launch(
                    headless=True, args=self.LAUNCH_ARGS
                )
            except Exception as e:
                if "Executable doesn't exist" not in str(e) or self._install_tried:
                    raise
                self._install_tried = True
                await self._install_chromium()
                self._browser = await self._playwright.chromium.launch(
                    headless=True, args=self.LAUNCH_ARGS
                )
            return self._browser

    @staticmethod
    async def _install_chromium():
        """第一次用时自动下载 Chromium, 约 150 MB, 只需一次"""
        logger.info(
            "[HtmlRender] 未找到 Chromium, 正在执行 playwright install chromium ..."
        )
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "playwright",
            "install",
            "chromium",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=900)
        finally:
            if proc.returncode is None:
                proc.kill()
                await proc.wait()
        if proc.returncode != 0:
            raise RuntimeError(
                f"playwright install chromium 失败: {stderr.decode(errors='ignore')[-300:]}"
            )
        logger.info("[HtmlRender] Chromium 安装完成")

    async def _screenshot(
        self, browser, html: str, out_path: Path, selector: str
    ) -> Path:
        # 落成文件再打开, 页面里的 file:// 字体才能加载
        token = uuid.uuid4().hex
        html_path = out_path.with_name(f"{out_path.stem}_{token}.html")
        image_path = html_path.with_suffix(".jpg")
        await asyncio.to_thread(html_path.write_text, html, "utf-8")
        context = None
        try:
            async with asyncio.timeout(15):
                context = await browser.new_context(
                    viewport={"width": 800, "height": 600},
                    device_scale_factor=self.SCALE,
                )
                page = await context.new_page()
                await page.goto(html_path.as_uri(), wait_until="load")
                await page.evaluate("document.fonts.ready")
                await page.locator(selector).screenshot(
                    path=str(image_path), type="jpeg", quality=self.JPEG_QUALITY
                )
                await asyncio.to_thread(image_path.replace, out_path)
        finally:
            try:
                if context is not None:
                    await context.close()
            finally:
                await safe_unlink(html_path)
                await safe_unlink(image_path)
        return out_path

    async def _render_by_astrbot(self, html: str, out_path: Path) -> Path:
        from astrbot.core import html_renderer

        tmp = await html_renderer.render_custom_template(
            "{% raw %}" + html + "{% endraw %}",
            {},
            return_url=False,
            options={"full_page": True, "type": "jpeg", "quality": self.JPEG_QUALITY},
        )
        await asyncio.to_thread(self._crop_to_content, Path(tmp), out_path)
        return out_path

    def _crop_to_content(self, src: Path, dst: Path) -> None:
        """网络渲染的画布固定 800 宽、至少 720 高, 按底色裁掉右侧和底部的空白"""
        with Image.open(src) as img:
            rgb = img.convert("RGB")
        diff = ImageChops.difference(rgb, Image.new("RGB", rgb.size, PAGE_BG))
        if box := diff.convert("L").point(lambda v: 255 if v > 12 else 0).getbbox():
            left, top, right, bottom = box
            pad = 16
            rgb = rgb.crop(
                (
                    max(0, left - pad),
                    max(0, top - pad),
                    min(rgb.width, right + pad),
                    min(rgb.height, bottom + pad),
                )
            )
        rgb.save(dst, "JPEG", quality=self.JPEG_QUALITY)
        src.unlink(missing_ok=True)
