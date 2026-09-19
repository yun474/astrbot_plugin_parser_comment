from __future__ import annotations

import json
import zoneinfo
from collections.abc import Mapping, MutableMapping
from pathlib import Path
from types import MappingProxyType, UnionType
from typing import Any, Union, get_args, get_origin, get_type_hints

from astrbot.api import logger
from astrbot.core.config.astrbot_config import AstrBotConfig
from astrbot.core.star.context import Context
from astrbot.core.star.star_tools import StarTools
from astrbot.core.utils.astrbot_path import get_astrbot_plugin_path


class ConfigNode:
    """
    配置节点, 把 dict 变成强类型对象。

    规则：
    - schema 来自子类类型注解
    - 声明字段：读写，写回底层 dict
    - 未声明字段和下划线字段：仅挂载属性，不写回
    - 支持 ConfigNode 多层嵌套（lazy + cache）
    """

    _SCHEMA_CACHE: dict[type, dict[str, type]] = {}
    _FIELDS_CACHE: dict[type, set[str]] = {}

    @classmethod
    def _schema(cls) -> dict[str, type]:
        return cls._SCHEMA_CACHE.setdefault(cls, get_type_hints(cls))

    @classmethod
    def _fields(cls) -> set[str]:
        return cls._FIELDS_CACHE.setdefault(
            cls,
            {k for k in cls._schema() if not k.startswith("_")},
        )

    @staticmethod
    def _is_optional(tp: type) -> bool:
        if get_origin(tp) in (Union, UnionType):
            return type(None) in get_args(tp)
        return False

    def __init__(self, data: MutableMapping[str, Any]):
        object.__setattr__(self, "_data", data)
        object.__setattr__(self, "_children", {})
        for key, tp in self._schema().items():
            if key.startswith("_"):
                continue
            if key in data:
                continue
            if hasattr(self.__class__, key):
                continue
            if self._is_optional(tp):
                continue
            logger.warning(f"[config:{self.__class__.__name__}] 缺少字段: {key}")

    def __getattr__(self, key: str) -> Any:
        if key in self._fields():
            value = self._data.get(key)
            tp = self._schema().get(key)

            if isinstance(tp, type) and issubclass(tp, ConfigNode):
                children: dict[str, ConfigNode] = self.__dict__["_children"]
                if key not in children:
                    if not isinstance(value, MutableMapping):
                        raise TypeError(
                            f"[config:{self.__class__.__name__}] "
                            f"字段 {key} 期望 dict，实际是 {type(value).__name__}"
                        )
                    children[key] = tp(value)
                return children[key]

            return value

        if key in self.__dict__:
            return self.__dict__[key]

        raise AttributeError(key)

    def __setattr__(self, key: str, value: Any) -> None:
        if key in self._fields():
            self._data[key] = value
            return
        object.__setattr__(self, key, value)

    def raw_data(self) -> Mapping[str, Any]:
        """
        底层配置 dict 的只读视图
        """
        return MappingProxyType(self._data)

    def save_config(self) -> None:
        """
        保存配置到磁盘（仅允许在根节点调用）
        """
        if not isinstance(self._data, AstrBotConfig):
            raise RuntimeError(
                f"{self.__class__.__name__}.save_config() 只能在根配置节点上调用"
            )
        self._data.save_config()


class ConfigNodeContainer:
    """
    配置节点容器, 把 list 的 dict 变成 dict 的对象集合。

    - nodes: list[dict[str, Any]]
    - item_cls 用于包装 dict 成强类型节点
    - key_name 作为属性名访问, 默认为 "__template_key"
    """

    def __init__(
        self,
        nodes: list[dict[str, Any]],
        item_cls: type[ConfigNode],
        key_name="__template_key",
    ):
        self._nodes: dict[str, ConfigNode] = {}
        for node in nodes:
            key = node.get(key_name)
            if not key:
                logger.warning(f"[node] 缺少 {key_name}，已跳过")
                continue
            if key in self._nodes:
                logger.warning(f"[node] {key} 重复配置，已覆盖")
            self._nodes[key] = item_cls(node)

    def __getattr__(self, name: str) -> ConfigNode:
        if name in self._nodes:
            return self._nodes[name]
        raise AttributeError(name)

    def __iter__(self):
        return iter(self._nodes.values())

    def keys(self):
        return self._nodes.keys()

    def items(self):
        return self._nodes.items()


def qq_official_upload_limit_mb() -> int:
    """当前 AstrBot 官方适配器能上传的视频上限

    腾讯对 mp4 的软限制是 30 MB (超过降级成文件卡片); AstrBot 4.27.3 起支持分片上传,
    更早的版本只能 base64 直传, 超过 10 MB 会被平台 413。
    """
    try:
        from astrbot.core.platform.sources.qqofficial import (  # noqa: F401
            qqofficial_chunked_upload,
        )
    except ImportError:
        return 10
    return 30


def qq_official_force_chunked_upload() -> bool:
    """让官方适配器对本地视频 / 文件一律走分片上传

    腾讯富媒体接口已不再接受 base64 直传的视频 (返回 "上传URL错误"), 而适配器只对
    10 MB 以上的文件用分片上传, 这里把它的阈值改成 0。图片走的是另一条接口, 不受影响。
    """
    try:
        from astrbot.core.platform.sources.qqofficial import qqofficial_message_event
    except ImportError:
        return False
    if not hasattr(qqofficial_message_event, "QQOFFICIAL_CHUNKED_UPLOAD_THRESHOLD"):
        return False
    qqofficial_message_event.QQOFFICIAL_CHUNKED_UPLOAD_THRESHOLD = 0
    return True


