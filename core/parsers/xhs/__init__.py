import json
import re
from typing import Any, ClassVar
from urllib.parse import unquote

from astrbot.api import logger
from msgspec import convert

from ...config import PluginConfig
from ...cookie import CookieJar
from ...data import SendGroup
from ...download import Downloader
from ..base import BaseParser, ParseException, Platform, handle
from .card import XHSCardRenderer
from .model import NotePage


class XHSParser(BaseParser):
    # 平台信息
    platform: ClassVar[Platform] = Platform(name="xhs", display_name="小红书")
    # 值位置上的 undefined 才替换, 正文里的同名单词不受影响
    UNDEFINED_PATTERN: ClassVar[re.Pattern[str]] = re.compile(
        r"(?<=[:\[,])undefined(?=[,\]}])"
    )

    def __init__(self, config: PluginConfig, downloader: Downloader):
        super().__init__(config, downloader)
        self.mycfg = config.parser.xhs
        self.cookiejar = CookieJar(config, self.mycfg, domain="xiaohongshu.com")
        if self.cookiejar.cookies_str:
            self.ios_headers["cookie"] = self.cookiejar.cookies_str
        self.card = XHSCardRenderer(
            self, show_replies=self.mycfg.comment_show_replies is not False
        )
        self.comment_enable = self.mycfg.comment_render_enable is not False

    # 短链先跳一次; 落到登录 / 404 页时原笔记地址编码在 redirectPath 里, 解码后照样能匹配
    # http://xhslink.com/o/xxxx  http://xhslink.cn/m/xxxx
    @handle("xhslink.com", r"xhslink\.com/[A-Za-z0-9._?%&+=/#@-]+")
    @handle("xhslink.cn", r"xhslink\.cn/[A-Za-z0-9._?%&+=/#@-]+")
    async def _parse_short_link(self, searched: re.Match[str]):
        url = f"https://{searched.group(0)}"
        redirect_url = await self.get_redirect_url(url, self.ios_headers)
        if redirect_url == url:
            raise ParseException(f"无法重定向 URL: {url}")
        keyword, matched = self.search_url(unquote(redirect_url))
        return await self.parse(keyword, matched)

    # https://www.xiaohongshu.com/discovery/item/6a94f90e00000000070068fb?source=webshare&xhsshare=pc_web&xsec_token=...&xsec_source=pc_share
    # https://www.xiaohongshu.com/explore/6a94f90e00000000070068fb?xsec_token=...
    @handle(
        "xiaohongshu.com",
        r"(?:explore|discovery/item)/(?P<xhs_id>[0-9a-zA-Z]+)(?:\?(?P<query>[A-Za-z0-9._%&+=/#@-]+))?",
    )
    async def _parse_note(self, searched: re.Match[str]):
        xhs_id, query = searched.group("xhs_id", "query")
        # 移动端分享页不用登录, 还内嵌了热评; PC 端 explore 页没 Cookie 会跳登录
        url = f"https://www.xiaohongshu.com/discovery/item/{xhs_id}"
        if query:
            url += f"?{query}"
        async with self.session.get(url, headers=self.ios_headers) as resp:
            html = await resp.text()
            logger.debug(f"[小红书] {resp.url} | status: {resp.status}")

        data = self._extract_initial_state(html).get("noteData") or {}
        if not (data.get("data") or {}).get("noteData"):
            raise ParseException("小红书笔记不存在、已删除或分享链接已失效")
        page = convert(data["data"], type=NotePage)
        return self._build_result(page)

    def _build_result(self, page: NotePage):
        note = page.noteData
        comments = page.commentData.comments if self.comment_enable else []
        author = self.create_author(note.user.nickName, note.user.avatar)

        if note.type == "video" and note.video and (urls := note.video.urls):
            task = self.downloader.download_video(
                urls[0],
                video_name=f"xhs_{note.noteId}.mp4",
                headers=self.headers,
                proxy=self.proxy,
                backup_urls=urls[1:],
            )
            video = self.create_video_content_by_task(
                task, next(iter(note.image_urls), None), note.video.duration
            )
            contents = [video]
            send_groups = [SendGroup(contents=contents)]
            if comments:
                card = self.card.build(page, comments, author, show_note=False)
                send_groups.append(
                    SendGroup(contents=[card], force_merge=True, render_card=False)
                )
        else:
            contents = self.create_image_contents(note.image_urls)
            # 首图的下载任务图集和卡片共用, 同一张图不下两遍
            card = self.card.build(
                page,
                comments,
                author,
                show_note=True,
                cover=contents[0].path_task if contents else None,
            )
            send_groups = [
                SendGroup(contents=[card], force_merge=False, render_card=False),
                SendGroup(contents=contents),
            ]

        return self.result(
            title=note.title,
            text=note.desc,
            author=author,
            contents=contents,
            send_groups=send_groups,
            timestamp=note.time // 1000,
        )

    def _extract_initial_state(self, html: str) -> dict[str, Any]:
        matched = re.search(r"window\.__INITIAL_STATE__=(.*?)</script>", html)
        if not matched:
            raise ParseException("小红书分享链接失效或内容已删除")
        return json.loads(self.UNDEFINED_PATTERN.sub("null", matched.group(1)))
