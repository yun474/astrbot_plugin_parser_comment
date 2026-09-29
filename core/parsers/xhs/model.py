"""小红书移动端分享页 __INITIAL_STATE__.noteData.data 的结构"""

from __future__ import annotations

from msgspec import Struct, field


class Image(Struct):
    fileId: str = ""
    url: str = ""

    @property
    def original_url(self) -> str:
        # 分享页给的 h5_1080jpg 带水印, 按 fileId 取原图没有
        if self.fileId:
            return f"https://sns-na-i1.xhscdn.com/{self.fileId}?imageView2/2/format/jpg"
        return self.url


class Stream(Struct):
    masterUrl: str
    backupUrls: list[str] = []


class Streams(Struct):
    h264: list[Stream] = []
    h265: list[Stream] = []
    av1: list[Stream] = []
    h266: list[Stream] = []


class VideoMeta(Struct):
    duration: int = 0


class Media(Struct):
    stream: Streams
    video: VideoMeta | None = None


class Video(Struct):
    media: Media

    @property
    def urls(self) -> list[str]:
        """主链 + 备用线路; h264 有水印, h265 无水印"""
        stream = self.media.stream
        for streams in (stream.h265, stream.h264, stream.av1, stream.h266):
            if streams:
                return [streams[0].masterUrl, *streams[0].backupUrls]
        return []

    @property
    def duration(self) -> float:
        return float(self.media.video.duration) if self.media.video else 0.0


class User(Struct):
    nickName: str
    avatar: str = ""
    userId: str = ""


class InteractInfo(Struct):
    likedCount: str = ""
    collectedCount: str = ""
    commentCount: str = ""
    shareCount: str = ""


class Note(Struct):
    noteId: str
    type: str
    user: User
    title: str = ""
    desc: str = ""
    time: int = 0
    imageList: list[Image] = []
    video: Video | None = None
    interactInfo: InteractInfo = field(default_factory=InteractInfo)

    @property
    def image_urls(self) -> list[str]:
        return [url for image in self.imageList if (url := image.original_url)]


class CommentUser(Struct):
    nickname: str = "小红书用户"
    image: str = ""
    userId: str = ""


class Picture(Struct):
    url: str = ""
    originUrl: str = ""


class Comment(Struct):
    id: str
    content: str = ""
    user: CommentUser = field(default_factory=CommentUser)
    time: int = 0
    ipLocation: str = ""
    likeViewCount: str = ""
    subCommentCount: int = 0
    pictures: list[Picture] = []
    subComments: list[Comment] = []


class CommentData(Struct):
    comments: list[Comment] = []
    commentCount: int = 0


class NotePage(Struct):
    noteData: Note
    commentData: CommentData = field(default_factory=CommentData)
