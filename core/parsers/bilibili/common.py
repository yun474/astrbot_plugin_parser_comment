from asyncio import Task
from datetime import datetime, tzinfo
from pathlib import Path

from msgspec import Struct


class Upper(Struct):
    mid: int
    """用户 ID"""
    name: str
    """作者"""
    face: str
    """头像"""


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
