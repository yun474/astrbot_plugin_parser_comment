"""通过网页签名请求补齐移动分享接口省略的实况视频。"""

import msgspec

from astrbot.api import logger

from .video import Image


async def load_gallery_images(parser, vid: str, images):
    if not images or any(
        image.video and image.video.play_addr.url_list for image in images
    ):
        return images

    try:
        browser = await parser.html_renderer._get_browser()
        options = {"locale": "zh-CN"}
        if parser.proxy:
            options["proxy"] = {"server": parser.proxy}
        context = await browser.new_context(**options)
        try:
            cookies = [
                {
                    "name": c.name,
                    "value": c.value,
                    "domain": c.domain,
                    "path": c.path,
                    "secure": c.secure,
                }
                for c in parser.cookiejar.cookies
                if not c.is_expired()
                and c.domain.lstrip(".") in ("douyin.com", "www.douyin.com")
            ]
            if cookies:
                await context.add_cookies(cookies)
            page = await context.new_page()
            await page.goto(
                f"https://www.douyin.com/note/{vid}",
                wait_until="load",
                timeout=25000,
            )
            payload = await page.evaluate(
                """async (vid) => {
                    const query = new URLSearchParams({
                        device_platform: 'webapp', aid: '6383',
                        channel: 'channel_pc_web', aweme_id: vid
                    });
                    const response = await fetch(
                        '/aweme/v1/web/aweme/detail/?' + query.toString(),
                        {credentials: 'include', signal: AbortSignal.timeout(10000)}
                    );
                    if (!response.ok) throw new Error(`HTTP ${response.status}`);
                    return await response.json();
                }""",
                vid,
            )
            detail = payload.get("aweme_detail") or {}
            if payload.get("status_code") != 0 or str(detail.get("aweme_id")) != vid:
                raise ValueError("作品详情未返回对应作品")
            full_images = msgspec.convert(detail.get("images"), type=list[Image])
            if len(full_images) != len(images):
                raise ValueError("作品详情与分享页的图片数量不一致")
            return full_images
        finally:
            await context.close()
    except Exception as e:
        logger.warning(
            f"[抖音] 实况视频获取失败，保留分享页图片: {type(e).__name__}: {e}"
        )
        return images
