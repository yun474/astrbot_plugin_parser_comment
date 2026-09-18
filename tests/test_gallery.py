from __future__ import annotations

import asyncio
import importlib
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("PIL")
pytest.importorskip("apilmoji")
pytest.importorskip("aiohttp")
pytest.importorskip("yt_dlp")

from PIL import Image

from stubs import install_stubs


@pytest.fixture
def modules(monkeypatch: pytest.MonkeyPatch):
    logs = install_stubs(monkeypatch)
    render = importlib.import_module("core.render")
    sender = importlib.import_module("core.sender")
    data = importlib.import_module("core.data")
    return render, sender, data, logs


def make_cfg(tmp_path: Path, threshold: int = 4):
    return SimpleNamespace(
        emoji_cdn="https://example.invalid/",
        emoji_style="FACEBOOK",
        cache_dir=tmp_path,
        image_merge_threshold=threshold,
        show_download_fail_tip=True,
    )


def write_images(tmp_path: Path) -> list[Path]:
    specs = [
        ("a.jpg", "RGB", (1200, 1600)),  # 竖图
        ("b.png", "RGBA", (800, 400)),  # 透明横图
        ("c.jpg", "RGB", (300, 300)),  # 小方图
        ("d.jpg", "RGB", (500, 3000)),  # 超长图
        ("e.jpg", "RGB", (1920, 1080)),
    ]
    paths = []
    for name, mode, size in specs:
        img = Image.new(
            mode, size, (200, 80, 80, 128) if mode == "RGBA" else (80, 120, 200)
        )
        path = tmp_path / name
        img.save(path)
        paths.append(path)
    return paths


def test_compose_gallery_keeps_order_and_ratio(modules, tmp_path):
    render, _, _, _ = modules
    renderer = render.Renderer(make_cfg(tmp_path))
    paths = write_images(tmp_path)

    out = asyncio.run(renderer.render_gallery(paths))
    assert out is not None and out.suffix == ".jpg"
    with Image.open(out) as img:
        assert img.width == render.Renderer.GALLERY_WIDTH
        # 5 张 -> 3 列 2 行; 每列宽 ~594, 超长图被限制在两倍列宽以内
        cell_w = (render.Renderer.GALLERY_WIDTH - 8 * 4) // 3
        assert img.height <= 8 + (cell_w * 2 + 8) * 2
        assert img.height > cell_w  # 至少放下了一行竖图


def test_merge_gallery_replaces_images_with_one(modules, tmp_path):
    _, sender_module, data, _ = modules
    render = importlib.import_module("core.render")
    cfg = make_cfg(tmp_path, threshold=4)
    sender = sender_module.MessageSender(cfg, render.Renderer(cfg))
    paths = write_images(tmp_path)
    contents = [
        data.TextContent("标题"),
        *[data.ImageContent(p) for p in paths],
        data.VideoContent(tmp_path / "v.mp4"),
    ]

    merged = asyncio.run(sender._merge_gallery(contents))
    kinds = [type(c).__name__ for c in merged]
    assert kinds == ["TextContent", "ImageContent", "VideoContent"]
    assert merged[1].path_task.name.startswith("gallery_")


def test_merge_gallery_respects_threshold(modules, tmp_path):
    _, sender_module, data, _ = modules
    render = importlib.import_module("core.render")
    cfg = make_cfg(tmp_path, threshold=5)
    sender = sender_module.MessageSender(cfg, render.Renderer(cfg))
    contents = [data.ImageContent(p) for p in write_images(tmp_path)]

    assert asyncio.run(sender._merge_gallery(contents)) is contents
    cfg.image_merge_threshold = 0
    assert asyncio.run(sender._merge_gallery(contents)) is contents
