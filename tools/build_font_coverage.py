"""更新内置字体后运行：python -m pip install fonttools && python tools/build_font_coverage.py。

只生成原中文字体缺失的字符索引，不修改字体文件；运行插件不需要 fonttools。
Emoji 继续交给 Apilmoji，避免拆开组合表情。
"""

import json
from pathlib import Path

from emoji import EMOJI_DATA
from fontTools.ttLib import TTFont

resources = Path(__file__).resolve().parents[1] / "core" / "resources"
covered = set(TTFont(resources / "HYSongYunLangHeiW-1.ttf").getBestCmap())
covered.update(ord(c) for sequence in EMOJI_DATA for c in sequence)
coverage = {}
for name in ("NotoSansMath-Regular.ttf", "NotoSansSymbols2-Regular.ttf"):
    codepoints = set(TTFont(resources / "fonts" / name).getBestCmap()) - covered
    coverage[name] = "".join(chr(cp) for cp in sorted(codepoints) if cp >= 32)
    covered.update(codepoints)
(resources / "fonts" / "coverage.json").write_text(
    json.dumps(coverage, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
)
