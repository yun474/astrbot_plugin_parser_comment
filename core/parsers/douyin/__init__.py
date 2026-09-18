import asyncio
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar
from urllib.parse import urlparse

import msgspec
from aiohttp import ClientError

from astrbot.api import logger

from ...config import PluginConfig
from ...cookie import CookieJar
from ..base import (
    BaseParser,
    Downloader,
    ParseException,
    Platform,
    handle,
)

if TYPE_CHECKING:
    from ...data import ParseResult
    from .video import VideoData


@dataclass(slots=True)
class ProbedVideo:
    url: str
    size: int
    ratio: str


class DouyinParser(BaseParser):
    # 平台信息
    platform: ClassVar[Platform] = Platform(name="douyin", display_name="抖音")
    # play 端点支持的清晰度, 由高到低; 请求不存在的档位时端点会回落到最高档
    PLAY_RATIOS: ClassVar[tuple[str, ...]] = ("1080p", "720p", "540p")
    TTWID_REGISTER_URL: ClassVar[str] = (
        "https://ttwid.bytedance.com/ttwid/union/register/"
    )
    ROUTER_DATA_PATTERN: ClassVar[re.Pattern[str]] = re.compile(
        r"window\._ROUTER_DATA\s*=\s*(.*?)</script>", re.DOTALL
    )

    def __init__(self, config: PluginConfig, downloader: Downloader):
        super().__init__(config, downloader)
        self.mycfg = config.parser.douyin
        self.cookiejar = CookieJar(config, self.mycfg, domain="douyin.com")
        self._set_cookies()

    def _set_cookies(self, cookies_str: str = ""):
        """设置cookie到请求头"""
        cookies_str = cookies_str or self.cookiejar.cookies_str
        if cookies_str:
            self.ios_headers["Cookie"] = cookies_str
            self.android_headers["Cookie"] = cookies_str

    def _sync_headers_for_url(self, url: str) -> dict[str, str]:
        headers = self.ios_headers.copy()
        headers.pop("Cookie", None)
        if cookies_str := self.cookiejar.get_cookie_header_for_url(url):
            headers["Cookie"] = cookies_str
        elif self._is_iesdouyin_url(url):
            if cookies_str := self.cookiejar.get_cookie_header(domain="iesdouyin.com"):
                headers["Cookie"] = cookies_str
        return headers

    @staticmethod
    def _is_iesdouyin_url(url: str) -> bool:
        hostname = urlparse(url).hostname or ""
        return hostname == "iesdouyin.com" or hostname.endswith(".iesdouyin.com")

    def _has_ttwid(self) -> bool:
        cookies = self.cookiejar.get(domain="iesdouyin.com") or {}
        return bool(cookies.get("ttwid"))

    # https://v.douyin.com/_2ljF4AmKL8
    @handle("v.douyin", r"v\.douyin\.com/[a-zA-Z0-9_\-]+")
    @handle("jx.douyin", r"jx\.douyin\.com/[a-zA-Z0-9_\-]+")
    async def _parse_short_link(self, searched: re.Match[str]):
        url = f"https://{searched.group(0)}"
        return await self.parse_with_redirect(url)

    # https://www.douyin.com/video/7521023890996514083
    # https://www.douyin.com/note/7469411074119322899
    @handle("", r"(?<![A-Za-z0-9_/=:%?&.-])(?P<vid>\d{18,20})(?!\d)")
    @handle("aweme_id", r"aweme_id[=:/\s]+(?P<vid>\d{10,})")
    @handle("aweme", r"aweme/(?P<vid>\d{10,})")
    @handle("douyin", r"douyin\.com/(?P<ty>video|note)/(?P<vid>\d+)")
    @handle("iesdouyin", r"iesdouyin\.com/share/(?P<ty>slides|video|note)/(?P<vid>\d+)")
    @handle("m.douyin", r"m\.douyin\.com/share/(?P<ty>slides|video|note)/(?P<vid>\d+)")
    # https://jingxuan.douyin.com/m/video/7574300896016862490?app=yumme&utm_source=copy_link
    @handle(
        "jingxuan.douyin",
        r"jingxuan\.douyin.com/m/(?P<ty>slides|video|note)/(?P<vid>\d+)",
    )
    async def _parse_douyin(self, searched: re.Match[str]):
        ty = searched.groupdict().get("ty") or "video"
        vid = searched.group("vid")
        logger.debug(f"[抖音] 解析类型: {ty}, ID: {vid}")
        if ty == "slides":
            return await self.parse_slides(vid)

        try:
            return await self.parse_video(ty, vid)
        except ParseException as e:
            logger.warning(f"[抖音] 分享页解析失败 {ty}/{vid}, 错误: {e}")
            raise ParseException(f"抖音解析失败: {e.message}") from e

    @staticmethod
    def _build_iesdouyin_url(ty: str, vid: str) -> str:
        return f"https://www.iesdouyin.com/share/{ty}/{vid}/"

    @staticmethod
    def _build_m_douyin_url(ty: str, vid: str) -> str:
        return f"https://m.douyin.com/share/{ty}/{vid}/"

    async def ensure_ttwid(self) -> None:
        if self._has_ttwid():
            return

        logger.debug("[抖音] 当前缺少匿名 ttwid，尝试注册")
        headers = self.ios_headers.copy()
        headers.update(
            {
                "Content-Type": "application/json",
                "Referer": "https://www.iesdouyin.com/",
            }
        )
        payload = {
            "region": "cn",
            "aid": 1768,
            "needFid": False,
            "service": "www.iesdouyin.com",
            "union": True,
            "fid": "",
        }
        try:
            async with self.session.post(
                self.TTWID_REGISTER_URL,
                json=payload,
                headers=headers,
            ) as resp:
                if resp.status >= 400:
                    raise ParseException(f"ttwid register status: {resp.status}")
                set_cookie_headers = resp.headers.getall("Set-Cookie", [])
                self.cookiejar.update_from_response(set_cookie_headers)
                self._set_cookies()
                body = await resp.json(content_type=None)
        except (ClientError, asyncio.TimeoutError, ValueError) as e:
            raise ParseException("ttwid register failed") from e

        if not isinstance(body, dict):
            raise ParseException("ttwid register returned invalid body")

        if callback_url := body.get("redirect_url"):
            callback_headers = self._sync_headers_for_url(callback_url)
            callback_headers["Referer"] = "https://www.iesdouyin.com/"
            try:
                async with self.session.get(
                    callback_url,
                    headers=callback_headers,
                    allow_redirects=False,
                ) as resp:
                    if resp.status >= 400:
                        raise ParseException(f"ttwid callback status: {resp.status}")
                    set_cookie_headers = resp.headers.getall("Set-Cookie", [])
                    self.cookiejar.update_from_response(set_cookie_headers)
                    self._set_cookies()
            except (ClientError, asyncio.TimeoutError) as e:
                raise ParseException("ttwid callback failed") from e

        if not self._has_ttwid():
            raise ParseException("ttwid register returned no cookie")

    async def parse_with_redirect(self, url: str) -> "ParseResult":
        """先重定向再解析，并更新 cookies"""
        logger.debug(f"[抖音] 短链重定向请求: {url}")
        async with self.session.get(
            url, headers=self.ios_headers, allow_redirects=False
        ) as resp:
            logger.debug(f"[抖音] 短链重定向响应状态码: {resp.status}")
            # 从响应中提取 Set-Cookie 并更新
            set_cookie_headers = resp.headers.getall("Set-Cookie", [])
            self.cookiejar.update_from_response(set_cookie_headers)
            self._set_cookies()

            # 只有在状态码是重定向状态码时才获取 Location
            redirect_url = url
            if resp.status in (301, 302, 303, 307, 308):
                redirect_url = resp.headers.get("Location", url)
                logger.debug(f"[抖音] 重定向到: {redirect_url}")

        if redirect_url == url:
            raise ParseException(f"无法重定向 URL: {url}")

        keyword, searched = self.search_url(redirect_url)
        return await self.parse(keyword, searched)

    async def _fetch_share_page(self, url: str) -> str:
        logger.debug(f"[抖音] 请求分享页: {url}")
        async with self.session.get(
            url, headers=self._sync_headers_for_url(url), allow_redirects=False
        ) as resp:
            if resp.status != 200:
                raise ParseException(f"分享页 status: {resp.status}")
            text = await resp.text()
            set_cookie_headers = resp.headers.getall("Set-Cookie", [])
            self.cookiejar.update_from_response(set_cookie_headers)
            self._set_cookies()
        return text

    @staticmethod
    def _is_waf_challenge(text: str) -> bool:
        """字节 WAF 的人机挑战页: 没有正文, 只有一段算 hash 的脚本"""
        return "_wafchallengeid" in text or "waf-jschallenge" in text

    async def _load_share_data(self, ty: str, vid: str) -> "tuple[str, VideoData]":
        """轮流请求各分享域名, 返回 (分享页 url, 视频数据)

        同一 ttwid 短时间请求过多时 iesdouyin 会返回 WAF 挑战页; 两个域名偶尔还会
        SSR 降级成不带数据的空壳页, 所以最多跑两轮, 挑战页所在域名不再重试。
        """
        from .video import RouterData

        candidates = [
            self._build_iesdouyin_url(ty, vid),
            self._build_m_douyin_url(ty, vid),
        ]
        last_error = ParseException("抖音分享页解析失败")
        for round_no in range(2):
            for url in list(candidates):
                text = await self._fetch_share_page(url)
                if self._is_waf_challenge(text):
                    logger.warning(f"[抖音] 分享页触发人机验证: {url}")
                    last_error = ParseException(
                        "抖音触发了人机验证，请稍后再试或降低解析频率"
                    )
                    candidates.remove(url)
                    continue
                matched = self.ROUTER_DATA_PATTERN.search(text)
                if not matched or not matched.group(1):
                    logger.warning(
                        f"[抖音] 分享页未包含 _ROUTER_DATA (长度 {len(text)}): {url}"
                    )
                    last_error = ParseException("can't find _ROUTER_DATA in html")
                    continue
                res = msgspec.json.decode(
                    matched.group(1).strip(), type=RouterData
                ).video_info_res
                if reason := res.unavailable_reason:
                    raise ParseException(f"视频不可用: {reason}")
                if not res.item_list:
                    logger.warning(f"[抖音] 分享页未返回视频数据: {url}")
                    last_error = ParseException("can't find data in videoInfoRes")
                    continue
                return url, res.item_list[0]
            if not candidates or round_no:
                break
            await asyncio.sleep(1)
        raise last_error

    async def parse_video(self, ty: str, vid: str):
        await self.ensure_ttwid()
        url, video_data = await self._load_share_data(ty, vid)
        logger.debug(
            f"[抖音] 解析成功 - 作者: {video_data.author.nickname}, 描述: {video_data.desc[:50]}..."
        )
        contents = []

        # 添加图片内容
        if image_url_lists := video_data.image_url_lists:
            logger.debug(f"[抖音] 检测到图文内容，图片数量: {len(image_url_lists)}")
            contents.extend(
                self.create_image_contents(image_url_lists, headers=self.ios_headers)
            )

        # 添加视频内容
        elif video_data.video:
            logger.debug(
                f"[抖音] 检测到视频内容，时长: {video_data.video.duration / 1000:.0f}秒"
            )
            if video_content := await self._create_video_content(video_data, url):
                contents.append(video_content)

        # 构建作者
        author = self.create_author(
            video_data.author.nickname, video_data.avatar_url, headers=self.ios_headers
        )

        return self.result(
            title=video_data.desc,
            author=author,
            contents=contents,
            timestamp=video_data.create_time,
        )

    async def _create_video_content(self, video_data: "VideoData", referer: str):
        """优先走 play 端点按配置清晰度挑流, 失败时回退到 play_addr 里的直链"""
        headers = self._build_media_headers(referer)
        video_urls = video_data.video_urls
        video_name = None
        if play_token := video_data.play_token:
            try:
                probed = await self.probe_video_url(play_token, referer)
                logger.debug(
                    f"[抖音] 选用 {probed.ratio}, 大小 {probed.size / 1024 / 1024:.2f} MB"
                )
                # 直链失败时轮换 CDN 线路重试; 文件名固定, 多群解析同一视频可复用缓存
                video_urls = [
                    probed.url,
                    self._build_play_url(play_token, probed.ratio, line="1"),
                    self._build_play_url(play_token, probed.ratio, line="0"),
                ]
                video_name = f"douyin_{play_token}_{probed.ratio}.mp4"
            except ParseException as e:
                logger.warning(f"[抖音] play 端点探测失败，回退 play_addr: {e}")
        if not video_urls:
            return None
        task = self.downloader.download_video(
            video_urls[0],
            video_name=video_name,
            headers=headers,
            proxy=self.proxy,
            backup_urls=video_urls[1:],
        )
        duration = video_data.video.duration / 1000 if video_data.video else 0
        return self.create_video_content(
            task, video_data.cover_url, duration, headers=headers
        )

    @staticmethod
    def _build_play_url(video_id: str, ratio: str, line: str = "0") -> str:
        return (
            "https://aweme.snssdk.com/aweme/v1/play/"
            f"?video_id={video_id}&ratio={ratio}&line={line}"
        )

    def _build_media_headers(self, referer: str) -> dict[str, str]:
        headers = self.ios_headers.copy()
        headers.pop("Cookie", None)
        headers["Referer"] = referer
        return headers

    @property
    def quality_ladder(self) -> tuple[str, ...]:
        """从配置的清晰度开始, 逐档降低"""
        if self.cfg.force_lowest_quality:
            return self.PLAY_RATIOS[-1:]
        quality = str(self.mycfg.video_quality or "").lower()
        if quality in self.PLAY_RATIOS:
            return self.PLAY_RATIOS[self.PLAY_RATIOS.index(quality) :]
        return self.PLAY_RATIOS

    async def probe_video_url(self, video_id: str, referer: str) -> ProbedVideo:
        """按清晰度阶梯探测 play 端点, 返回首个不超过大小限制的档位"""
        probed: list[ProbedVideo] = []
        for ratio in self.quality_ladder:
            item = await self._probe_ratio(video_id, ratio, referer)
            if item is None:
                continue
            if item.size <= self.cfg.max_size:
                return item
            logger.info(
                f"[抖音] {ratio} 大小 {item.size / 1024 / 1024:.2f} MB 超过限制，尝试更低清晰度"
            )
            probed.append(item)

        if not probed:
            raise ParseException("can't probe play endpoint")
        # 所有档位都超限: 交给下载器按大小限制处理并提示用户
        return min(probed, key=lambda item: item.size)

    async def _probe_ratio(
        self, video_id: str, ratio: str, referer: str
    ) -> ProbedVideo | None:
        """用 Range 请求探测某一档位, 拿到最终 CDN 直链和文件大小; 网络抖动时重试一次"""
        play_url = self._build_play_url(video_id, ratio)
        headers = self._build_media_headers(referer)
        headers["Range"] = "bytes=0-1"
        for attempt in range(2):
            try:
                async with self.session.get(
                    play_url, headers=headers, allow_redirects=True
                ) as resp:
                    if resp.status >= 400:
                        logger.debug(f"[抖音] {ratio} 探测失败，状态码: {resp.status}")
                        return None
                    if not resp.content_type.startswith("video/"):
                        logger.debug(
                            f"[抖音] {ratio} 返回的不是视频: {resp.content_type}"
                        )
                        return None
                    size = self._extract_response_size(resp.headers)
                    if size <= 0:
                        logger.debug(f"[抖音] {ratio} 未拿到有效文件大小")
                        return None
                    return ProbedVideo(str(resp.url), size, ratio)
            except (ClientError, asyncio.TimeoutError) as e:
                logger.debug(f"[抖音] {ratio} 第 {attempt + 1} 次探测请求失败: {e}")
        return None

    @staticmethod
    def _extract_response_size(headers) -> int:
        if content_range := headers.get("Content-Range"):
            if matched := re.search(r"/(\d+)\s*$", content_range):
                return int(matched.group(1))
        if content_length := headers.get("Content-Length"):
            try:
                return int(content_length)
            except ValueError:
                return 0
        return 0

    async def parse_slides(self, video_id: str):
        url = "https://www.iesdouyin.com/web/api/v2/aweme/slidesinfo/"
        params = {
            "aweme_ids": f"[{video_id}]",
            "request_source": "200",
        }
        logger.debug(f"[抖音] 请求参数: {params}")
        async with self.session.get(
            url, params=params, headers=self.android_headers
        ) as resp:
            logger.debug(f"[抖音] 幻灯片API响应状态码: {resp.status}")
            resp.raise_for_status()
            # 从响应中提取 Set-Cookie 并更新
            set_cookie_headers = resp.headers.getall("Set-Cookie", [])
            self.cookiejar.update_from_response(set_cookie_headers)
            self._set_cookies()

            from .slides import SlidesInfo

            response_text = await resp.read()
            logger.debug(f"[抖音] 幻灯片API响应体大小: {len(response_text)} 字节")
            slides_data = msgspec.json.decode(
                response_text, type=SlidesInfo
            ).aweme_details[0]
        logger.debug(
            f"[抖音] 幻灯片解析成功 - 作者: {slides_data.name}, 描述: {slides_data.desc[:50]}..."
        )
        contents = []

        # 添加图片内容
        if image_url_lists := slides_data.image_url_lists:
            logger.debug(f"[抖音] 检测到幻灯片图片，数量: {len(image_url_lists)}")
            contents.extend(
                self.create_image_contents(
                    image_url_lists, headers=self.android_headers
                )
            )

        # 添加动态内容
        if dynamic_url_lists := slides_data.dynamic_url_lists:
            logger.debug(f"[抖音] 检测到幻灯片动态效果，数量: {len(dynamic_url_lists)}")
            contents.extend(
                self.create_dynamic_contents(
                    dynamic_url_lists, headers=self.android_headers
                )
            )

        # 构建作者
        author = self.create_author(
            slides_data.name, slides_data.avatar_url, headers=self.android_headers
        )

        return self.result(
            title=slides_data.desc,
            author=author,
            contents=contents,
            timestamp=slides_data.create_time,
        )
