from __future__ import annotations

import asyncio
import importlib
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from stubs import install_stubs

pytest.importorskip("aiohttp")
msgspec = pytest.importorskip("msgspec")
pytest.importorskip("yt_dlp")

CIMultiDict = pytest.importorskip("multidict").CIMultiDict


@pytest.fixture
def modules(monkeypatch):
    install_stubs(monkeypatch)
    for name in (
        "core.parsers.douyin.comments",
        "core.parsers.douyin.gallery",
        "core.parsers.douyin.gallery_video",
    ):
        monkeypatch.delitem(sys.modules, name, raising=False)
    return SimpleNamespace(
        parser=importlib.import_module("core.parsers.douyin"),
        video=importlib.import_module("core.parsers.douyin.video"),
        data=importlib.import_module("core.data"),
        comments=importlib.import_module("core.parsers.douyin.comments"),
    )


def gallery_payload():
    return {
        "author": {"nickname": "作者", "avatar_thumb": {"url_list": []}},
        "desc": "静态与动态混合图集",
        "create_time": 1,
        "images": [
            {"url_list": ["https://cdn/static.jpg"]},
            {
                "url_list": ["https://cdn/cover.jpg"],
                "video": {
                    "play_addr": {
                        "url_list": ["https://cdn/live.mp4", "https://backup/live.mp4"]
                    },
                    "cover": {"url_list": ["https://cdn/cover.jpg"]},
                    "duration": 3000,
                },
            },
            {
                "url_list": ["https://cdn/last.jpg", "https://backup/last.jpg"],
                "video": {
                    "play_addr": {"url_list": []},
                    "cover": {"url_list": []},
                    "duration": 0,
                },
            },
        ],
    }


@pytest.mark.parametrize("entry", ["note", "video", "slides"])
def test_gallery_downloads_dynamic_media_instead_of_cover(modules, entry):
    async def run():
        parser = modules.parser.DouyinParser.__new__(modules.parser.DouyinParser)
        parser.cfg = SimpleNamespace()
        parser.mycfg = SimpleNamespace(
            live_photo_enable=True, gallery_merge_video=False
        )
        parser.headers = {}
        parser.ios_headers = {"User-Agent": "ios"}
        parser.android_headers = {"User-Agent": "android"}
        parser.downloader = SimpleNamespace(
            download_img=Mock(return_value=Path("image.jpg")),
            download_video=Mock(return_value=Path("live.mp4")),
        )
        parser.create_author = Mock(return_value=None)
        parser.comment_service = SimpleNamespace(
            build_send_groups=Mock(return_value=[])
        )
        payload = gallery_payload()
        if entry == "slides":
            response = SimpleNamespace(
                status=200,
                headers=CIMultiDict(),
                raise_for_status=Mock(),
                read=AsyncMock(
                    return_value=msgspec.json.encode({"aweme_details": [payload]})
                ),
            )
            request = AsyncMock()
            request.__aenter__.return_value = response
            parser._session = SimpleNamespace(
                closed=False, get=Mock(return_value=request)
            )
            parser.cookiejar = SimpleNamespace(update_from_response=Mock())
            parser._set_cookies = Mock()
            result = await parser.parse_slides("123")
        else:
            data = msgspec.convert(payload, type=modules.video.VideoData)
            parser.ensure_ttwid = AsyncMock()
            parser._load_share_data = AsyncMock(
                return_value=("https://example.com", data)
            )
            result = await parser.parse_video(entry, "123")

        assert [type(c) for c in result.contents] == [
            modules.data.ImageContent,
            modules.data.DynamicContent,
            modules.data.ImageContent,
        ]
        images = parser.downloader.download_img.call_args_list
        assert [c.args[0] for c in images] == [
            "https://cdn/static.jpg",
            "https://cdn/last.jpg",
        ]
        assert images[1].kwargs["backup_urls"] == ["https://backup/last.jpg"]
        assert images[0].kwargs["headers"]["Referer"] == "https://www.douyin.com/"
        parser.downloader.download_video.assert_called_once_with(
            "https://cdn/live.mp4",
            headers={
                **(parser.android_headers if entry == "slides" else parser.ios_headers),
                "Referer": "https://www.douyin.com/",
            },
            proxy=None,
            backup_urls=["https://backup/live.mp4"],
        )
        assert (
            parser.comment_service.build_send_groups.call_args.kwargs["kind"] == "note"
        )

    asyncio.run(run())


