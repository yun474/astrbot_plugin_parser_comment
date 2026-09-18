import html
import re
from asyncio import Task
from datetime import datetime, tzinfo
from pathlib import Path

from msgspec import Struct

OFFICIAL_CARD_PATTERN = r"(?ms)^\[卡片消息\](?=.*^source[:：]\s*哔哩哔哩\s*$).*?^title[:：]\s*(?P<title>.+?)\s*$"
"""官 Bot 收到的 B站小程序分享卡片: 平台把它转成 "[卡片消息] 小程序 / 摘要 / preview /
source: 哔哩哔哩 / source_logo / title" 这样的多行文本, 没有链接, 只能靠标题去搜"""

_SEARCH_TAG_RE = re.compile(r"</?em[^>]*>")


class Upper(Struct):
    mid: int
    """用户 ID"""
    name: str
    """作者"""
    face: str
    """头像"""


def normalize_title(title: str) -> str:
    """去掉搜索结果里的高亮标签和多余空白, 用于标题比对"""
    text = html.unescape(_SEARCH_TAG_RE.sub("", title or ""))
    return re.sub(r"\s+", "", text).casefold()


def pick_video_by_title(title: str, results: list[dict]) -> str | None:
    """从搜索结果里挑同名视频的 bvid: 优先完全一致, 其次前缀一致 (卡片标题可能被截断)"""
    wanted = normalize_title(title)
    candidates = [
        (normalize_title(item.get("title", "")), str(item["bvid"]))
        for item in results
        if item.get("bvid")
    ]
    for found, bvid in candidates:
        if found == wanted:
            return bvid
    for found, bvid in candidates:
        if found.startswith(wanted) or wanted.startswith(found):
            return bvid
    return None


def fmt_count(value: int) -> str:
    """B站风格的计数: 1234 / 1.2万 / 1.1亿"""
    if value >= 100_000_000:
        text = f"{value / 100_000_000:.1f}亿"
    elif value >= 10_000:
        text = f"{value / 10_000:.1f}万"
    else:
        return str(value)
    return text.replace(".0", "")


def fmt_duration(seconds: int) -> str:
    hours, rest = divmod(int(seconds), 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


def fmt_relative_time(timestamp: int, tz: tzinfo) -> str:
    """B站评论区的时间写法: 刚刚 / n分钟前 / n小时前 / n天前 / 日期"""
    now = datetime.now(tz)
    moment = datetime.fromtimestamp(timestamp, tz)
    delta = (now - moment).total_seconds()
    if delta < 60:
        return "刚刚"
    if delta < 3600:
        return f"{int(delta // 60)}分钟前"
    if delta < 86400:
        return f"{int(delta // 3600)}小时前"
    if delta < 7 * 86400:
        return f"{int(delta // 86400)}天前"
    return moment.strftime("%m-%d" if moment.year == now.year else "%Y-%m-%d")


def bfs_thumb(url: str, spec: str) -> str:
    """给 B站图床链接加缩放参数, 如 spec='96w_96h_1c' / '480w'"""
    return f"{url}@{spec}.webp" if "hdslb.com/bfs/" in url and "@" not in url else url


async def settle_path(path: Path | Task[Path] | None) -> Path | None:
    """等待下载任务, 失败当作没有这张图"""
    if path is None:
        return None
    if isinstance(path, Path):
        return path
    try:
        return await path
    except Exception:
        return None
