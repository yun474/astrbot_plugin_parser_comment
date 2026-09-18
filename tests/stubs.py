"""测试用的 astrbot / core 包桩，让 core 下的模块可以脱离 AstrBot 单独导入"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]


def install_stubs(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """安装最小桩模块，返回收集到的日志列表 (级别: 内容)"""
    logs: list[str] = []

    def record(level: str):
        return lambda message, *args, **kwargs: logs.append(f"{level}: {message}")

    logger = SimpleNamespace(
        debug=record("debug"),
        info=record("info"),
        warning=record("warning"),
        error=record("error"),
        exception=record("error"),
    )

    astrbot_pkg = types.ModuleType("astrbot")
    astrbot_pkg.__path__ = []
    astrbot_api = types.ModuleType("astrbot.api")
    astrbot_api.logger = logger

    core_pkg = types.ModuleType("core")
    core_pkg.__path__ = [str(ROOT / "core")]
    parsers_pkg = types.ModuleType("core.parsers")
    parsers_pkg.__path__ = [str(ROOT / "core" / "parsers")]

    config_module = types.ModuleType("core.config")
    config_module.PluginConfig = object
    config_module.ParserItem = object

    # sender 依赖的消息组件, 只要能构造即可
    components = types.ModuleType("astrbot.core.message.components")

    class BaseMessageComponent:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

        @classmethod
        def fromBytes(cls, data):
            return cls(data=data)

        @classmethod
        def fromFileSystem(cls, path):
            return cls(file=path)

    components.BaseMessageComponent = BaseMessageComponent
    for name in ("Plain", "Image", "Video", "Record", "File", "Node", "Nodes"):
        setattr(components, name, type(name, (BaseMessageComponent,), {}))
    event_module = types.ModuleType("astrbot.core.platform.astr_message_event")
    event_module.AstrMessageEvent = type("AstrMessageEvent", (), {})

    modules = {
        "astrbot": astrbot_pkg,
        "astrbot.api": astrbot_api,
        "astrbot.core.message.components": components,
        "astrbot.core.platform.astr_message_event": event_module,
        "core": core_pkg,
        "core.parsers": parsers_pkg,
        "core.config": config_module,
    }
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)
    for name in [
        "core.sender",
        "core.render",
        "core.download",
        "core.utils",
        "core.exception",
        "core.constants",
        "core.data",
        "core.cookie",
        "core.parsers.base",
        "core.parsers.douyin",
        "core.parsers.douyin.video",
        "core.parsers.douyin.slides",
    ]:
        monkeypatch.delitem(sys.modules, name, raising=False)
    return logs
