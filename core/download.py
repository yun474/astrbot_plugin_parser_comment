from asyncio import (
    Task,
    TimeoutError,
    create_task,
    current_task,
    gather,
    shield,
    sleep,
    to_thread,
)
from collections.abc import Callable, Coroutine, Sequence
from functools import wraps
from pathlib import Path
from typing import Any, ParamSpec, TypeVar

import aiofiles
import yt_dlp
from aiohttp import ClientError, ClientPayloadError, ClientSession, ClientTimeout
from msgspec import Struct, convert
from tqdm.asyncio import tqdm

from astrbot.api import logger

from .config import PluginConfig
from .constants import COMMON_HEADER
from .exception import (
    DownloadException,
    DurationLimitException,
    ParseException,
    SizeLimitException,
    ZeroSizeException,
)
from .utils import LimitedSizeDict, generate_file_name, merge_av, safe_unlink

P = ParamSpec("P")
T = TypeVar("T")

MEDIA_SUFFIXES = frozenset(
    {
        ".mp4",
        ".mov",
        ".webm",
        ".mp3",
        ".m4a",
        ".flac",
        ".jpg",
        ".jpeg",
        ".png",
        ".webp",
        ".gif",
    }
)


def auto_task(func: Callable[P, Coroutine[Any, Any, T]]) -> Callable[P, Task[T]]:
    """装饰器：自动将异步函数调用转换为 Task, 完整保留类型提示"""

    @wraps(func)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> Task[T]:
        coro = func(*args, **kwargs)
        name = " | ".join(str(arg) for arg in args if isinstance(arg, str))
        task = create_task(coro, name=func.__name__ + " | " + name)
        # 封面、头像等任务可能无人 await，标记异常已取回，避免 asyncio 在回收时刷屏
        task.add_done_callback(_retrieve_exception)
        return task

    return wrapper


def _retrieve_exception(task: Task) -> None:
    if not task.cancelled():
        task.exception()


def describe_error(exc: BaseException) -> str:
    """把异常压成一句话，用于日志和用户提示"""
    text = str(exc) or type(exc).__name__
    return text if len(text) <= 120 else text[:117] + "..."


class VideoInfo(Struct):
    title: str
    """标题"""
    channel: str
    """频道名称"""
    uploader: str
    """上传者 id"""
    duration: int
    """时长"""
    timestamp: int
    """发布时间戳"""
    thumbnail: str
    """封面图片"""
    description: str
    """简介"""
    channel_id: str
    """频道 id"""

    @property
    def author_name(self) -> str:
        return f"{self.channel}@{self.uploader}"


