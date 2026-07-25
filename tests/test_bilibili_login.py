from __future__ import annotations

import asyncio
import importlib
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def login_module(monkeypatch: pytest.MonkeyPatch):
    root = Path(__file__).resolve().parents[1]

    astrbot_pkg = types.ModuleType("astrbot")
    astrbot_pkg.__path__ = []
    astrbot_api = types.ModuleType("astrbot.api")
    astrbot_api.logger = SimpleNamespace(
        info=lambda *args, **kwargs: None,
        warning=lambda *args, **kwargs: None,
    )

    bilibili_api = types.ModuleType("bilibili_api")
    bilibili_api.Credential = type("Credential", (), {})
    login_v2 = types.ModuleType("bilibili_api.login_v2")
    login_v2.QrCodeLogin = type("QrCodeLogin", (), {})
    login_v2.QrCodeLoginEvents = SimpleNamespace()

    core_pkg = types.ModuleType("core")
    core_pkg.__path__ = [str(root / "core")]
    parsers_pkg = types.ModuleType("core.parsers")
    parsers_pkg.__path__ = [str(root / "core" / "parsers")]
    bilibili_pkg = types.ModuleType("core.parsers.bilibili")
    bilibili_pkg.__path__ = [str(root / "core" / "parsers" / "bilibili")]
    config_module = types.ModuleType("core.config")
    config_module.PluginConfig = object

    modules = {
        "astrbot": astrbot_pkg,
        "astrbot.api": astrbot_api,
        "bilibili_api": bilibili_api,
        "bilibili_api.login_v2": login_v2,
        "core": core_pkg,
        "core.parsers": parsers_pkg,
        "core.parsers.bilibili": bilibili_pkg,
        "core.config": config_module,
    }
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)

    monkeypatch.delitem(
        sys.modules,
        "core.parsers.bilibili.login",
        raising=False,
    )
    return importlib.import_module("core.parsers.bilibili.login")


def test_initially_loaded_expired_credential_is_rejected(login_module):
    login = object.__new__(login_module.BilibiliLogin)
    login._credential = None

    class ExpiredCredential:
        async def check_valid(self):
            return False

    async def fake_init():
        login._credential = ExpiredCredential()

    login._init_credential = fake_init

    assert asyncio.run(login.credential) is None
