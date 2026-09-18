import asyncio
from re import Match
from typing import Any, ClassVar

from bilibili_api import request_settings, select_client
from bilibili_api.opus import Opus
from bilibili_api.video import Video, VideoCodecs, VideoQuality
from msgspec import convert

from astrbot.api import logger

from ...config import PluginConfig
from ...data import ImageContent, MediaContent, Platform, SendGroup
from ...exception import DownloadException, DurationLimitException
from ..base import (
    BaseParser,
    Downloader,
    ParseException,
    handle,
)
from .comment_renderer import BiliCommentRenderer
from .comment_service import BiliCommentService
from .login import BilibiliLogin
from .poster import BiliPosterRenderer

# 选择客户端
select_client("curl_cffi")
# 模拟浏览器，第二参数数值参考 curl_cffi 文档
# https://curl-cffi.readthedocs.io/en/latest/impersonate.html
request_settings.set("impersonate", "chrome131")


class BilibiliParser(BaseParser):
    # 平台信息
    platform: ClassVar[Platform] = Platform(name="bilibili", display_name="B站")

    def __init__(self, config: PluginConfig, downloader: Downloader):
        super().__init__(config, downloader)
        self.mycfg = config.parser.bilibili
        self.headers.update(
            {
                "Referer": "https://www.bilibili.com/",
                "Origin": "https://www.bilibili.com",
            }
        )

        self.video_quality = (
            VideoQuality._360P
            if self.cfg.force_lowest_quality
            else getattr(
                VideoQuality, str(self.mycfg.video_quality).upper(), VideoQuality._720P
            )
        )
        self.video_codecs = [
            getattr(VideoCodecs, str(c).upper(), VideoCodecs.AVC)
            for c in (self.mycfg.video_codec_list or ["AVC"])
        ]
        self.login = BilibiliLogin(config)
        # 海报和评论区都是 HTML 模板渲染, 共用解析器基类里的浏览器
        self.poster = (
            BiliPosterRenderer(self.html_renderer, config.cache_dir, config.timezone)
            if self.mycfg.poster_render_enable is not False
            else None
        )
        self.comment_merge_with_video = bool(self.mycfg.comment_merge_with_video)
        comment_limit = self.mycfg.comment_limit
        qr_check_max = self.mycfg.comment_qr_check_max
        self.comment_service = BiliCommentService(
            parser=self,
            renderer=BiliCommentRenderer(self.html_renderer, config.timezone),
            enabled=self.mycfg.comment_render_enable is not False,
            comment_limit=9 if comment_limit is None else int(comment_limit),
            enable_text_ad_filter=self.mycfg.comment_filter_text is not False,
            enable_qr_filter=bool(self.mycfg.comment_filter_qr),
            qr_check_max=4 if qr_check_max is None else int(qr_check_max),
            show_replies=self.mycfg.comment_show_replies is not False,
        )

    @handle("b23.tv", r"b23\.tv/[A-Za-z\d\._?%&+\-=/#]+")
    @handle("bili2233", r"bili2233\.cn/[A-Za-z\d\._?%&+\-=/#]+")
    async def _parse_short_link(self, searched: Match[str]):
        """解析短链"""
        url = f"https://{searched.group(0)}"
        return await self.parse_with_redirect(url)

    @handle("BV", r"^(?P<bvid>BV[0-9a-zA-Z]{10})(?:\s)?(?P<page_num>\d{1,3})?$")
    @handle(
        "/BV",
        r"bilibili\.com(?:/video)?/(?P<bvid>BV[0-9a-zA-Z]{10})(?:\?p=(?P<page_num>\d{1,3}))?",
    )
    async def _parse_bv(self, searched: Match[str]):
        """解析视频信息"""
        bvid = str(searched.group("bvid"))
        page_num = int(searched.group("page_num") or 1)

        return await self.parse_video(bvid=bvid, page_num=page_num)

    @handle("bm", r"^bm(?P<bvid>BV[0-9a-zA-Z]{10})(?:\s(?P<page_num>\d{1,3}))?$")
    async def _parse_bv_bm(self, searched: Match[str]):
        bvid = searched.group("bvid")
        page = int(searched.group("page_num") or 1)
        _, a_url = await self.extract_download_urls(bvid=bvid, page_index=page - 1)
        if not a_url:
            raise ParseException("未找到音频链接")
        audio = self.create_audio_content(a_url)
        return self.result(
            title=f"BiliBili_audio_{bvid}",
            contents=[audio],
            url=a_url,
        )

    @handle("av", r"^av(?P<avid>\d{6,})(?:\s)?(?P<page_num>\d{1,3})?$")
    @handle(
        "/av",
        r"bilibili\.com(?:/video)?/av(?P<avid>\d{6,})(?:\?p=(?P<page_num>\d{1,3}))?",
    )
    async def _parse_av(self, searched: Match[str]):
        """解析视频信息"""
        avid = int(searched.group("avid"))
        page_num = int(searched.group("page_num") or 1)

        return await self.parse_video(avid=avid, page_num=page_num)

    @handle("/dynamic/", r"bilibili\.com/dynamic/(?P<dynamic_id>\d+)")
    @handle("t.bili", r"t\.bilibili\.com/(?P<dynamic_id>\d+)")
    async def _parse_dynamic(self, searched: Match[str]):
        """解析动态信息"""
        dynamic_id = int(searched.group("dynamic_id"))
        return await self.parse_dynamic(dynamic_id)

    @handle("live.bili", r"live\.bilibili\.com/(?P<room_id>\d+)")
    async def _parse_live(self, searched: Match[str]):
        """解析直播信息"""
        room_id = int(searched.group("room_id"))
        return await self.parse_live(room_id)

    @handle("/favlist", r"favlist\?fid=(?P<fav_id>\d+)")
    async def _parse_favlist(self, searched: Match[str]):
        """解析收藏夹信息"""
        fav_id = int(searched.group("fav_id"))
        return await self.parse_favlist(fav_id)

    @handle("/read/", r"bilibili\.com/read/cv(?P<read_id>\d+)")
    async def _parse_read(self, searched: Match[str]):
        """解析专栏信息"""
        read_id = int(searched.group("read_id"))
        return await self.parse_read_with_opus(read_id)

    @handle("/opus/", r"bilibili\.com/opus/(?P<opus_id>\d+)")
    async def _parse_opus(self, searched: Match[str]):
        """解析图文动态信息"""
        opus_id = int(searched.group("opus_id"))
        return await self.parse_opus(opus_id)

    async def parse_video(
        self,
        *,
        bvid: str | None = None,
        avid: int | None = None,
        page_num: int = 1,
    ):
        """解析视频信息

        Args:
            bvid (str | None): bvid
            avid (int | None): avid
            page_num (int): 页码
        """

        from .video import AIConclusion, VideoInfo

        video = await self._get_video(bvid=bvid, avid=avid)
        # 转换为 msgspec struct
        video_info = convert(await video.get_info(), VideoInfo)
        # 获取简介
        text = f"简介: {video_info.desc}" if video_info.desc else None
        # up
        author = self.create_author(video_info.owner.name, video_info.owner.face)
        # 处理分 p
        page_info = video_info.extract_info_with_page(page_num)

        # 获取 AI 总结（默认提示）
        ai_summary = ""
        if self.login._credential:
            try:
                cid = await video.get_cid(page_info.index)
                ai_conclusion = await video.get_ai_conclusion(cid)
                ai_conclusion = convert(ai_conclusion, AIConclusion)
                ai_summary = ai_conclusion.summary
            except Exception:
                ai_summary = "哔哩哔哩 cookie 未配置或失效, 无法使用 AI 总结"

        url = f"https://bilibili.com/{video_info.bvid}"
        url += f"?p={page_info.index + 1}" if page_info.index > 0 else ""

        # 视频下载 task
        async def download_video():
            try:
                output_path = self.cfg.cache_dir / f"{video_info.bvid}-{page_num}.mp4"
                if output_path.exists():
                    return output_path
                v_url, a_url = await self.extract_download_urls(
                    video=video, page_index=page_info.index
                )
                if page_info.duration > self.cfg.max_duration:
                    raise DurationLimitException
                if a_url is not None:
                    return await self.downloader.download_av_and_merge(
                        v_url,
                        a_url,
                        output_path=output_path,
                        headers=self.headers,
                        proxy=self.proxy,
                    )
                else:
                    return await self.downloader.streamd(
                        v_url,
                        file_name=output_path.name,
                        headers=self.headers,
                        proxy=self.proxy,
                    )
            except DownloadException:
                raise
            except Exception as e:
                logger.warning(f"[Bilibili] 视频下载失败: {e}")
                raise DownloadException(f"B站媒体下载失败: {e}") from e

        video_task = asyncio.create_task(download_video())
        video_content = self.create_video_content(
            video_task,
            page_info.cover,
            page_info.duration,
        )
        poster_task = None
        if self.poster is not None:
            poster_task = asyncio.create_task(
                self.poster.render(
                    video_info,
                    page_info,
                    cover=video_content.cover,
                    avatar=author.avatar,
                    url=url,
                    ai_summary=ai_summary,
                ),
                name=f"bili_poster_{video_info.bvid}",
            )
            # 发送链路异常时可能没人 await 它, 标记异常已取回, 别让 asyncio 刷屏
            poster_task.add_done_callback(lambda t: t.cancelled() or t.exception())
        comment_contents = self.comment_service.build_comment_image_content(
            video_info.aid,
            video_title=page_info.title,
            video_cover=page_info.cover,
            up_name=video_info.owner.name,
        )

        # 有海报时: 海报先单独发出来, 视频紧随其后, 不塞进合并转发里藏起来;
        # 没海报时沿用原版的默认发送策略。评论图默认作为第二组合并转发。
        main_group = SendGroup(
            contents=[video_content],
            force_merge=False if poster_task else None,
            render_card=True if poster_task else None,
        )
        if comment_contents and self.comment_merge_with_video:
            send_groups = [
                SendGroup(
                    contents=[video_content, *comment_contents],
                    force_merge=True,
                    render_card=main_group.render_card,
                    preserve_order=True,
                ),
            ]
        else:
            send_groups = [main_group]
            if comment_contents:
                send_groups.append(
                    SendGroup(
                        contents=comment_contents, force_merge=True, render_card=False
                    )
                )

        return self.result(
            url=url,
            title=page_info.title,
            timestamp=page_info.timestamp,
            text=text,
            author=author,
            contents=[video_content],
            send_groups=send_groups,
            card=poster_task,
            extra={"info": ai_summary},
        )

    async def parse_dynamic(self, dynamic_id: int):
        """解析动态信息

        Args:
            url (str): 动态链接
        """
        from bilibili_api.dynamic import Dynamic

        from .dynamic import DynamicData

        dynamic_ = Dynamic(dynamic_id, await self.login.credential)

        dynamic_info = convert(await dynamic_.get_info(), DynamicData).item
        author = self.create_author(dynamic_info.name, dynamic_info.avatar)

        # 下载图片
        contents: list[MediaContent] = []
        for image_url in dynamic_info.image_urls:
            img_task = self.downloader.download_img(
                image_url, headers=self.headers, proxy=self.proxy
            )
            contents.append(ImageContent(img_task))

        return self.result(
            title=dynamic_info.title,
            text=dynamic_info.text,
            timestamp=dynamic_info.timestamp,
            author=author,
            contents=contents,
        )

    async def parse_opus(self, opus_id: int):
        """解析图文动态信息

        Args:
            opus_id (int): 图文动态 id
        """
        opus = Opus(opus_id, await self.login.credential)
        return await self._parse_opus_obj(opus)

    async def parse_read_with_opus(self, read_id: int):
        """解析专栏信息, 使用 Opus 接口
        Args:
            read_id (int): 专栏 id
        """
        from bilibili_api.article import Article

        article = Article(read_id)
        return await self._parse_opus_obj(await article.turn_to_opus())

    async def _parse_opus_obj(self, bili_opus: Opus):
        """解析图文动态信息
        Args:
            opus_id (int): 图文动态 id
        Returns:
            ParseResult: 解析结果
        """
        from .opus import ImageNode, OpusItem, TextNode

        opus_info = await bili_opus.get_info()
        if not isinstance(opus_info, dict):
            raise ParseException("获取图文动态信息失败")
        # 转换为结构体
        opus_data = convert(opus_info, OpusItem)
        logger.debug(f"opus_data: {opus_data}")
        author = self.create_author(*opus_data.name_avatar)
        # 按顺序处理图文内容（参考 parse_read 的逻辑）
        contents: list[MediaContent] = []
        current_text = ""
        for node in opus_data.gen_text_img():
            if isinstance(node, ImageNode):
                contents.append(
                    self.create_graphics_content(
                        node.url, current_text.strip(), node.alt
                    )
                )
                current_text = ""
            elif isinstance(node, TextNode):
                current_text += node.text
        return self.result(
            title=opus_data.title,
            author=author,
            timestamp=opus_data.timestamp,
            contents=contents,
            text=current_text.strip(),
        )

    async def parse_live(self, room_id: int):
        """解析直播信息

        Args:
            room_id (int): 直播 id

        Returns:
            ParseResult: 解析结果
        """
        from bilibili_api.live import LiveRoom

        from .live import RoomData

        room = LiveRoom(room_display_id=room_id, credential=await self.login.credential)
        info_dict = await room.get_room_info()

        room_data = convert(info_dict, RoomData)
        contents: list[MediaContent] = []
        # 下载封面
        if cover := room_data.cover:
            cover_task = self.downloader.download_img(
                cover, headers=self.headers, proxy=self.proxy
            )
            contents.append(ImageContent(cover_task))

        # 下载关键帧
        if keyframe := room_data.keyframe:
            keyframe_task = self.downloader.download_img(
                keyframe, headers=self.headers, proxy=self.proxy
            )
            contents.append(ImageContent(keyframe_task))

        author = self.create_author(room_data.name, room_data.avatar)

        url = f"https://www.bilibili.com/blackboard/live/live-activity-player.html?enterTheRoom=0&cid={room_id}"
        return self.result(
            url=url,
            title=room_data.title,
            text=room_data.detail,
            contents=contents,
            author=author,
        )

    async def parse_favlist(self, fav_id: int):
        """解析收藏夹信息

        Args:
            fav_id (int): 收藏夹 id

        Returns:
            list[GraphicsContent]: 图文内容列表
        """
        from bilibili_api.favorite_list import get_video_favorite_list_content

        from .favlist import FavData

        # 只会取一页，20 个
        fav_dict = await get_video_favorite_list_content(fav_id)

        if fav_dict["medias"] is None:
            raise ParseException("收藏夹内容为空, 或被风控")

        favdata = convert(fav_dict, FavData)

        return self.result(
            title=favdata.title,
            timestamp=favdata.timestamp,
            author=self.create_author(favdata.info.upper.name, favdata.info.upper.face),
            contents=[
                self.create_graphics_content(fav.cover, fav.desc)
                for fav in favdata.medias
            ],
        )

    async def _get_video(
        self, *, bvid: str | None = None, avid: int | None = None
    ) -> Video:
        """解析视频信息

        Args:
            bvid (str | None): bvid
            avid (int | None): avid
        """
        if avid:
            return Video(aid=avid, credential=await self.login.credential)
        elif bvid:
            return Video(bvid=bvid, credential=await self.login.credential)
        else:
            raise ParseException("avid 和 bvid 至少指定一项")

    async def extract_download_urls(
        self,
        video: Video | None = None,
        *,
        bvid: str | None = None,
        avid: int | None = None,
        page_index: int = 0,
    ) -> tuple[str, str | None]:
        """解析视频下载链接

        Args:
            bvid (str | None): bvid
            avid (int | None): avid
            page_index (int): 页索引 = 页码 - 1
        """

        from bilibili_api.video import (
            AudioStreamDownloadURL,
            VideoDownloadURLDataDetecter,
            VideoStreamDownloadURL,
        )

        if video is None:
            video = await self._get_video(bvid=bvid, avid=avid)

        # 获取下载数据
        download_url_data = await video.get_download_url(page_index=page_index)
        # bilibili-api 17.x 无法识别部分新返回的 hvc1 编码，先归一化为 HEV。
        for video_data in (download_url_data.get("dash") or {}).get("video") or []:
            if not isinstance(video_data, dict):
                continue
            codecs = video_data.get("codecs", "")
            if isinstance(codecs, str) and codecs.startswith("hvc1"):
                video_data["codecs"] = f"hev,{codecs}"

        try:
            detecter = VideoDownloadURLDataDetecter(download_url_data)
            streams = detecter.detect_best_streams(
                video_max_quality=self.video_quality,
                codecs=self.video_codecs,
                no_dolby_video=True,
                no_hdr=True,
            )
            if not streams:
                raise DownloadException("官方选择器未找到匹配的视频流")
            video_stream = streams[0]
            if not isinstance(video_stream, VideoStreamDownloadURL):
                raise DownloadException("未找到可下载的视频流")
            logger.debug(
                f"视频流质量: {video_stream.video_quality.name}, 编码: {video_stream.video_codecs}"
            )

            audio_stream = streams[1] if len(streams) > 1 else None
            if not isinstance(audio_stream, AudioStreamDownloadURL):
                return video_stream.url, None
            logger.debug(f"音频流质量: {audio_stream.audio_quality.name}")
            return video_stream.url, audio_stream.url
        except Exception as e:
            logger.warning(f"[Bilibili] 官方流选择失败，尝试手动兜底: {e}")
            return self._extract_download_urls_fallback(download_url_data)

    @staticmethod
    def _stream_url(item: dict[str, Any]) -> str | None:
        return item.get("baseUrl") or item.get("base_url") or item.get("url")

    @staticmethod
    def _safe_int(value: Any, default: int = 0) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _codec_rank(codec: str, preferred: list[str]) -> int:
        codec_l = (codec or "").lower()
        if not codec_l:
            return len(preferred) + 8

        for idx, name in enumerate(preferred):
            name_l = name.lower()
            if name_l == "avc" and ("avc" in codec_l or "avc1" in codec_l):
                return idx
            if name_l in {"hev", "hevc"} and ("hev" in codec_l or "hvc" in codec_l):
                return idx
            if name_l == "av1" and ("av01" in codec_l or "av1" in codec_l):
                return idx
        return len(preferred) + 1

    def _preferred_codec_names(self) -> list[str]:
        names: list[str] = []
        for codec in self.video_codecs:
            name = str(getattr(codec, "name", "") or "").upper()
            if name:
                names.append(name)
            value = str(getattr(codec, "value", "") or "").upper()
            if value:
                names.append(value)
        return names or ["AVC"]

    def _extract_download_urls_fallback(
        self,
        data: dict[str, Any],
    ) -> tuple[str, str | None]:
        """在 bilibili_api 流选择器遇到异常 payload 时手动挑流。

        移植说明：bilibili_api 的 detect_best_streams 在部分返回里会遇到
        video_codecs=None 并崩溃。这里只依赖接口原始 dict，优先选择配置范围
        内的最高画质和偏好编码，保证解析失败时能降级而不是炸出 AttributeError。
        """
        dash = data.get("dash") or {}
        videos = [
            item
            for item in dash.get("video") or []
            if isinstance(item, dict) and self._stream_url(item)
        ]
        audios = [
            item
            for item in dash.get("audio") or []
            if isinstance(item, dict) and self._stream_url(item)
        ]

        if videos:
            max_quality = self._safe_int(getattr(self.video_quality, "value", 64), 64)
            preferred_codecs = self._preferred_codec_names()

            def video_score(item: dict[str, Any]) -> tuple[int, int, int, int]:
                quality = self._safe_int(item.get("id"))
                within_quality = 0 if quality <= max_quality else 1
                codec_rank = self._codec_rank(
                    str(item.get("codecs") or ""),
                    preferred_codecs,
                )
                bandwidth = self._safe_int(item.get("bandwidth"))
                return (within_quality, codec_rank, -quality, -bandwidth)

            videos.sort(key=video_score)
            video_url = self._stream_url(videos[0])
            if not video_url:
                raise DownloadException("未找到可下载的视频流")

            audio_url = None
            if audios:
                audios.sort(
                    key=lambda item: self._safe_int(item.get("bandwidth")),
                    reverse=True,
                )
                audio_url = self._stream_url(audios[0])

            logger.debug(
                "[Bilibili] 手动流选择: "
                f"qn={videos[0].get('id')}, "
                f"codec={videos[0].get('codecs')}, "
                f"audio={bool(audio_url)}"
            )
            return video_url, audio_url

        durls = data.get("durl") or []
        if durls and isinstance(durls[0], dict):
            video_url = durls[0].get("url")
            if video_url:
                logger.debug("[Bilibili] 手动流选择: durl")
                return video_url, None

        raise DownloadException("未找到可下载的视频流")
