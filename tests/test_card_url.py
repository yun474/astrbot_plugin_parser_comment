"""分享卡片 (QQ 小程序 / 结构化消息) 链接提取"""

from __future__ import annotations

import importlib
import json
import sys

import pytest
from stubs import install_stubs


@pytest.fixture
def utils(monkeypatch: pytest.MonkeyPatch):
    install_stubs(monkeypatch)
    monkeypatch.delitem(sys.modules, "core.utils", raising=False)
    return importlib.import_module("core.utils")


MINIAPP_CARD = {
    "app": "com.tencent.miniapp_01",
    "prompt": "[QQ小程序]哔哩哔哩",
    "meta": {
        "detail_1": {
            "appid": "1109937557",
            "title": "哔哩哔哩",
            "desc": "视频标题",
            "icon": "https://open.gtimg.cn/open/app_icon/01/10/99/37/1109937557_100_m.png",
            "preview": "pubminishare-30161.picsz.qpic.cn/abc",
            "qqdocurl": "https:\\/\\/b23.tv\\/AbCdEf?share_medium=android&share_source=qq",
            "url": "m.q.qq.com/a/s/xxxx",
        }
    },
}

STRUCT_CARD = {
    "app": "com.tencent.structmsg",
    "meta": {
        "news": {
            "jumpUrl": "https://www.bilibili.com/video/BV1GJ411x7h7?p=2",
            "preview": "https://i0.hdslb.com/bfs/archive/x.jpg",
        }
    },
}


def test_miniapp_card_prefers_qqdocurl(utils):
    assert utils.extract_json_url(MINIAPP_CARD) == (
        "https://b23.tv/AbCdEf?share_medium=android&share_source=qq"
    )
    # 字符串形式 (NapCat 原样透传) 也能解析
    assert utils.extract_json_url(json.dumps(MINIAPP_CARD)) == (
        "https://b23.tv/AbCdEf?share_medium=android&share_source=qq"
    )


def test_struct_card_uses_jump_url(utils):
    assert utils.extract_json_url(STRUCT_CARD) == (
        "https://www.bilibili.com/video/BV1GJ411x7h7?p=2"
    )


def test_deep_scan_prefers_platform_links_over_icons(utils):
    card = {
        "meta": {
            "detail_1": {
                "icon": "https://open.gtimg.cn/icon.png",
                "shareTemplateData": {"link": "https://b23.tv/deep"},
            }
        }
    }
    assert utils.extract_json_url(card) == "https://b23.tv/deep"


def test_garbage_input(utils):
    assert utils.extract_json_url("not json") is None
    assert utils.extract_json_url({"meta": {"detail_1": {"title": "无链接"}}}) is None
    assert utils.extract_json_url([]) is None
