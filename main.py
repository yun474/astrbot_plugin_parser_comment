# main.py

import asyncio
import json
import re

from astrbot.api import logger
from astrbot.api.event import filter
from astrbot.api.star import Context, Star
from astrbot.core import AstrBotConfig
from astrbot.core.message.components import At, Image, Json, Plain
from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import (
    AiocqhttpMessageEvent,
)

from .core.arbiter import ArbiterContext, EmojiLikeArbiter
from .core.clean import CacheCleaner
from .core.config import PluginConfig
from .core.debounce import Debouncer
from .core.download import Downloader
from .core.exception import ParseException
from .core.parsers import BaseParser, BilibiliParser
from .core.render import Renderer
from .core.sender import MessageSender
from .core.utils import extract_json_url


class ParserPlugin(Star):
    LLM_TOOL_NAME = "parse_media_link"

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.cfg = PluginConfig(config, context=context)
        # 渲染器
        self.renderer = Renderer(self.cfg)
        # 下载器
        self.downloader = Downloader(self.cfg)
        # 防抖器
        self.debouncer = Debouncer(self.cfg)
        # 仲裁器
        self.arbiter = EmojiLikeArbiter()
        # 消息发送器
        self.sender = MessageSender(self.cfg, self.renderer)
        # 缓存清理器
        self.cleaner = CacheCleaner(self.cfg)
        # 关键词 -> Parser 映射
        self.parser_map: dict[str, BaseParser] = {}
        # 关键词 -> 正则 列表
        self.key_pattern_list: list[tuple[str, re.Pattern[str]]] = []

    def _use_qq_official_mode(self, _event: AstrMessageEvent) -> bool:
        """适配模式由配置直接控制，避免平台名称差异让兼容逻辑失效。"""
        return bool(self.cfg.qq_official_mode)

    async def initialize(self):
        """加载、重载插件时触发"""
        # 加载渲染器资源
        await asyncio.to_thread(Renderer.load_resources)
        # 注册解析器
        self._register_parser()
        self._sync_llm_tool_state()

    async def terminate(self):
        """插件卸载时触发"""
        # 关下载器里的会话
        await self.downloader.close()
        # 关所有解析器里的会话 (去重后的实例)
        unique_parsers = set(self.parser_map.values())
        for parser in unique_parsers:
            await parser.close_session()
        # 关 HTML 渲染用的浏览器
        await BaseParser.close_html_renderer()
        # 关缓存清理器
        await self.cleaner.stop()

    def _register_parser(self):
        """注册解析器（以 parser.enable 为唯一启用来源）"""
        # 所有 Parser 子类
        all_subclass = BaseParser.get_all_subclass()
        enabled_platforms = set(self.cfg.parser.enabled_platforms())

        enabled_classes: list[type[BaseParser]] = []
        enabled_names: list[str] = []
        for cls in all_subclass:
            platform_name = cls.platform.name

            if platform_name not in enabled_platforms:
                logger.debug(f"[parser] 平台未启用或未配置: {platform_name}")
                continue

            enabled_classes.append(cls)
            enabled_names.append(platform_name)

            # 一个平台一个 parser 实例
            parser = cls(self.cfg, self.downloader)

            # 关键词 → parser
            for keyword, _ in cls._key_patterns:
                self.parser_map[keyword] = parser

        logger.debug(f"启用平台: {'、'.join(enabled_names) if enabled_names else '无'}")

        # -------- 关键词-正则表（统一生成） --------
        patterns: list[tuple[str, re.Pattern[str]]] = []

        for cls in enabled_classes:
            for kw, pat in cls._key_patterns:
                patterns.append((kw, re.compile(pat) if isinstance(pat, str) else pat))

        # 长关键词优先，避免短词抢匹配
        patterns.sort(key=lambda x: -len(x[0]))

        self.key_pattern_list = patterns

        logger.debug(f"[parser] 关键词-正则对已生成: {[kw for kw, _ in patterns]}")

    def _get_parser_by_type(self, parser_type):
        for parser in self.parser_map.values():
            if isinstance(parser, parser_type):
                return parser
        raise ValueError(f"未找到类型为 {parser_type} 的 parser 实例")

    def _sync_llm_tool_state(self) -> None:
        """让工具是否暴露给 LLM 与模式开关保持一致。"""
        setter_name = (
            "activate_llm_tool" if self.cfg.llm_tool_mode else "deactivate_llm_tool"
        )
        setter = getattr(self.context, setter_name, None)
        if not callable(setter):
            logger.warning(f"[LLMTool] 当前 AstrBot 不支持 {setter_name}")
            return
        try:
            if not setter(self.LLM_TOOL_NAME):
                logger.warning(f"[LLMTool] 未找到工具: {self.LLM_TOOL_NAME}")
        except Exception as e:
            logger.warning(f"[LLMTool] 同步工具状态失败: {e}")

    def _match_link(self, text: str) -> tuple[str, re.Match[str]] | None:
        """按与消息监听相同的规则查找解析器。"""
        for keyword, pattern in self.key_pattern_list:
            if keyword not in text:
                continue
            if searched := pattern.search(text):
                return keyword, searched
        return None

    def _session_denied_reason(self, event: AstrMessageEvent) -> str | None:
        umo = event.unified_msg_origin
        if self.cfg.whitelist and umo not in self.cfg.whitelist:
            return "当前会话不在解析白名单中"
        if self.cfg.blacklist and umo in self.cfg.blacklist:
            return "当前会话已关闭解析"
        return None

    @staticmethod
    def _tool_result(success: bool, message: str, **extra) -> str:
        return json.dumps(
            {"success": success, "message": message, **extra},
            ensure_ascii=False,
        )

    @filter.llm_tool(name=LLM_TOOL_NAME)
    async def parse_media_link(self, event: AstrMessageEvent, link: str) -> str:
        """解析媒体链接，并将视频或图片直接发送到当前会话。

        Args:
            link(string): 用户要求解析的完整媒体链接
        """
        if not self.cfg.llm_tool_mode:
            return self._tool_result(False, "LLM 工具模式未开启")

        if denied_reason := self._session_denied_reason(event):
            return self._tool_result(False, denied_reason)

        text = str(link or "").strip()
        if not text:
            return self._tool_result(False, "未提供媒体链接")

        matched = self._match_link(text)
        if matched is None:
            return self._tool_result(False, "没有匹配到已启用的链接解析器")

        keyword, searched = matched
        matched_link = searched.group(0)
        umo = event.unified_msg_origin
        if self.debouncer.hit_link(umo, matched_link):
            return self._tool_result(False, "链接处于防抖时间内，未重复解析")

        try:
            parse_res = await self.parser_map[keyword].parse(keyword, searched)
            resource_id = parse_res.get_resource_id()
            if self.debouncer.hit_resource(umo, resource_id):
                return self._tool_result(False, "资源处于防抖时间内，未重复发送")

            sent = await self.sender.send_parse_result(
                event,
                parse_res,
                direct_media=True,
            )
            if not sent:
                return self._tool_result(
                    False,
                    "解析完成，但没有可发送的媒体或消息发送失败",
                    platform=parse_res.platform.display_name,
                )

            return self._tool_result(
                True,
                "解析成功，媒体已直接发送到当前会话",
                platform=parse_res.platform.display_name,
                title=parse_res.title or "",
                url=parse_res.url or matched_link,
            )
        except Exception as e:
            logger.warning(f"[LLMTool] 解析失败: link={matched_link}, error={e}")
            return self._tool_result(False, f"解析失败: {e}", url=matched_link)

    @staticmethod
    def _card_text(chain: list) -> str | None:
        """分享卡片: OneBot 的 Json 段取出链接; 官 Bot 会把小程序卡片转成
        "[卡片消息] ..." 多行文本 (没有链接), 原样交给解析器按标题匹配"""
        for seg in chain:
            if isinstance(seg, Json) and (url := extract_json_url(seg.data)):
                logger.debug(f"解析Json组件: {url}")
                return url
            if isinstance(seg, Plain) and seg.text.lstrip().startswith("[卡片消息]"):
                return seg.text
        return None

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_message(self, event: AstrMessageEvent):
        """消息的统一入口"""
        umo = event.unified_msg_origin

        # 白名单
        if self.cfg.whitelist and umo not in self.cfg.whitelist:
            return

        # 黑名单
        if self.cfg.blacklist and umo in self.cfg.blacklist:
            return

        # 消息链
        chain = event.get_messages()
        if not chain:
            return

        seg1 = chain[0]
        card_text = self._card_text(chain)
        # LLM 工具模式只认工具调用, 但 LLM 看不见分享卡片, 所以卡片直接唤醒解析
        card_wakeup = bool(self.cfg.llm_tool_mode and card_text)
        if self.cfg.llm_tool_mode and not card_wakeup:
            return

        text = card_text or event.message_str
        if not text:
            return

        self_id = event.get_self_id()

        # 指定机制：专门@其他bot的消息不解析
        if isinstance(seg1, At) and str(seg1.qq) != self_id:
            return

        # 核心匹配逻辑 ：关键词 + 正则双重判定，汇集了所有解析器的正则对。
        matched = self._match_link(text)
        if matched is None:
            return
        keyword, searched = matched
        logger.debug(f"匹配结果: {keyword}, {searched}")
        qq_official_mode = self._use_qq_official_mode(event)

        # 仲裁机制 (官 Bot 模式和卡片唤醒的 LLM 工具模式都不贴表情)
        if (
            not qq_official_mode
            and not card_wakeup
            and isinstance(event, AiocqhttpMessageEvent)
            and not event.is_private_chat()
        ):
            raw = event.message_obj.raw_message
            if not isinstance(raw, dict):
                logger.warning(f"Unexpected raw_message type: {type(raw)}")
                return
            is_win = await self.arbiter.compete(
                bot=event.bot,
                ctx=ArbiterContext(
                    message_id=int(raw["message_id"]),
                    msg_time=int(raw["time"]),
                    self_id=int(raw["self_id"]),
                ),
            )
            if not is_win:
                logger.debug("Bot在仲裁中输了, 跳过解析")
                return
            logger.debug("Bot在仲裁中胜出, 准备解析...")

        # 基于link防抖
        link = searched.group(0)
        if self.debouncer.hit_link(umo, link):
            logger.warning(f"[链接防抖] 链接 {link} 在防抖时间内，跳过解析")
            return

        if qq_official_mode and not card_wakeup:
            try:
                await event.send(event.plain_result("云云帮你发视频，稍等一下哦..."))
            except Exception as e:
                logger.warning(f"[QQOfficial] 开始解析提示发送失败: {e}")

        # 解析
        parser = self.parser_map[keyword]
        try:
            parse_res = await parser.parse(keyword, searched)
        except ParseException as e:
            cause = f" ({e.__cause__!r})" if e.__cause__ else ""
            logger.error(
                f"[{parser.platform.display_name}] 解析失败: {e.message}{cause} | link: {link}"
            )
            if self.cfg.show_download_fail_tip:
                await event.send(event.plain_result(f"解析失败: {e.message}"))
            return

        # 基于资源ID防抖
        resource_id = parse_res.get_resource_id()
        if self.debouncer.hit_resource(umo, resource_id):
            logger.warning(f"[资源防抖] 资源 {resource_id} 在防抖时间内，跳过发送")
            return

        # 发送 (卡片唤醒沿用 LLM 工具模式的直发媒体策略)
        await self.sender.send_parse_result(event, parse_res, direct_media=card_wakeup)

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("开启解析")
    async def open_parser(self, event: AstrMessageEvent):
        """开启当前会话的解析"""
        umo = event.unified_msg_origin
        self.cfg.remove_blacklist(umo)
        yield event.plain_result("当前会话的解析已开启")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("关闭解析")
    async def close_parser(self, event: AstrMessageEvent):
        """关闭当前会话的解析"""
        umo = event.unified_msg_origin
        self.cfg.add_blacklist(umo)
        yield event.plain_result("当前会话的解析已关闭")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("登录B站", alias={"blogin", "登录b站"})
    async def login_bilibili(self, event: AstrMessageEvent):
        """扫码登录B站"""
        parser: BilibiliParser = self._get_parser_by_type(BilibiliParser)  # type: ignore
        qrcode = await parser.login.login_with_qrcode()
        yield event.chain_result([Image.fromBytes(qrcode)])
        async for msg in parser.login.check_qr_state():
            yield event.plain_result(msg)
