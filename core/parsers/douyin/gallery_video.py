"""把抖音图集按原顺序合成为单个 MP4，可附带原作品配乐。"""

import asyncio
import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory

from ...data import ImageContent, VideoContent
from ...exception import ParseException
from .music import MusicClip


async def _ffmpeg(args: list[str]) -> None:
    process = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-y",
        *args,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        _, stderr = await asyncio.wait_for(process.communicate(), timeout=180)
    except (asyncio.CancelledError, asyncio.TimeoutError):
        if process.returncode is None:
            process.kill()
        await process.wait()
        raise
    if process.returncode:
        raise RuntimeError(stderr.decode(errors="replace")[-1000:])


async def merge_gallery_video(
    cfg,
    contents,
    duration: float,
    *,
    music_path: Path | None = None,
    music_info: MusicClip | None = None,
) -> VideoContent:
    """合成失败明确报错，不退回逐条发送，避免意外刷屏。"""
    paths = await asyncio.gather(
        *(c.get_path() for c in contents), return_exceptions=True
    )
    if any(isinstance(p, BaseException) for p in paths):
        raise ParseException("抖音图集部分素材下载失败，无法合并视频，请稍后重试")
    if duration > cfg.max_duration:
        raise ParseException("抖音图集合并后时长超过配置限制")

    # 配置与源文件共同参与缓存标识，避免复用不同素材或旧编码产物。
    fingerprint = "720x1280-30fps-still2s-bgm-v3|" + "|".join(
        f"{type(c).__name__}:{p}:{p.stat().st_size}:{p.stat().st_mtime_ns}"
        for c, p in zip(contents, paths)
    )
    clip = music_info or MusicClip()
    if music_path:
        fingerprint += f"|{music_path}:{music_path.stat().st_size}:{music_path.stat().st_mtime_ns}:{clip}"
    digest = hashlib.sha256(fingerprint.encode()).hexdigest()[:20]
    output = cfg.cache_dir / f"douyin_gallery_{digest}.mp4"
    try:
        if not output.exists():
            with TemporaryDirectory(
                prefix="douyin_gallery_", dir=cfg.cache_dir
            ) as temp:
                work = Path(temp)
                for i, (content, path) in enumerate(zip(contents, paths)):
                    args = (
                        ["-loop", "1", "-framerate", "30"]
                        if isinstance(content, ImageContent)
                        else []
                    )
                    args += ["-i", str(path)]
                    if isinstance(content, ImageContent):
                        args += ["-t", "2"]
                    args += [
                        "-map",
                        "0:v:0",
                        "-an",
                        "-vf",
                        "scale=720:1280:force_original_aspect_ratio=decrease:force_divisible_by=2:out_color_matrix=bt709:out_range=tv,"
                        "pad=720:1280:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30,setpts=PTS-STARTPTS",
                        "-c:v",
                        "libx264",
                        "-preset",
                        "veryfast",
                        "-crf",
                        "23",
                        "-pix_fmt",
                        "yuv420p",
                        "-colorspace",
                        "bt709",
                        "-color_primaries",
                        "bt709",
                        "-color_trc",
                        "bt709",
                        "-color_range",
                        "tv",
                        "-threads",
                        "2",
                        str(work / f"{i}.mp4"),
                    ]
                    await _ffmpeg(args)
                playlist = work / "concat.txt"
                playlist.write_text(
                    "".join(f"file '{i}.mp4'\n" for i in range(len(paths))),
                    encoding="utf-8",
                )
                merged = work / "merged.mp4"
                concat_args = [
                    "-f",
                    "concat",
                    "-safe",
                    "1",
                    "-i",
                    str(playlist),
                ]
                if music_path:
                    audio = work / "bgm.m4a"
                    audio_args = [
                        "-ss",
                        str(max(0, clip.begin_time) / 1000),
                        "-i",
                        str(music_path),
                    ]
                    if clip.end_time > clip.begin_time:
                        audio_args += [
                            "-t",
                            str((clip.end_time - clip.begin_time) / 1000),
                        ]
                    audio_args += [
                        "-vn",
                        "-af",
                        f"volume={max(0, clip.volume) / 100}",
                        "-c:a",
                        "aac",
                        "-b:a",
                        "128k",
                        str(audio),
                    ]
                    await _ffmpeg(audio_args)
                    concat_args += [
                        "-stream_loop",
                        "-1",
                        "-i",
                        str(audio),
                        "-map",
                        "0:v:0",
                        "-map",
                        "1:a:0",
                        "-shortest",
                    ]
                concat_args += ["-c", "copy", "-movflags", "+faststart", str(merged)]
                await _ffmpeg(concat_args)
                if merged.stat().st_size > cfg.max_size:
                    raise ParseException("抖音图集合并视频超过资源大小限制")
                merged.replace(output)
        if output.stat().st_size > cfg.max_size:
            raise ParseException("抖音图集合并视频超过资源大小限制")
        return VideoContent(output, duration=duration)
    except (OSError, RuntimeError, asyncio.TimeoutError) as e:
        raise ParseException(f"抖音图集合并失败，请检查 FFmpeg 是否可用：{e}") from e