@pytest.mark.parametrize(
    "enabled,limit,merge",
    [(False, 9, False), (True, 0, False), (True, 9, False), (True, 9, True)],
)
def test_dynamic_gallery_keeps_order_with_or_without_comments(
    modules, enabled, limit, merge
):
    async def run():
        service = modules.comments.DouyinCommentService.__new__(
            modules.comments.DouyinCommentService
        )
        service.enabled, service.limit, service.merge = enabled, limit, merge
        service._build_image = AsyncMock(return_value=Path("comments.jpg"))
        contents = [
            modules.data.DynamicContent(Path("live.mp4")),
            modules.data.ImageContent(Path("static.jpg")),
        ]
        groups = service.build_send_groups(contents, "123", "图集", "作者", kind="note")
        assert groups[0].preserve_order is True
        assert groups[0].contents[:2] == contents
        if enabled and limit:
            await asyncio.gather(
                *(
                    c.path_task
                    for g in groups
                    for c in g.contents
                    if isinstance(c.path_task, asyncio.Task)
                )
            )
        else:
            service._build_image.assert_not_called()

    asyncio.run(run())


@pytest.mark.parametrize("failure", [None, "request", "wrong_id", "missing_image"])
def test_web_gallery_enrichment_and_context_cleanup(modules, failure):
    async def run():
        gallery = importlib.import_module("core.parsers.douyin.gallery")
        payload = gallery_payload()
        originals = msgspec.convert(
            [
                {"url_list": [f"https://mobile-{n}/image.webp", *i["url_list"]]}
                for n, i in enumerate(payload["images"])
            ],
            type=list[modules.video.Image],
        )
        detail = {"aweme_id": "123", **payload}
        for n, item in enumerate(detail["images"]):
            item["url_list"] = [*item["url_list"], f"https://pc-{n}/image.webp"]
        if failure == "wrong_id":
            detail["aweme_id"] = "456"
        if failure == "missing_image":
            detail["images"] = detail["images"][:1]
        page = SimpleNamespace(
            route=AsyncMock(),
            goto=AsyncMock(),
            evaluate=AsyncMock(return_value={"status_code": 0, "aweme_detail": detail}),
        )
        if failure == "request":
            page.evaluate.side_effect = RuntimeError("detail unavailable")
        context = SimpleNamespace(
            new_page=AsyncMock(return_value=page),
            close=AsyncMock(),
            add_cookies=AsyncMock(),
        )
        browser = SimpleNamespace(new_context=AsyncMock(return_value=context))
        parser = SimpleNamespace(
            html_renderer=SimpleNamespace(_get_browser=AsyncMock(return_value=browser)),
            cookiejar=SimpleNamespace(cookies=[]),
            proxy=None,
        )
        result = await gallery.load_gallery_images(parser, "123", originals)
        if failure:
            assert result is originals
        else:
            for n, (original, enriched) in enumerate(zip(originals, result)):
                assert enriched.url_list == [
                    *original.url_list,
                    f"https://pc-{n}/image.webp",
                ]
            assert result[1].video.play_addr.url_list == [
                "https://cdn/live.mp4",
                "https://backup/live.mp4",
            ]
        context.close.assert_awaited_once()
        assert page.goto.call_args.kwargs["wait_until"] == "load"
        assert page.goto.call_args.kwargs["timeout"] == 60000
        page.route.assert_not_called()
        assert "AbortSignal.timeout(30000)" in page.evaluate.call_args.args[0]
        page.evaluate.assert_awaited_once()

    asyncio.run(run())


def test_complete_gallery_does_not_launch_browser(modules):
    gallery = importlib.import_module("core.parsers.douyin.gallery")
    images = msgspec.convert(
        gallery_payload()["images"], type=list[modules.video.Image]
    )
    parser = SimpleNamespace(html_renderer=SimpleNamespace(_get_browser=AsyncMock()))
    assert asyncio.run(gallery.load_gallery_images(parser, "123", images)) is images
    parser.html_renderer._get_browser.assert_not_called()