class Downloader:
    """下载器，支持youtube-dlp 和 流式下载"""

    def __init__(self, config: PluginConfig):
        self.cfg = config
        self.default_headers: dict[str, str] = COMMON_HEADER.copy()
        # 视频信息缓存
        self.info_cache: LimitedSizeDict[str, VideoInfo] = LimitedSizeDict()
        # 正在下载中的文件 -> 下载任务，同一文件只下载一次
        self._inflight: dict[Path, Task[Path]] = {}
        # 用于流式下载的客户端
        self.client = ClientSession(
            timeout=ClientTimeout(total=self.cfg.download_timeout)
        )

    async def close(self):
        """关闭网络客户端"""
        await self.client.close()

    @auto_task
    async def streamd(
        self,
        url: str,
        *,
        file_name: str | None = None,
        headers: dict[str, str] | None = None,
        proxy: str | None = None,
        backup_urls: Sequence[str] = (),
    ) -> Path:
        """流式下载

        Args:
            backup_urls: 同一资源的备用直链，主链接失败后按顺序轮换重试
        """
        if not file_name:
            file_name = generate_file_name(url)
        file_path = self.cfg.cache_dir / file_name
        # 如果文件存在，则直接返回
        if file_path.exists():
            return file_path
        # 同一文件正在下载时复用该任务，避免读到写了一半的文件
        if (inflight := self._inflight.get(file_path)) is not None:
            return await shield(inflight)
        self._inflight[file_path] = current_task()  # type: ignore[assignment]
        try:
            return await self._streamd(
                [url, *backup_urls], file_path, headers or self.default_headers, proxy
            )
        finally:
            self._inflight.pop(file_path, None)

    async def _streamd(
        self,
        urls: list[str],
        file_path: Path,
        headers: dict[str, str],
        proxy: str | None,
    ) -> Path:
        # 先写入 .part 临时文件，下载完整后再改名，保证缓存目录里只有完整文件
        part_path = file_path.with_name(file_path.name + ".part")
        max_bytes = self.cfg.max_size
        attempts = max(self.cfg.download_retry_times + 1, len(urls))
        for attempt in range(attempts):
            url = urls[attempt % len(urls)]
            try:
                async with self.client.get(
                    url, headers=headers, allow_redirects=True, proxy=proxy
                ) as response:
                    if response.status >= 400:
                        raise ClientError(f"HTTP {response.status} {response.reason}")
                    # 风控/验证页会以 200 + HTML 返回, 别把它当成媒体存下来
                    if (
                        response.content_type == "text/html"
                        and file_path.suffix.lower() in MEDIA_SUFFIXES
                    ):
                        raise ClientError("服务器返回了网页而不是媒体文件")
                    content_length = response.content_length

                    if content_length == 0:
                        raise ZeroSizeException
                    if content_length and content_length > max_bytes:
                        logger.warning(
                            f"媒体大小 {content_length / 1024 / 1024:.2f} MB 超过 {max_bytes / 1024 / 1024:.0f} MB, 取消下载 | url: {url}"
                        )
                        raise SizeLimitException

                    downloaded = 0
                    with self.get_progress_bar(file_path.name, content_length) as bar:
                        async with aiofiles.open(part_path, "wb") as file:
                            async for chunk in response.content.iter_chunked(
                                1024 * 1024
                            ):
                                downloaded += len(chunk)
                                if downloaded > max_bytes:
                                    raise SizeLimitException
                                await file.write(chunk)
                                bar.update(len(chunk))

                    if downloaded == 0:
                        raise ZeroSizeException
                    if content_length and downloaded < content_length:
                        raise ClientPayloadError(
                            f"数据不完整 {downloaded}/{content_length}"
                        )

                await to_thread(part_path.replace, file_path)
                return file_path
            except (ZeroSizeException, SizeLimitException) as exc:
                await safe_unlink(part_path)
                if isinstance(exc, ZeroSizeException):
                    logger.warning(f"{exc.message} | url: {url}")
                raise
            except (ClientError, TimeoutError, OSError) as exc:
                await safe_unlink(part_path)
                reason = describe_error(exc)
                if attempt < attempts - 1:
                    logger.warning(
                        f"下载失败: {reason}, 准备第 {attempt + 1} 次重试 | url: {url}"
                    )
                    await sleep(1 + attempt)
                    continue
                logger.error(f"下载失败: {reason}, 已放弃 | url: {url}")
                raise DownloadException(f"媒体下载失败: {reason}") from exc
        raise DownloadException("媒体下载失败")

    @staticmethod
    def get_progress_bar(desc: str, total: int | None = None) -> tqdm:
        """获取进度条 bar

        Args:
            desc (str): 描述
            total (int | None): 总大小. Defaults to None.

        Returns:
            tqdm: 进度条
        """
        return tqdm(
            total=total,
            unit="B",
            unit_scale=True,
            unit_divisor=1024,
            dynamic_ncols=True,
            colour="green",
            desc=desc,
        )

    @auto_task
    async def download_video(
        self,
        url: str,
        *,
        video_name: str | None = None,
        headers: dict[str, str] | None = None,
        proxy: str | None = None,
        backup_urls: Sequence[str] = (),
    ) -> Path:
        if video_name is None:
            video_name = generate_file_name(url, ".mp4")
        return await self.streamd(
            url,
            file_name=video_name,
            headers=headers,
            proxy=proxy,
            backup_urls=backup_urls,
        )

    @auto_task
    async def download_audio(
        self,
        url: str,
        *,
        audio_name: str | None = None,
        headers: dict[str, str] | None = None,
        proxy: str | None = None,
    ) -> Path:
        if audio_name is None:
            audio_name = generate_file_name(url, ".mp3")
        return await self.streamd(
            url, file_name=audio_name, headers=headers, proxy=proxy
        )

    @auto_task
    async def download_file(
        self,
        url: str,
        *,
        file_name: str | None = None,
        headers: dict[str, str] | None = None,
        proxy: str | None = None,
    ) -> Path:
        if file_name is None:
            file_name = generate_file_name(url, ".zip")
        return await self.streamd(
            url, file_name=file_name, headers=headers, proxy=proxy
        )

    @auto_task
    async def download_img(
        self,
        url: str,
        *,
        img_name: str | None = None,
        headers: dict[str, str] | None = None,
        proxy: str | None = None,
        backup_urls: Sequence[str] = (),
    ) -> Path:
        if img_name is None:
            img_name = generate_file_name(url, ".jpg")
        return await self.streamd(
            url,
            file_name=img_name,
            headers=headers,
            proxy=proxy,
            backup_urls=backup_urls,
        )

    async def download_imgs_without_raise(
        self,
        urls: list[str],
        *,
        headers: dict[str, str] | None = None,
        proxy: str | None = None,
    ) -> list[Path]:
        paths_or_errs = await gather(
            *[self.download_img(url, headers=headers, proxy=proxy) for url in urls],
            return_exceptions=True,
        )
        return [p for p in paths_or_errs if isinstance(p, Path)]

    @auto_task
    async def download_av_and_merge(
        self,
        v_url: str,
        a_url: str,
        *,
        output_path: Path,
        headers: dict[str, str] | None = None,
        proxy: str | None = None,
    ) -> Path:
        """
        download video and audio file by url with stream and merge
        """
        v_path, a_path = await gather(
            self.download_video(v_url, headers=headers, proxy=proxy),
            self.download_audio(a_url, headers=headers, proxy=proxy),
        )
        await merge_av(v_path=v_path, a_path=a_path, output_path=output_path)
        return output_path

    async def ytdlp_extract_info(
        self,
        url: str,
        *,
        cookiefile: Path | None = None,
        headers: dict[str, str] | None = None,
        proxy: str | None = None,
        format: str | None = None,
    ) -> VideoInfo:
        if (info := self.info_cache.get(url)) is not None:
            return info
        opts = {
            "quiet": True,
            "skip_download": True,
            "http_headers": headers or self.default_headers,
        }
        if proxy:
            opts["proxy"] = proxy
        if cookiefile and cookiefile.is_file():
            opts["cookiefile"] = str(cookiefile)
        if format:
            opts["format"] = format
        with yt_dlp.YoutubeDL(opts) as ydl:  # type: ignore
            raw = await to_thread(ydl.extract_info, url, download=False)
            if not raw:
                raise ParseException("获取视频信息失败")
        info = convert(raw, VideoInfo)
        self.info_cache[url] = info
        return info

    async def ytdlp_extract_raw(
        self,
        url: str,
        *,
        cookiefile: Path | None = None,
        headers: dict[str, str] | None = None,
        proxy: str | None = None,
        format: str | None = None,
    ) -> dict[str, Any]:
        opts = {
            "quiet": True,
            "skip_download": True,
            "http_headers": headers or self.default_headers,
        }
        if proxy:
            opts["proxy"] = proxy
        if cookiefile and cookiefile.is_file():
            opts["cookiefile"] = str(cookiefile)
        if format:
            opts["format"] = format

        with yt_dlp.YoutubeDL(opts) as ydl:  # type: ignore
            raw = await to_thread(ydl.extract_info, url, download=False)
            if not isinstance(raw, dict):
                raise ParseException("yt-dlp 返回数据异常")
            return raw  # type: ignore

    @auto_task
    async def ytdlp_download_video(
        self,
        url: str,
        *,
        cookiefile: Path | None = None,
        headers: dict[str, str] | None = None,
        proxy: str | None = None,
        format: str | None = None,
        node: bool = False,
    ) -> Path:
        info = await self.ytdlp_extract_info(
            url, cookiefile=cookiefile, headers=headers, proxy=proxy
        )
        if info.duration > self.cfg.max_duration:
            logger.warning(
                f"媒体时长 {info.duration}s 超过 {self.cfg.max_duration}s, 取消下载 | url: {url}"
            )
            raise DurationLimitException

        video_path = self.cfg.cache_dir / generate_file_name(url, ".mp4")
        if video_path.exists():
            return video_path

        opts = {
            "outtmpl": str(video_path),
            "merge_output_format": "mp4",
            # "format": f"bv[filesize<={info.duration // 10 + 10}M]+ba/b[filesize<={info.duration // 8 + 10}M]",
            # "format": "bv*[height<=720]+ba/b[height<=720]",
            "format": format or "best",
            "postprocessors": [
                {"key": "FFmpegVideoConvertor", "preferedformat": "mp4"}
            ],
            "http_headers": headers or self.default_headers,
        }
        if proxy:
            opts["proxy"] = proxy
        if cookiefile and cookiefile.is_file():
            opts["cookiefile"] = str(cookiefile)
        if node:
            opts["js_runtimes"] = {"node": {}}

        with yt_dlp.YoutubeDL(opts) as ydl:  # type: ignore
            await self._ytdlp_download(ydl, url)
        return video_path

    @staticmethod
    async def _ytdlp_download(ydl: yt_dlp.YoutubeDL, url: str) -> None:
        try:
            await to_thread(ydl.download, [url])
        except yt_dlp.utils.DownloadError as exc:
            reason = describe_error(exc)
            logger.error(f"yt-dlp 下载失败: {reason} | url: {url}")
            raise DownloadException(f"媒体下载失败: {reason}") from exc

    @auto_task
    async def ytdlp_download_video_relaxed(
        self,
        url: str,
        *,
        cookiefile: Path | None = None,
        headers: dict[str, str] | None = None,
        proxy: str | None = None,
        format: str | None = None,
        node: bool = False,
    ) -> Path:
        file_stem = generate_file_name(url)
        video_path = self.cfg.cache_dir / f"{file_stem}.mp4"
        if video_path.exists():
            return video_path

        opts = {
            "outtmpl": str(self.cfg.cache_dir / file_stem) + ".%(ext)s",
            "merge_output_format": "mp4",
            "format": format or None,
            "postprocessors": [
                {"key": "FFmpegVideoConvertor", "preferedformat": "mp4"}
            ],
            "http_headers": headers or self.default_headers,
            "quiet": True,
            "no_warnings": True,
        }
        if not opts["format"]:
            opts.pop("format")
        if proxy:
            opts["proxy"] = proxy
        if cookiefile and cookiefile.is_file():
            opts["cookiefile"] = str(cookiefile)
        if node:
            opts["js_runtimes"] = {"node": {}}

        with yt_dlp.YoutubeDL(opts) as ydl:  # type: ignore
            await self._ytdlp_download(ydl, url)
        if video_path.exists():
            return video_path

        candidates = sorted(self.cfg.cache_dir.glob(f"{file_stem}*.mp4"))
        if candidates:
            return candidates[0]
        logger.error(f"yt-dlp 下载后未找到输出文件 | url: {url}")
        raise DownloadException("媒体下载失败: yt-dlp 未产出文件")

    @auto_task
    async def ytdlp_download_audio(
        self,
        url: str,
        *,
        cookiefile: Path | None,
        headers: dict[str, str] | None = None,
        proxy: str | None = None,
        format: str | None = None,
    ) -> Path:
        file_name = generate_file_name(url)
        audio_path = self.cfg.cache_dir / f"{file_name}.flac"
        if audio_path.exists():
            return audio_path

        opts = {
            "outtmpl": str(self.cfg.cache_dir / file_name) + ".%(ext)s",
            "format": format or "bestaudio/best",
            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "flac",
                    "preferredquality": "0",
                }
            ],
            "cookiefile": None,
            "http_headers": headers or self.default_headers,
        }
        if proxy:
            opts["proxy"] = proxy
        if cookiefile and cookiefile.is_file():
            opts["cookiefile"] = str(cookiefile)

        with yt_dlp.YoutubeDL(opts) as ydl:  # type: ignore
            await self._ytdlp_download(ydl, url)
        return audio_path
