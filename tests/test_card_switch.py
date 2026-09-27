"""隔离加载入口匹配方法，验证卡片开关不影响普通链接。"""

import ast
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("enabled", [None, False, True])
def test_bilibili_card_switch(enabled):
    source = ast.parse((ROOT / "main.py").read_text(encoding="utf-8"))
    cls = next(node for node in source.body if isinstance(node, ast.ClassDef))
    method = next(
        node for node in cls.body if getattr(node, "name", "") == "_match_link"
    )
    parser_type = type("BilibiliParser", (), {})
    namespace = {"re": re, "BilibiliParser": parser_type}
    exec(  # noqa: S102 - 仅执行仓库内的单个方法，避免加载 AstrBot 运行时
        compile(ast.Module(body=[method], type_ignores=[]), "main.py", "exec"),
        namespace,
    )
    parser = parser_type()
    parser.mycfg = SimpleNamespace(miniapp_parse_enable=enabled)
    owner = SimpleNamespace(
        parser_map={
            "b23.tv": parser,
            "BV": parser,
            "[卡片消息]": parser,
            "douyin": object(),
        },
        key_pattern_list=[
            (key, re.compile(re.escape(key)))
            for key in ("b23.tv", "BV", "[卡片消息]", "douyin")
        ],
    )
    match = namespace["_match_link"]
    assert match(owner, "b23.tv/example") is not None
    assert match(owner, "BV1GJ411x7h7") is not None
    assert (match(owner, "b23.tv/example", is_card=True) is not None) == bool(enabled)
    assert (match(owner, "[卡片消息] 标题") is not None) == bool(enabled)
    assert match(owner, "douyin", is_card=True) is not None


def test_card_switch_defaults_off():
    schema = json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8"))
    assert (
        schema["parsers_template"]["templates"]["bilibili"]["items"][
            "miniapp_parse_enable"
        ]["default"]
        is False
    )
    templates = json.loads((ROOT / "default_template.json").read_text(encoding="utf-8"))
    bili = next(item for item in templates if item["__template_key"] == "bilibili")
    assert bili["miniapp_parse_enable"] is False