@pytest.mark.parametrize(
    "live,merge", [(False, False), (False, True), (True, False), (True, True)]
)
def test_gallery_switch_combinations(modules, monkeypatch, live, merge):
    async def run():
        gallery = importlib.import_module("core.parsers.douyin.gallery")
        video = importlib.import_module("core.parsers.douyin.gallery_video")
        images = msgspec.convert(
            gallery_payload()["images"], type=list[modules.video.Image]
        )
        enrich = AsyncMock(return_value=images)
        combine = AsyncMock(return_value=modules.data.VideoContent(Path("merged.mp4")))
        monkeypatch.setattr(gallery, "load_gallery_images", enrich)
        monkeypatch.setattr(video, "merge_gallery_video", combine)
        parser = modules.parser.DouyinParser.__new__(modules.parser.DouyinParser)
        parser.cfg = SimpleNamespace()
        parser.mycfg = SimpleNamespace(
            live_photo_enable=live, gallery_merge_video=merge
        )
        parser.headers = {}
        parser.downloader = SimpleNamespace(
            download_img=Mock(return_value=Path("image.jpg")),
            download_video=Mock(return_value=Path("live.mp4")),
        )
        parser.comment_service = SimpleNamespace(
            build_send_groups=Mock(return_value=[])
        )
        contents = await parser._prepare_gallery("123", images, {})
        assert enrich.await_count == int(live)
        assert parser.downloader.download_video.call_count == int(live)
        assert parser.downloader.download_img.call_count == (2 if live else 3)
        if merge:
            assert len(contents) == 1
            assert isinstance(contents[0], modules.data.VideoContent)
            assert combine.call_args.args[2] == (7 if live else 6)
            groups = parser._gallery_send_groups(
                contents, "123", "图集", "作者", kind="note"
            )
            assert groups[0].contents == contents
            assert groups[0].render_card is False
        else:
            assert len(contents) == 3
            combine.assert_not_called()

    asyncio.run(run())


@pytest.mark.parametrize(
    "static_count,dynamic_count",
    [
        (0, 0),
        (1, 0),
        (2, 0),
        (3, 0),
        (4, 0),
        (0, 1),
        (0, 2),
        (1, 1),
    ],
)
@pytest.mark.parametrize("bgm", ["available", "missing", "failed"])
def test_gallery_merge_threshold(
    modules, monkeypatch, static_count, dynamic_count, bgm
):
    async def run():
        gallery = importlib.import_module("core.parsers.douyin.gallery")
        video = importlib.import_module("core.parsers.douyin.gallery_video")
        music_module = importlib.import_module("core.parsers.douyin.music")
        source = gallery_payload()["images"]
        images = msgspec.convert(
            [source[0]] * static_count + [source[1]] * dynamic_count,
            type=list[modules.video.Image],
        )
        monkeypatch.setattr(
            gallery, "load_gallery_images", AsyncMock(return_value=images)
        )
        merged = modules.data.VideoContent(Path("merged.mp4"))
        combine = AsyncMock(return_value=merged)
        monkeypatch.setattr(video, "merge_gallery_video", combine)
        parser = modules.parser.DouyinParser.__new__(modules.parser.DouyinParser)
        parser.cfg = SimpleNamespace()
        parser.mycfg = SimpleNamespace(live_photo_enable=True, gallery_merge_video=True)
        parser.headers = {}
        parser.downloader = SimpleNamespace(
            download_img=Mock(return_value=Path("image.jpg")),
            download_video=Mock(return_value=Path("live.mp4")),
            download_audio=AsyncMock(return_value=Path("bgm.mp3")),
        )
        music = msgspec.convert(
            {"play_url": {"url_list": ["https://cdn/bgm.mp3"]}}, type=music_module.Music
        )
        if bgm == "missing":
            music = None
        elif bgm == "failed":
            exception = importlib.import_module("core.exception")
            parser.downloader.download_audio.side_effect = exception.DownloadException()
        should_merge = (
            dynamic_count > 0
            or static_count >= 3
            or (static_count == 2 and bgm == "available")
        )
        contents = await parser._prepare_gallery("123", images, {}, music)
        if should_merge:
            assert contents == [merged]
            combine.assert_awaited_once()
            assert combine.call_args.args[2] == static_count * 2 + dynamic_count * 3
            assert combine.call_args.kwargs["music_path"] == (
                Path("bgm.mp3") if bgm == "available" else None
            )
        else:
            assert len(contents) == static_count
            assert all(isinstance(c, modules.data.ImageContent) for c in contents)
            combine.assert_not_awaited()
        if bgm != "missing" and (static_count >= 2 or dynamic_count > 0):
            parser.downloader.download_audio.assert_awaited_once()
        else:
            parser.downloader.download_audio.assert_not_awaited()

    asyncio.run(run())


def test_merge_failure_does_not_return_individual_media(modules, monkeypatch):
    async def run():
        video = importlib.import_module("core.parsers.douyin.gallery_video")
        exception = importlib.import_module("core.exception")
        monkeypatch.setattr(
            video,
            "merge_gallery_video",
            AsyncMock(side_effect=exception.ParseException("合成失败")),
        )
        parser = modules.parser.DouyinParser.__new__(modules.parser.DouyinParser)
        parser.cfg = SimpleNamespace()
        parser.mycfg = SimpleNamespace(
            live_photo_enable=False, gallery_merge_video=True
        )
        parser.headers = {}
        parser.downloader = SimpleNamespace(
            download_img=Mock(return_value=Path("image.jpg"))
        )
        images = msgspec.convert(
            gallery_payload()["images"], type=list[modules.video.Image]
        )
        images.append(images[0])
        with pytest.raises(exception.ParseException, match="合成失败"):
            await parser._prepare_gallery("123", images, {})

    asyncio.run(run())


