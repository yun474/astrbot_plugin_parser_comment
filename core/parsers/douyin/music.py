from msgspec import Struct, field


class MusicAddress(Struct):
    url_list: list[str] = field(default_factory=list)


class Music(Struct):
    play_url: MusicAddress | None = None


class MusicClip(Struct):
    """图集配乐的截取位置（毫秒）与音量（百分比）。"""

    begin_time: int = 0
    end_time: int = 0
    volume: float = 100
