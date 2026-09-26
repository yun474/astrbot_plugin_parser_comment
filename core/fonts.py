"""Pillow 所用的符号后备字体；字符覆盖表随插件附带，不依赖系统字体。"""

import json
from functools import lru_cache
from pathlib import Path

from PIL import ImageFont

FONT_DIR = Path(__file__).parent / "resources" / "fonts"


@lru_cache(maxsize=1)
def symbol_coverage():
    data = json.loads((FONT_DIR / "coverage.json").read_text(encoding="utf-8"))
    return {name: frozenset(chars) for name, chars in data.items()}


@lru_cache(maxsize=8)
def symbol_fonts(size):
    return tuple(
        (chars, ImageFont.truetype(FONT_DIR / name, size))
        for name, chars in symbol_coverage().items()
    )
