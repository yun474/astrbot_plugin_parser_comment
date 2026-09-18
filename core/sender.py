import asyncio
from itertools import chain
from pathlib import Path

from astrbot.api import logger
from astrbot.core.message.components import (
    BaseMessageComponent,
    File,
    Image,
    Node,
    Nodes,
    Plain,
    Record,
    Video,
)
from astrbot.core.platform.astr_message_event import AstrMessageEvent

from .config import PluginConfig
from .data import (
    AudioContent,
    DynamicContent,
    FileContent,
    GraphicsContent,
    ImageContent,
    ParseResult,
    SendGroup,
    TextContent,
    VideoContent,
)
from .exception import (
    DownloadException,
    DownloadLimitException,
    DurationLimitException,
    SizeLimitException,
    ZeroSizeException,
)
from .render import Renderer


class MessageSender:
    """
    消息发送器

    职责：
    - 根据解析结果（ParseResult）规划发送策略
    - 控制是否渲染卡片、是否强制合并转发
    - 将不同类型的内容转换为 AstrBot 消息组件并发送

    重要原则：
    - 不在此处做解析
    - 不在此处决定“内容是什么”
    - 只负责“怎么发”
    """

    def __init__(self, config: PluginConfig, renderer: Renderer):
        self.cfg = config
        self.renderer = renderer

    def _use_qq_official_mode(self, _event: AstrMessageEvent) -> bool:
        """适配模式由配置直接控制，不再依赖适配器的平台名称。"""
        return bool(self.cfg.qq_official_mode)

    def _to_file_uri(self, path: Path) -> str:
        if not path.is_absolute():
            path = path.resolve()
        return path.as_uri()

    async def _image_from_path(
        self,
        path: Path,
        *,
        inline_bytes: bool = True,
    ) -> Image:
        """构造图片消息段。

        移植说明：OneBot/aiocqhttp 有时会把 file:// 本地图片重新解析成
        相对路径，导致发送阶段报 No such file。这里优先用 fromBytes
        内联图片，避免发送端二次读取本地文件路径。
        """
        if inline_bytes:
            try:
                data = await asyncio.to_thread(path.read_bytes)
                return Image.fromBytes(data)
            except Exception as e:
                logger.debug(f"图片内联失败，回退到本地文件组件: {path}, {e}")
        return Image.fromFileSystem(str(path))

    @staticmethod
    def _video_from_path(path: Path) -> Video:
        return Video.fromFileSystem(str(path))

    @staticmethod
    def _record_from_path(path: Path) -> Record:
        return Record.fromFileSystem(str(path))

    @staticmethod
    def _iter_contents(result: ParseResult):
        return chain(result.contents, result.repost.contents if result.repost else ())

    def _download_fail_tip(self, exc: DownloadException) -> Plain | None:
        """下载失败提示，带上具体原因方便用户定位问题"""
        if not self.cfg.show_download_fail_tip:
            return None
        match exc:
            case SizeLimitException():
                text = "此项媒体超过大小限制"
            case DurationLimitException():
                text = "此项媒体超过时长限制"
            case _:
                text = f"此项{exc.message}"
        return Plain(text)

    def _build_send_plan(
        self,
        result: ParseResult,
        contents: list | tuple | None = None,
        *,
        force_merge_override: bool | None = None,
        render_card_override: bool | None = None,
        preserve_order: bool = False,
    ) -> dict:
        """
        根据解析结果生成发送计划（plan）

        plan 只做“策略决策”，不做任何 IO 或发送动作。
        后续发送流程严格按 plan 执行，避免逻辑分散。
        """
        light, heavy = [], []

        # 合并主内容 + 转发内容，统一参与发送策略计算
        iterable = list(
            contents if contents is not None else self._iter_contents(result)
        )
        for cont in iterable:
            match cont:
                case ImageContent() | GraphicsContent() | TextContent():
                    light.append(cont)
                case VideoContent() | AudioContent() | FileContent() | DynamicContent():
                    heavy.append(cont)
                case _:
                    light.append(cont)

        # 仅在“单一重媒体且无其他内容”时，才允许渲染卡片
        is_single_heavy = len(heavy) == 1 and not light
        render_card = is_single_heavy and self.cfg.single_heavy_render_card
        if render_card_override is not None:
            render_card = render_card_override
        # 实际消息段数量（卡片也算一个段）
        seg_count = len(light) + len(heavy) + (1 if render_card else 0)

        # 达到阈值后，强制合并转发，避免刷屏
        force_merge = seg_count >= self.cfg.forward_threshold
        if force_merge_override is not None:
            force_merge = force_merge_override

        return {
            "light": light,
            "heavy": heavy,
            "render_card": render_card,
            # 预览卡片：仅在“渲染卡片 + 不合并”时独立发送
            "preview_card": render_card and not force_merge,
            "force_merge": force_merge,
            "preserve_order": preserve_order,
            "ordered": iterable if preserve_order else [],
        }

    async def _send_preview_card(
        self,
        event: AstrMessageEvent,
        result: ParseResult,
        plan: dict,
    ):
        """
        发送预览卡片（独立消息）

        场景：
        - 只有一个重媒体
        - 未触发合并转发
        - 卡片作为“预览”，不与正文混合
        """
        if not plan["preview_card"]:
            return

        if image_path := await self.renderer.render_card(result):
            await event.send(
                event.chain_result(
                    [
                        await self._image_from_path(
                            image_path,
                            inline_bytes=not self._use_qq_official_mode(event),
                        )
                    ]
                )
            )

    async def _append_content_segments(
        self,
        segs: list[BaseMessageComponent],
        cont,
        *,
        inline_images: bool,
        heavy_mode: bool,
    ) -> None:
        """把一个 MediaContent 转成消息段。

        移植说明：preserve_order 分组会混排视频和评论图，所以这里抽出
        单项转换逻辑，避免保序路径和原轻/重媒体路径出现两套行为。
        """
        if not heavy_mode:
            if isinstance(cont, TextContent):
                if cont.text:
                    segs.append(Plain(cont.text))
                return

            try:
                path: Path = await cont.get_path()
            except (DownloadLimitException, ZeroSizeException):
                return
            except DownloadException as e:
                if tip := self._download_fail_tip(e):
                    segs.append(tip)
                return

            match cont:
                case ImageContent():
                    segs.append(
                        await self._image_from_path(path, inline_bytes=inline_images)
                    )
                case GraphicsContent() as g:
                    # OneBot/aiocqhttp 本地文件参数要求 file:// URI，而非裸本地路径。
                    segs.append(
                        await self._image_from_path(path, inline_bytes=inline_images)
                    )
                    # GraphicsContent 允许携带补充文本
                    if g.text:
                        segs.append(Plain(g.text))
                    if g.alt:
                        segs.append(Plain(g.alt))
            return

        try:
            path: Path = await cont.get_path()
        except DownloadException as e:
            if tip := self._download_fail_tip(e):
                segs.append(tip)
            return

        match cont:
            case VideoContent() | DynamicContent():
                segs.append(self._video_from_path(path))
            case AudioContent():
                segs.append(
                    File(name=path.name, file=self._to_file_uri(path))
                    if self.cfg.audio_to_file
                    else self._record_from_path(path)
                )
            case FileContent():
                segs.append(File(name=path.name, file=self._to_file_uri(path)))

    async def _build_segments(
        self,
        result: ParseResult,
        plan: dict,
        *,
        inline_images: bool = True,
    ) -> list[BaseMessageComponent]:
        """
        根据发送计划构建消息段列表

        这里负责：
        - 下载媒体
        - 转换为 AstrBot 消息组件
        """
        segs: list[BaseMessageComponent] = []
        # 合并转发时，卡片以内联形式作为一个消息段参与合并
        if plan["render_card"] and plan["force_merge"]:
            if image_path := await self.renderer.render_card(result):
                segs.append(
                    await self._image_from_path(
                        image_path,
                        inline_bytes=inline_images,
                    )
                )

        if plan["preserve_order"]:
            for cont in plan["ordered"]:
                heavy_mode = isinstance(
                    cont, (VideoContent, AudioContent, FileContent, DynamicContent)
                )
                await self._append_content_segments(
                    segs,
                    cont,
                    inline_images=inline_images,
                    heavy_mode=heavy_mode,
                )
            return segs

        # 轻媒体处理
        for cont in plan["light"]:
            await self._append_content_segments(
                segs,
                cont,
                inline_images=inline_images,
                heavy_mode=False,
            )

        # 重媒体处理
        for cont in plan["heavy"]:
            await self._append_content_segments(
                segs,
                cont,
                inline_images=inline_images,
                heavy_mode=True,
            )

        return segs

    def _merge_segments_if_needed(
        self,
        event: AstrMessageEvent,
        segs: list[BaseMessageComponent],
        force_merge: bool,
    ) -> list[BaseMessageComponent]:
        """
        根据策略决定是否将消息段合并为转发节点

        合并后的消息结构：
        - 每个原始消息段成为一个 Node
        - 统一使用机器人自身身份
        """
        if not force_merge or not segs:
            return segs

        nodes = Nodes([])
        self_id = event.get_self_id()

        for seg in segs:
            nodes.nodes.append(Node(uin=self_id, name="解析器", content=[seg]))

        return [nodes]

    @staticmethod
    def _build_text_fallback(result: ParseResult) -> list[BaseMessageComponent]:
        lines: list[str] = []
        if result.header:
            lines.append(result.header)
        if result.text:
            lines.append(result.text)
        elif result.extra.get("info"):
            lines.append(str(result.extra["info"]))

        text = "\n".join(line for line in lines if line).strip()
        return [Plain(text)] if text else []

    async def _merge_gallery(self, contents: list) -> list:
        """图片超过阈值时拼成一张图，只在没有合并转发可用的模式下调用"""
        threshold = int(self.cfg.image_merge_threshold or 0)
        images = [cont for cont in contents if isinstance(cont, ImageContent)]
        if threshold <= 0 or len(images) <= threshold:
            return contents

        paths: list[Path] = []
        for cont in images:
            try:
                paths.append(await cont.get_path())
            except DownloadException as e:
                logger.warning(f"图集拼图跳过一张下载失败的图片: {e.message}")
        collage = await self.renderer.render_gallery(paths) if paths else None
        if collage is None:
            return contents

        logger.info(f"图集 {len(paths)} 张图片已拼成一张发送")
        merged: list = []
        for cont in contents:
            if not isinstance(cont, ImageContent):
                merged.append(cont)
            elif cont is images[0]:
                merged.append(ImageContent(collage))
        return merged

    def _resolve_groups(self, result: ParseResult) -> list[SendGroup]:
        if result.send_groups:
            return result.send_groups
        return [SendGroup(contents=list(MessageSender._iter_contents(result)))]

    async def _send_group(
        self,
        event: AstrMessageEvent,
        result: ParseResult,
        group: SendGroup,
        *,
        direct_media: bool = False,
    ) -> bool:
        qq_official_mode = self._use_qq_official_mode(event)
        contents = group.contents
        if qq_official_mode or direct_media:
            contents = await self._merge_gallery(contents)
        plan = self._build_send_plan(
            result,
            contents,
            force_merge_override=group.force_merge,
            render_card_override=group.render_card,
            preserve_order=bool(group.preserve_order),
        )

        if qq_official_mode or direct_media:
            # QQ 官方机器人不支持 OneBot/NapCat 的合并转发节点。
            # LLM 工具模式也必须把 Video/Image/File 等标准组件直接发到
            # 当前会话，不能把媒体包装进工具返回值或合并转发节点。
            plan["force_merge"] = False
            plan["preview_card"] = bool(plan["render_card"])

        await self._send_preview_card(event, result, plan)

        segs = await self._build_segments(
            result,
            plan,
            inline_images=not qq_official_mode,
        )
        if not qq_official_mode:
            segs = self._merge_segments_if_needed(event, segs, plan["force_merge"])

        if not segs:
            return False

        try:
            await event.send(event.chain_result(segs))
            return True
        except Exception as e:
            seg_meta = self._collect_seg_meta(segs)
            logger.error(f"发送解析结果失败： error={e}, segments={seg_meta}")
            return False

    @staticmethod
    def _collect_seg_meta(segs: list[BaseMessageComponent]) -> list[dict[str, str]]:
        """提取消息段元信息，用于失败日志定位。"""
        meta: list[dict[str, str]] = []

        for seg in segs:
            item = {"type": seg.__class__.__name__}
            for attr in ("file", "path", "url"):
                value = getattr(seg, attr, None)
                if value:
                    item["media"] = str(value)
                    break
            meta.append(item)

        return meta

    async def send_parse_result(
        self,
        event: AstrMessageEvent,
        result: ParseResult,
        *,
        direct_media: bool = False,
    ) -> bool:
        """
        发送解析结果的统一入口

        执行顺序固定：
        1. 构建发送计划
        2. 发送预览卡片（如有）
        3. 构建消息段
        4. 必要时合并转发
        5. 最终发送
        """
        groups = self._resolve_groups(result)

        sent = False
        for group in groups:
            sent = (
                await self._send_group(
                    event,
                    result,
                    group,
                    direct_media=direct_media,
                )
                or sent
            )

        if not sent:
            segs = self._build_text_fallback(result)
            if not segs:
                logger.warning("发送结果为空，不执行发送")
                return False

            try:
                await event.send(event.chain_result(segs))
                return True
            except Exception as e:
                seg_meta = self._collect_seg_meta(segs)
                logger.error(f"发送解析结果失败： error={e}, segments={seg_meta}")
                return False

        return True