@pytest.mark.parametrize("failure", [False, True])
def test_gallery_bgm_download_and_optional_fallback(modules, monkeypatch, failure):
    async def run():
        video = importlib.import_module("core.parsers.douyin.gallery_video")
        music_module = importlib.import_module("core.parsers.douyin.music")
        exception = importlib.import_module("core.exception")
        combine = AsyncMock(return_value=modules.data.VideoContent(Path("merged.mp4")))
        monkeypatch.setattr(video, "merge_gallery_video", combine)
        parser = modules.parser.DouyinParser.__new__(modules.parser.DouyinParser)
        parser.cfg = SimpleNamespace()
        parser.mycfg = SimpleNamespace(
            live_photo_enable=False, gallery_merge_video=True
        )
        parser.headers = {}
        parser.downloader = SimpleNamespace(
            download_img=Mock(return_value=Path("image.jpg")),
            download_audio=AsyncMock(return_value=Path("bgm.mp3")),
        )
        if failure:
            parser.downloader.download_audio.side_effect = exception.DownloadException()
        images = msgspec.convert(
            gallery_payload()["images"], type=list[modules.video.Image]
        )
        music = msgspec.convert(
            {"play_url": {"url_list": ["https://cdn/bgm.mp3"]}}, type=music_module.Music
        )
        info = music_module.MusicClip(begin_time=1000, end_time=3000, volume=50)
        images.append(images[0])
        result = await parser._prepare_gallery("123", images, {}, music, info)
        assert len(result) == 1
        assert combine.call_args.kwargs["music_info"] == info
        assert combine.call_args.kwargs["music_path"] == (
            None if failure else Path("bgm.mp3")
        )
        parser.downloader.download_audio.assert_awaited_once()

    asyncio.run(run())


def test_ffmpeg_merge_retains_order_and_loops_bgm(modules, tmp_path):
    import json
    import shutil
    import subprocess

    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("FFmpeg integration requires ffmpeg and ffprobe")
    pil = pytest.importorskip("PIL.Image")
    video = importlib.import_module("core.parsers.douyin.gallery_video")
    music = importlib.import_module("core.parsers.douyin.music")

    async def run():
        red, blue = tmp_path / "red.png", tmp_path / "blue.png"
        pil.new("RGB", (32, 32), "red").save(red)
        pil.new("RGB", (64, 32), "blue").save(blue)
        green, bgm = tmp_path / "green.mp4", tmp_path / "tone.wav"
        await video._ffmpeg(
            [
                "-f",
                "lavfi",
                "-i",
                "color=c=lime:s=32x64:r=30:d=1",
                "-c:v",
                "libx264",
                str(green),
            ]
        )
        await video._ffmpeg(
            ["-f", "lavfi", "-i", "sine=frequency=440:duration=1", str(bgm)]
        )
        cfg = SimpleNamespace(
            cache_dir=tmp_path, max_size=10 * 1024 * 1024, max_duration=10
        )
        contents = [
            modules.data.ImageContent(red),
            modules.data.DynamicContent(green),
            modules.data.ImageContent(blue),
        ]
        result = await video.merge_gallery_video(
            cfg,
            contents,
            5,
            music_path=bgm,
            music_info=music.MusicClip(begin_time=200, end_time=800, volume=50),
        )
        path = await result.get_path()
        probe = json.loads(
            subprocess.check_output(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-show_streams",
                    "-show_format",
                    "-of",
                    "json",
                    str(path),
                ]
            )
        )
        assert {s["codec_name"] for s in probe["streams"]} == {"h264", "aac"}
        assert float(probe["format"]["duration"]) == pytest.approx(5, abs=0.15)
        for time, channel in [(0.5, 0), (2.5, 1), (3.5, 2)]:
            pixel = subprocess.check_output(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-ss",
                    str(time),
                    "-i",
                    str(path),
                    "-vf",
                    "crop=16:16,scale=1:1",
                    "-frames:v",
                    "1",
                    "-f",
                    "rawvideo",
                    "-pix_fmt",
                    "rgb24",
                    "pipe:1",
                ]
            )
            assert pixel[channel] > 200
            assert sum(pixel) - pixel[channel] < 40
        assert not list(tmp_path.glob("douyin_gallery_*/"))

    asyncio.run(run())
