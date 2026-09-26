import asyncio
import importlib
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image, ImageChops, ImageDraw
from stubs import install_stubs


@pytest.fixture
def render(monkeypatch):
    install_stubs(monkeypatch)
    return importlib.import_module("core.render")


def test_pillow_symbol_fallback_draws_actual_glyph_and_measures_it(render):
    fonts = render.FontSet.new(render.Renderer.DEFAULT_FONT_PATH)
    info = fonts.text_font
    symbol = info.font_for("⩌")
    assert symbol is not info.font
    assert info.font_for("⩊") is symbol
    assert info.font_for("中") is info.font
    assert info.font_for("😀") is info.font
    assert bytes(symbol.getmask("⩌")) != bytes(symbol.getmask("\U0010ffff"))
    assert info.get_char_width("⩌") == int(symbol.getlength("⩌"))
    actual = Image.new("RGB", (200, 100), "white")
    expected = actual.copy()
    draw = ImageDraw.Draw(expected)
    draw.text(
        (10, 10 + info.font.getmetrics()[0]),
        "⩌⩊⩌",
        font=symbol,
        fill=(0, 0, 0),
        anchor="ls",
    )
    renderer = render.Renderer.__new__(render.Renderer)
    asyncio.run(
        renderer.text(SimpleNamespace(image=actual), (10, 10), ["⩌⩊⩌"], info, (0, 0, 0))
    )
    assert ImageChops.difference(actual, expected).getbbox() is None


def test_fallback_keeps_combined_emoji_intact(render, monkeypatch):
    from unittest.mock import AsyncMock

    info = render.FontSet.new(render.Renderer.DEFAULT_FONT_PATH).text_font
    draw_emoji = AsyncMock()
    monkeypatch.setattr(render.Apilmoji, "text", draw_emoji)
    renderer = render.Renderer.__new__(render.Renderer)
    renderer.EMOJI_SOURCE = object()
    image = Image.new("RGB", (400, 100), "white")
    asyncio.run(
        renderer.text(SimpleNamespace(image=image), (0, 0), ["⩌👩‍💻⩊"], info, (0, 0, 0))
    )
    assert draw_emoji.await_count == 1
    assert draw_emoji.call_args.args[2] == ["👩‍💻"]


def test_bundled_font_files_and_remote_embedding(render):
    from core.html_render import SYMBOL_FONT_PATHS, TEMPLATES, inline_symbol_fonts

    assert all(p.is_file() for p in SYMBOL_FONT_PATHS)
    assert all(uri.startswith("data:font/ttf;base64,") for uri in inline_symbol_fonts())
    html = TEMPLATES.get_template("bili_base.html").render(width=720)
    assert "PluginSymbols1" in html and "PluginSymbols2" in html
    assert all(p.as_uri() in html for p in SYMBOL_FONT_PATHS)
    from core.fonts import symbol_coverage

    assert all(
        (Path(SYMBOL_FONT_PATHS[0].parent) / name).is_file()
        for name in symbol_coverage()
    )
