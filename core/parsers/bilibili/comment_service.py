from __future__ import annotations

import asyncio
import hashlib
import re
import time
import urllib.parse
from pathlib import Path

from aiohttp import ClientTimeout
from astrbot.api import logger
from curl_cffi.requests import AsyncSession as CurlAsyncSession
from msgspec import json as msgjson

from ...data import ImageContent
from ...exception import DownloadLimitException
from .comment_renderer import BiliComment, BiliCommentRenderer
from .common import bfs_thumb

MIXIN_KEY_ENC_TAB = [
    46,
    47,
    18,
    2,
    53,
    8,
    23,
    32,
    15,
    50,
    10,
    31,
    58,
    3,
    45,
    35,
    27,
    43,
    5,
    49,
    33,
    9,
    42,
    19,
    29,
    28,
    14,
    39,
    12,
    38,
    41,
    13,
    37,
    48,
    7,
    16,
    24,
    55,
    40,
    61,
    26,
    17,
    0,
    1,
    60,
    51,
    30,
    4,
    22,
    25,
    54,
    21,
    56,
    59,
    6,
    63,
    57,
    62,
    11,
    36,
    20,
    34,
    44,
    52,
]


class BiliCommentService:
    """Fetch, filter, and render Bilibili comments.

    移植说明：这里把 R 版评论区功能适配到 Zhalslar 原版解析器。
    返回值是带后台任务的 ImageContent，让 parse_video 立刻返回，
    主视频解析/下载链路仍然保持原版逻辑。
    """

    API_URL = "https://api.bilibili.com/x/v2/reply/main"
    WBI_API_URL = "https://api.bilibili.com/x/v2/reply/wbi/main"
    LEGACY_API_URL = "https://api.bilibili.com/x/v2/reply"
    NAV_API_URL = "https://api.bilibili.com/x/web-interface/nav"
    COMMENT_TYPE_VIDEO = 1
    MAX_FETCH_PAGES = 5
    COMMENT_PAGE_SIZE = 20

    def __init__(
        self,
        parser,
        renderer: BiliCommentRenderer,
        *,
        enabled: bool = True,
        comment_limit: int = 9,
        enable_text_ad_filter: bool = True,
        enable_qr_filter: bool = False,
        qr_check_max: int = 4,
        show_replies: bool = True,
        fetch_timeout: float = 8.0,
    ):
        self.parser = parser
        self.renderer = renderer
        self.enabled = enabled
        self.show_replies = show_replies
        self.comment_limit = max(0, min(int(comment_limit or 0), 20))
        self.enable_text_ad_filter = enable_text_ad_filter
        self.enable_qr_filter = enable_qr_filter
        self.qr_check_max = max(0, int(qr_check_max or 0))
        self.fetch_timeout = fetch_timeout

        self._ad_kw_re = re.compile(
            r"(微信|v信|vx|加微|私信|进群|福利|代理|兼职|看片|资源|加我|联系我|返利|推广|引流|合作)",
            re.IGNORECASE,
        )
        self._contact_re = re.compile(
            r"(wx[:：]?\s*[a-zA-Z][-_a-zA-Z0-9]{4,}|qq[:：]?\s*\d{5,}|tg[:：]?\s*[a-zA-Z0-9_]{4,})",
            re.IGNORECASE,
        )
        self._shortlink_re = re.compile(
            r"(https?://)?([a-zA-Z0-9-]+\.)?(t\.cn|u\.jd\.com|dwz\.cn|v\.douyin\.com|b23\.tv)/",
            re.IGNORECASE,
        )
        self._qr_detect_cache: dict[str, bool] = {}
        self._wbi_mixin_key: str | None = None
        self._wbi_mixin_key_expire = 0.0

    @property
    def headers(self) -> dict[str, str]:
        headers = self.parser.headers.copy()
        cookies = getattr(self.parser.mycfg, "cookies", None)
        if cookies:
            headers["Cookie"] = str(cookies).strip()
        return headers

    async def _build_request_headers(self) -> tuple[dict[str, str], bool]:
        """构造评论接口请求头，并复用扫码登录保存的凭证。"""
        headers = self.parser.headers.copy()
        cookie_header = ""
        authenticated = False

        try:
            credential = await self.parser.login.credential
            if credential:
                cookies = credential.get_cookies() or {}
                cookie_header = "; ".join(
                    f"{name}={value}"
                    for name, value in cookies.items()
                    if value is not None
                )
                authenticated = bool(cookies.get("SESSDATA"))
        except Exception as e:
            logger.warning(f"[Bilibili-comments] 读取登录凭证失败: {e}")

        if not cookie_header:
            raw_cookies = getattr(self.parser.mycfg, "cookies", None)
            if raw_cookies:
                cookie_header = str(raw_cookies).strip()
                authenticated = bool(
                    re.search(r"(?:^|;\s*)SESSDATA=", cookie_header, re.IGNORECASE)
                )

        if cookie_header:
            headers["Cookie"] = cookie_header
        return headers, authenticated

    @staticmethod
    def _wbi_key_part(url: str | None) -> str | None:
        if not url:
            return None
        name = str(url).rsplit("/", 1)[-1].split(".", 1)[0].strip()
        return name or None

    async def _get_wbi_mixin_key(
        self,
        session: CurlAsyncSession,
        headers: dict[str, str],
    ) -> str | None:
        now = time.time()
        if self._wbi_mixin_key and now < self._wbi_mixin_key_expire:
            return self._wbi_mixin_key

        try:
            resp = await session.get(
                self.NAV_API_URL,
                headers=headers,
                proxy=self.parser.proxy,
                timeout=self.fetch_timeout,
            )
            if resp.status_code != 200:
                return None
            payload = msgjson.decode(resp.content)
            wbi_img = (payload.get("data") or {}).get("wbi_img") or {}
            img_key = self._wbi_key_part(wbi_img.get("img_url"))
            sub_key = self._wbi_key_part(wbi_img.get("sub_url"))
            raw_key = f"{img_key or ''}{sub_key or ''}"
            if len(raw_key) < 64:
                return None

            mixin_key = "".join(raw_key[index] for index in MIXIN_KEY_ENC_TAB)[:32]
            self._wbi_mixin_key = mixin_key
            self._wbi_mixin_key_expire = now + 12 * 60 * 60
            return mixin_key
        except Exception as e:
            logger.debug(f"[Bilibili-comments] WBI key 获取失败: {e}")
            return None

    @staticmethod
    def _sign_wbi_params(params: dict, mixin_key: str) -> dict:
        signed = dict(params)
        signed["wts"] = int(time.time())

        filtered = {}
        for key, value in signed.items():
            if isinstance(value, str):
                value = "".join(ch for ch in value if ch not in "!'()*")
            filtered[key] = value

        query = urllib.parse.urlencode(sorted(filtered.items()))
        filtered["w_rid"] = hashlib.md5(f"{query}{mixin_key}".encode()).hexdigest()
        return filtered

    async def _get_comment_json(
        self,
        session: CurlAsyncSession,
        url: str,
        params: dict,
        headers: dict[str, str],
    ) -> dict:
        resp = await session.get(
            url,
            params=params,
            headers=headers,
            proxy=self.parser.proxy,
            timeout=self.fetch_timeout,
        )
        if resp.status_code != 200 or not resp.content:
            return {}
        try:
            payload = msgjson.decode(resp.content)
        except Exception:
            return {}
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _normalize_legacy_reply_page(block: dict, page_num: int) -> dict:
        page = block.get("page") or {}
        try:
            total = int(page.get("count") or 0)
            size = int(page.get("size") or 20)
            current = int(page.get("num") or page_num)
        except (TypeError, ValueError):
            total = 0
            size = 20
            current = page_num

        replies = block.get("replies") or []
        block["cursor"] = {
            "is_end": (not replies) or (total > 0 and current * size >= total),
            "next": current + 1,
            "all_count": total,
        }
        return block

    async def _fetch_comment_page(
        self,
        session: CurlAsyncSession,
        *,
        oid: int,
        type_: int,
        next_cursor: int,
        headers: dict[str, str],
    ) -> dict:
        base_params = {
            "oid": oid,
            "type": type_,
            "mode": 3,
            "next": next_cursor,
            "ps": self.COMMENT_PAGE_SIZE,
        }

        mixin_key = await self._get_wbi_mixin_key(session, headers)
        if mixin_key:
            try:
                payload = await self._get_comment_json(
                    session,
                    self.WBI_API_URL,
                    self._sign_wbi_params(base_params, mixin_key),
                    headers,
                )
                if payload.get("code") == 0:
                    return payload.get("data") or {}
                logger.debug(
                    "[Bilibili-comments] WBI 接口失败: "
                    f"oid={oid}, code={payload.get('code')}, "
                    f"message={payload.get('message')}"
                )
            except Exception as e:
                logger.debug(f"[Bilibili-comments] WBI 接口异常: oid={oid}, {e}")

        try:
            payload = await self._get_comment_json(
                session,
                self.API_URL,
                base_params,
                headers,
            )
            if payload.get("code") == 0:
                return payload.get("data") or {}
        except Exception as e:
            logger.debug(f"[Bilibili-comments] main 接口异常: oid={oid}, {e}")

        try:
            page_num = max(1, int(next_cursor or 1))
            payload = await self._get_comment_json(
                session,
                self.LEGACY_API_URL,
                {
                    "oid": oid,
                    "type": type_,
                    "sort": 2,
                    "pn": page_num,
                    "ps": self.COMMENT_PAGE_SIZE,
                },
                headers,
            )
            if payload.get("code") == 0:
                return self._normalize_legacy_reply_page(
                    payload.get("data") or {},
                    page_num,
                )
        except Exception as e:
            logger.debug(f"[Bilibili-comments] legacy 接口异常: oid={oid}, {e}")

        return {}

    def build_comment_image_content(
        self,
        oid: int,
        type_: int = COMMENT_TYPE_VIDEO,
        *,
        video_title: str,
        video_cover: str | None,
        up_name: str = "",
    ) -> list[ImageContent]:
        if not self.enabled or self.comment_limit <= 0:
            return []

        task = asyncio.create_task(
            self._build_comment_image(
                oid,
                type_,
                video_title=video_title,
                video_cover=video_cover,
                up_name=up_name,
            ),
            name=f"bili_comment_render_{oid}",
        )
        return [ImageContent(task)]

    @staticmethod
    def _normalise_url(url: str | None) -> str | None:
        if not url:
            return None
        if url.startswith("//"):
            return f"https:{url}"
        return url

    def _is_ad_like_text(self, text: str) -> bool:
        if not text:
            return False
        return bool(
            self._ad_kw_re.search(text)
            or self._contact_re.search(text)
            or self._shortlink_re.search(text)
        )

    @staticmethod
    def _clean_message(raw_msg: str) -> str:
        message = (raw_msg or "").strip()
        if len(message) > 320:
            message = f"{message[:320].rstrip()}…"
        return message

    async def _has_qr_in_image(self, img_url: str) -> bool:
        if img_url in self._qr_detect_cache:
            return self._qr_detect_cache[img_url]

        if len(self._qr_detect_cache) > 512:
            self._qr_detect_cache.clear()

        try:
            async with self.parser.session.get(
                img_url,
                headers=self.headers,
                proxy=self.parser.proxy,
                timeout=ClientTimeout(total=self.fetch_timeout),
            ) as resp:
                if resp.status != 200:
                    self._qr_detect_cache[img_url] = False
                    return False
                body = await resp.read()

            try:
                import cv2
                import numpy as np

                arr = np.frombuffer(body, dtype=np.uint8)
                img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
                if img is None:
                    self._qr_detect_cache[img_url] = False
                    return False
                detector = cv2.QRCodeDetector()
                decoded, points, _ = detector.detectAndDecode(img)
                has_qr = bool(decoded) or points is not None
            except Exception:
                # 二维码过滤是可选增强：缺 cv2/numpy 时不能影响评论图。
                has_qr = False

            self._qr_detect_cache[img_url] = has_qr
            return has_qr
        except Exception:
            self._qr_detect_cache[img_url] = False
            return False

    async def _should_skip_comment(
        self,
        message: str,
        pic_url: str | None,
        qr_check_counter: list[int],
    ) -> bool:
        if self.enable_text_ad_filter and self._is_ad_like_text(message):
            return True

        if (
            self.enable_qr_filter
            and pic_url
            and qr_check_counter[0] < self.qr_check_max
            and (not message or len(message.strip()) <= 8)
        ):
            qr_check_counter[0] += 1
            if await self._has_qr_in_image(pic_url):
                return True

        return False

    def _parse_reply(
        self, item: dict, upper_mid: int, *, is_top: bool = False
    ) -> BiliComment | None:
        """把接口的一条评论 (含楼中楼预览) 转成渲染用结构, 空评论返回 None"""
        rpid = str(item.get("rpid") or item.get("rpid_str") or "")
        content = item.get("content") or {}
        member = item.get("member") or {}
        message = self._clean_message(content.get("message") or "")
        pics = [
            url
            for pic in content.get("pictures") or []
            if (url := self._normalise_url(pic.get("img_src")))
        ]
        if not rpid or (not message and not pics):
            return None

        emotes = {}
        for text, emote in (content.get("emote") or {}).items():
            if url := self._normalise_url(emote.get("url")):
                emotes[text] = (url, int((emote.get("meta") or {}).get("size") or 1))
        mid = int(item.get("mid") or 0)
        control = item.get("reply_control") or {}
        return BiliComment(
            rpid=rpid,
            mid=mid,
            uname=str(member.get("uname") or "B站用户"),
            message=message,
            avatar_url=self._normalise_url(member.get("avatar")) or "",
            level=int((member.get("level_info") or {}).get("current_level") or 0),
            vip=(member.get("vip") or {}).get("vipStatus") == 1,
            like=int(item.get("like") or 0),
            ctime=int(item.get("ctime") or 0),
            location=str(control.get("location") or ""),
            is_up=bool(upper_mid) and mid == upper_mid,
            is_top=is_top,
            up_liked=bool((item.get("up_action") or {}).get("like")),
            rcount=int(item.get("rcount") or 0),
            pic_urls=pics,
            emotes=emotes,
            has_at=bool(content.get("at_name_to_mid")),
        )

    def _parse_sub_replies(self, item: dict, upper_mid: int) -> list[BiliComment]:
        subs = []
        for sub in item.get("replies") or []:
            comment = self._parse_reply(sub, upper_mid)
            if comment is None:
                continue
            if self.enable_text_ad_filter and self._is_ad_like_text(comment.message):
                continue
            subs.append(comment)
        return subs

    async def _fetch_comments(
        self, oid: int, type_: int
    ) -> tuple[list[BiliComment], int]:
        """抓热评, 返回 (评论列表, 评论总数)"""
        strict_list: list[BiliComment] = []
        relaxed_list: list[BiliComment] = []
        seen: set[str] = set()
        next_cursor = 0
        is_end = False
        all_count = 0
        qr_check_counter = [0]

        headers, authenticated = await self._build_request_headers()
        referer = f"https://www.bilibili.com/video/av{oid}"
        headers["Referer"] = referer

        def enough() -> bool:
            return (
                len(strict_list) >= self.comment_limit
                or len(strict_list) + len(relaxed_list) >= self.comment_limit
            )

        # B站评论接口会对 aiohttp 的普通 TLS 指纹返回 -352，且 HTTP 状态仍是
        # 200。项目已依赖 curl_cffi，这里先访问视频页取得 buvid，再复用同一
        # 浏览器指纹会话抓评论，避免把风控响应误判成“评论区为空”。
        async with CurlAsyncSession(impersonate="chrome131") as session:
            try:
                await session.get(
                    referer,
                    headers=headers,
                    proxy=self.parser.proxy,
                    timeout=self.fetch_timeout,
                )
            except Exception as e:
                logger.debug(f"[Bilibili-comments] 视频页预热失败，继续尝试接口: {e}")

            for _ in range(self.MAX_FETCH_PAGES):
                if is_end or enough():
                    break

                try:
                    block = await self._fetch_comment_page(
                        session,
                        oid=oid,
                        type_=type_,
                        next_cursor=next_cursor,
                        headers=headers,
                    )
                    if not block:
                        break

                    upper_mid = int((block.get("upper") or {}).get("mid") or 0)
                    top_replies = block.get("top_replies") or []
                    replies = [*top_replies, *(block.get("replies") or [])]
                    cursor = block.get("cursor") or {}
                    is_end = bool(cursor.get("is_end"))
                    try:
                        next_cursor = int(cursor.get("next", next_cursor + 1))
                    except (TypeError, ValueError):
                        next_cursor += 1

                    try:
                        all_count = int(cursor.get("all_count") or 0)
                    except (TypeError, ValueError):
                        all_count = 0
                    if is_end and all_count > len(replies) and len(replies) <= 4:
                        login_state = "登录态仍受限" if authenticated else "访客态"
                        logger.warning(
                            "[Bilibili-comments] "
                            f"{login_state}接口仅返回 {len(replies)} 条，"
                            f"实际评论数 {all_count}，oid={oid}"
                        )

                    for index, item in enumerate(replies):
                        comment = self._parse_reply(
                            item, upper_mid, is_top=index < len(top_replies)
                        )
                        if comment is None or comment.rpid in seen:
                            continue
                        seen.add(comment.rpid)

                        if await self._should_skip_comment(
                            comment.message,
                            comment.pic_urls[0] if comment.pic_urls else None,
                            qr_check_counter,
                        ):
                            continue

                        if self.show_replies:
                            comment.replies = self._parse_sub_replies(item, upper_mid)

                        # 带 @ 的评论多半是在回别人, 往后排; 置顶和 UP 主的除外
                        if "@" in comment.message and not (
                            comment.is_top or comment.is_up
                        ):
                            relaxed_list.append(comment)
                        else:
                            strict_list.append(comment)

                        if enough():
                            break
                except Exception as e:
                    logger.warning(f"[Bilibili-comments] 评论抓取失败: {e}")
                    break

        if len(strict_list) < self.comment_limit:
            strict_list.extend(relaxed_list[: self.comment_limit - len(strict_list)])

        if not strict_list:
            logger.warning(f"[Bilibili-comments] 未获取到可渲染评论: oid={oid}")

        return strict_list[: self.comment_limit], all_count

    async def _download_image(self, url: str | None) -> Path | None:
        url = self._normalise_url(url)
        if not url:
            return None
        try:
            return await self.parser.downloader.download_img(
                url,
                headers=self.headers,
                proxy=self.parser.proxy,
            )
        except Exception as e:
            logger.debug(f"[Bilibili-comments] 图片下载失败: {url}, {e}")
            return None

    async def _attach_assets(
        self,
        comments: list[BiliComment],
        video_cover: str | None,
    ) -> Path | None:
        """并发下载封面、头像、配图和表情, 下载失败的图直接不画"""
        jobs: list[tuple[str, BiliComment, str, str]] = []
        for root in comments:
            for comment in root.iter_all():
                jobs.append(
                    ("avatar", comment, "", bfs_thumb(comment.avatar_url, "96w_96h_1c"))
                )
                spec = "480w" if len(comment.pic_urls) == 1 else "240w_240h_1c"
                for url in comment.pic_urls[:9]:
                    jobs.append(("pic", comment, "", bfs_thumb(url, spec)))
                for text, (url, _) in comment.emotes.items():
                    jobs.append(("emote", comment, text, url))

        cover_path, results = await asyncio.gather(
            self._download_image(video_cover),
            asyncio.gather(*(self._download_image(job[3]) for job in jobs)),
        )
        for (kind, comment, key, _), path in zip(jobs, results):
            if path is None:
                continue
            if kind == "avatar":
                comment.avatar_path = path
            elif kind == "pic":
                comment.pic_paths.append(path)
            else:
                comment.emote_paths[key] = path
        return cover_path

    @staticmethod
    def _cache_key(oid: int, comments: list[BiliComment], limit: int) -> str:
        seed = "|".join(
            f"{c.rpid}:{c.like}:{len(c.replies)}"
            for root in comments
            for c in root.iter_all()
        )
        return hashlib.md5(f"{oid}:{limit}:{seed}".encode("utf-8")).hexdigest()[:10]

    async def _build_comment_image(
        self,
        oid: int,
        type_: int,
        *,
        video_title: str,
        video_cover: str | None,
        up_name: str,
    ) -> Path:
        try:
            comments, total = await self._fetch_comments(oid, type_)
            if not comments:
                raise DownloadLimitException("评论区为空或不可见")

            cache_key = self._cache_key(oid, comments, self.comment_limit)
            out_path = (
                self.parser.cfg.cache_dir / f"bili_comments_{oid}_{cache_key}.jpg"
            )
            if out_path.exists() and out_path.stat().st_size > 100:
                return out_path

            cover_path = await self._attach_assets(comments, video_cover)
            return await self.renderer.render(
                out_path,
                comments,
                title=video_title,
                up_name=up_name,
                total=total,
                cover_path=cover_path,
            )
        except DownloadLimitException:
            raise
        except Exception as e:
            logger.warning(f"[Bilibili-comments] 评论区渲染跳过: {e}")
            raise DownloadLimitException("评论区渲染失败") from e