# ================ 插件自定义配置 ==================


class ParserItem(ConfigNode):
    __template_key: str
    enable: bool
    use_proxy: bool
    cookies: str | None
    show_body_text: bool | None
    video_send_mode: str | None
    video_codec_list: list | None
    video_quality: str | None
    comment_render_enable: bool | None
    comment_limit: int | None
    comment_filter_text: bool | None
    comment_filter_qr: bool | None
    comment_qr_check_max: int | None
    comment_merge_with_video: bool | None
    comment_show_replies: bool | None
    poster_render_enable: bool | None
    nsfw: str | None

    @property
    def name(self) -> str:
        return self._data.get("__template_key")


class ParserConfig(ConfigNodeContainer):
    acfun: ParserItem
    bilibili: ParserItem
    douyin: ParserItem
    instagram: ParserItem
    kuaishou: ParserItem
    ncm: ParserItem
    nga: ParserItem
    tiktok: ParserItem
    twitter: ParserItem
    weibo: ParserItem
    xiaoheihe: ParserItem
    zhihu: ParserItem
    xhs: ParserItem
    youtube: ParserItem
    iwara: ParserItem
    shipinhao: ParserItem

    def __init__(self, nodes: list[dict[str, Any]]):
        super().__init__(nodes, item_cls=ParserItem)

    def platforms(self) -> list[str]:
        return list(self._nodes.keys())

    def enabled_platforms(self) -> list[str]:
        return [k for k, v in self._nodes.items() if getattr(v, "enable", True)]


class PluginConfig(ConfigNode):
    whitelist: list[str]
    blacklist: list[str]

    arbiter: bool
    qq_official_mode: bool
    qq_official_video_max_mb: int
    qq_official_lowest_quality: bool
    image_merge_threshold: int
    llm_tool_mode: bool
    render_engine: str
    debounce_interval: int

    source_max_size: int
    source_max_minute: int

    audio_to_file: bool
    single_heavy_render_card: bool
    forward_threshold: int

    show_download_fail_tip: bool
    download_timeout: int
    download_retry_times: int
    common_timeout: int

    proxy: str | None

    clean_cron: str

    parsers_template: list[dict[str, Any]]

    _plugin_name = "astrbot_plugin_parser"

    def __init__(self, config: AstrBotConfig, context: Context):
        # 旧配置文件里可能还没有这些键
        config.setdefault("qq_official_mode", False)
        config.setdefault("qq_official_video_max_mb", 30)
        config.setdefault("qq_official_lowest_quality", True)
        config.setdefault("image_merge_threshold", 4)
        config.setdefault("llm_tool_mode", False)
        config.setdefault("render_engine", "auto")
        super().__init__(config)
        self.context = context
        self.admins_id = self.context.get_config().get("admins_id", [])

        # ---------- 内置配置 ----------
        self.emoji_cdn = "https://cdn.jsdelivr.net/npm/emoji-datasource-facebook@14.0.0/img/facebook/64/"
        self.emoji_style = "FACEBOOK"  # 可选：APPLE、FACEBOOK、GOOGLE、TWITTER

        # ---------- 派生字段 ----------
        self.proxy = self.proxy or None
        self.max_duration = self.source_max_minute * 60
        self.max_size = self.source_max_size * 1024 * 1024
        # 官 Bot 模式: 视频体积受腾讯富媒体接口限制, 清晰度可强制最低档
        self.force_lowest_quality = bool(
            self.qq_official_mode and self.qq_official_lowest_quality
        )
        if self.qq_official_mode:
            official_max_mb = min(
                int(self.qq_official_video_max_mb), qq_official_upload_limit_mb()
            )
            self.max_size = min(self.max_size, official_max_mb * 1024 * 1024)
            logger.info(
                f"[官Bot] 媒体体积上限 {official_max_mb} MB, "
                f"强制最低清晰度: {self.force_lowest_quality}, "
                f"视频分片上传: {qq_official_force_chunked_upload()}"
            )

        tz = context.get_config().get("timezone")
        self.timezone = (
            zoneinfo.ZoneInfo(tz) if tz else zoneinfo.ZoneInfo("Asia/Shanghai")
        )

        # ---------- 路径 ----------
        self.data_dir = StarTools.get_data_dir(self._plugin_name)
        self.plugin_dir = Path(get_astrbot_plugin_path()) / self._plugin_name
        self.cache_dir = self.data_dir / "cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.cookie_dir = self.data_dir / "cookies"
        self.cookie_dir.mkdir(parents=True, exist_ok=True)
        self.default_template_file = self.plugin_dir / "default_template.json"

        # ---------- Parser ----------
        if not self.parsers_template:
            self.parsers_template[:] = self.load_parser_template(
                self.default_template_file
            )
            self.save_config()

        self.parser = ParserConfig(self.parsers_template)

    @staticmethod
    def load_parser_template(file: Path) -> list[dict[str, Any]]:
        try:
            with file.open(encoding="utf-8-sig") as f:
                template = json.loads(f.read())
                logger.info(f"[parser] 加载模板成功: {file}")
                return template
        except Exception as e:
            logger.error(f"[parser] 加载模板失败: {e}")
            return []

    def add_blacklist(self, umo: str):
        if umo not in self.blacklist:
            self.blacklist.append(umo)
            self.save_config()

    def remove_blacklist(self, umo: str):
        if umo in self.blacklist:
            self.blacklist.remove(umo)
            self.save_config()
