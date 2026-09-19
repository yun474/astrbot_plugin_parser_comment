"""B站 Web 接口客户端

评论、搜索这类接口对普通 TLS 指纹和无 buvid 的访客会限流或只给残缺结果, 这里统一用
curl_cffi 的浏览器指纹会话, 先访问一次站内页面拿 buvid, 复用扫码 / 配置的 Cookie,
需要的接口再做 WBI 签名。
"""

from __future__ import annotations

import hashlib
import re
import time
import urllib.parse

from curl_cffi.requests import AsyncSession as CurlAsyncSession
from msgspec import json as msgjson

from astrbot.api import logger

MIXIN_KEY_ENC_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49,
    33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40, 61,
    26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36,
    20, 34, 44, 52,
]  # fmt: skip


class BiliWebClient:
    NAV_API_URL = "https://api.bilibili.com/x/web-interface/nav"
    IMPERSONATE = "chrome131"
    USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    )
    """UA 和 TLS 指纹保持同一个 Chrome 版本, 解析器默认的老 UA 会被搜索接口降级"""

    def __init__(self, parser, timeout: float = 8.0):
        self.parser = parser
        self.timeout = timeout
        self._mixin_key: str | None = None
        self._mixin_key_expire = 0.0

    def session(self) -> CurlAsyncSession:
        return CurlAsyncSession(impersonate=self.IMPERSONATE)

    async def headers(self) -> tuple[dict[str, str], bool]:
        """请求头 (含 Cookie) 和是否带登录态; 优先扫码登录保存的凭证, 其次配置里的 Cookie"""
        headers = {**self.parser.headers, "User-Agent": self.USER_AGENT}
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
            logger.warning(f"[Bilibili] 读取登录凭证失败: {e}")

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

    async def warm_up(
        self, session: CurlAsyncSession, headers: dict[str, str], url: str
    ) -> None:
        """先访问一个站内页面, 让会话拿到 buvid 等风控 Cookie"""
        try:
            await session.get(
                url, headers=headers, proxy=self.parser.proxy, timeout=self.timeout
            )
        except Exception as e:
            logger.debug(f"[Bilibili] 页面预热失败, 继续尝试接口: {e}")

    async def get_json(
        self,
        session: CurlAsyncSession,
        url: str,
        params: dict,
        headers: dict[str, str],
        *,
        wbi: bool = False,
    ) -> dict:
        """请求 JSON 接口, 非 200 / 非 JSON 时返回空 dict"""
        if wbi:
            if mixin_key := await self._get_mixin_key(session, headers):
                params = self.sign(params, mixin_key)
        resp = await session.get(
            url,
            params=params,
            headers=headers,
            proxy=self.parser.proxy,
            timeout=self.timeout,
        )
        if resp.status_code != 200 or not resp.content:
            return {}
        try:
            payload = msgjson.decode(resp.content)
        except Exception:
            return {}
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _key_part(url: str | None) -> str | None:
        if not url:
            return None
        name = str(url).rsplit("/", 1)[-1].split(".", 1)[0].strip()
        return name or None

    async def _get_mixin_key(
        self, session: CurlAsyncSession, headers: dict[str, str]
    ) -> str | None:
        now = time.time()
        if self._mixin_key and now < self._mixin_key_expire:
            return self._mixin_key

        try:
            payload = await self.get_json(session, self.NAV_API_URL, {}, headers)
            wbi_img = (payload.get("data") or {}).get("wbi_img") or {}
            raw_key = (
                f"{self._key_part(wbi_img.get('img_url')) or ''}"
                f"{self._key_part(wbi_img.get('sub_url')) or ''}"
            )
            if len(raw_key) < 64:
                return None
            self._mixin_key = "".join(raw_key[i] for i in MIXIN_KEY_ENC_TAB)[:32]
            self._mixin_key_expire = now + 12 * 60 * 60
            return self._mixin_key
        except Exception as e:
            logger.debug(f"[Bilibili] WBI key 获取失败: {e}")
            return None

    @staticmethod
    def sign(params: dict, mixin_key: str) -> dict:
        """WBI 签名: 参数按 key 排序后拼上 mixin key 取 md5 作为 w_rid"""
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
