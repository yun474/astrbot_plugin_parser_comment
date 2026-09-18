from msgspec import Struct, field


class PlayAddr(Struct):
    url_list: list[str]


class Cover(Struct):
    url_list: list[str]


class Video(Struct):
    play_addr: PlayAddr
    cover: Cover
    duration: int


class Image(Struct):
    video: Video | None = None
    url_list: list[str] = field(default_factory=list)


class Avatar(Struct):
    url_list: list[str]


class Author(Struct):
    nickname: str
    # avatar_larger: Avatar
    avatar_thumb: Avatar


class SlidesData(Struct):
    author: Author
    desc: str
    create_time: int
    images: list[Image]

    @property
    def name(self) -> str:
        return self.author.nickname

    @property
    def avatar_url(self) -> str | None:
        url_list = self.author.avatar_thumb.url_list
        return url_list[0] if url_list else None

    @property
    def image_url_lists(self) -> list[list[str]]:
        """每张图片的镜像地址列表, 顺序即优先级"""
        return [image.url_list for image in self.images]

    @property
    def dynamic_url_lists(self) -> list[list[str]]:
        """每段动图视频的镜像地址列表"""
        return [image.video.play_addr.url_list for image in self.images if image.video]


class SlidesInfo(Struct):
    aweme_details: list[SlidesData] = field(default_factory=list)
