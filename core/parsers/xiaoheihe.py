import asyncio
import hashlib
import html
import json
import random
import re
import time
from typing import Any, ClassVar
from urllib.parse import urlparse

from curl_cffi import requests as curl_requests

from astrbot.api import logger

from ..config import PluginConfig
from ..data import MediaContent, Platform, SendGroup, TextContent, VideoContent
from ..download import Downloader
from ..exception import ParseException
from .base import BaseParser, handle

V4_EP = (
    "V1ZCERzVgMWrKv+VcTl5QmS9JuPWLOQ8A0mACeTyYXtTbiguOrHhwaqnagZ6zdAgF"
    "4WpAYBvUH3EDnPRlNWut4CTDU1tCa80BSnvTMC9X1j9Kh6IMlGmzPIqpBzzx9r7Nt"
    "9XtUhv2WiQ2BgPnUwOFe7gN9r8Yj3184qxn1btJL8="
)
V4_DATA = (
    "abbbe96a1579aa6fe4fa84e875851b7d7a843a14c5c9573c771d9c1443c9b3a"
    "d7603a8d9d67dbc9bd001bf42702ac82e4a6979323ff305eecd74b9620ee140"
    "0c135f840b35d9402ec3e3a93fcb3d0d3d6b3e740f5176b72225b6fb8a0d483"
    "cab753aa71062dc9b59bc8de950628f23607301c6cd94e75f680b86485a11ac"
    "36eba1413e9f14b274eadff30114dfb1cedadc4bd08ef83c5b2d048970d07d3"
    "943afef809b44e3b9fee602c91e274fee1523a8beee7e7cec85680b279d616d"
    "da15e98b1b0aa718276bcdb05d4ac3e44e72da220e0ea798ad7452aec01d0db"
    "c31ad6bf147eab7f7e539d35fe5149110aae5c7069a67eba4aae638505819f8"
    "9e2a58bc3b5001c8a5045334121ef04a8e442d7dbb7776bd6013674d2c0028a"
    "f131bf6bde47b90dce5c8b9463c9f83d0e7264145c2f6f259d70c4d63a4996b"
    "b7c0074e8a59fa298ad144ec139cb29bc94074fbe2f4a88400d85c003793e2b"
    "e2077184c3ba2e792926fce25f24d3a764a7c2667446173c74aa704d0d517f2"
    "10926aaef05376230b43c3a676dad6ff1c9603553d66eadfb492445eac44745"
    "acc620b325560d4941c10e05f3099a17a553fd763a1b7d6ef29f512e436bdfa"
    "9fa7c5a70b6a5f91bbcb21946fc2ce92db0c92930008b0fc82e90c3c73f9265"
    "2ca388f77b262a918cf59160fa88e481138ee7fe9a9b51d7949a74d22d1dab4"
    "e865c12325bfb5b9e748526afb6d8a05c543fd6dc72e81b06a4ebbf8149fca5"
    "37a19330da2011eec0229e2302babe239397aa1c2292ab3807cf0aa129d078a"
    "a9da010003eac5bb2c06435fbbe9bee7543290c1224745bb485d78f42ee4e82"
    "afb27a38befc60a688fb2514795064926bf205357bd46b7c14dd15aea2cab48"
    "5c993f0df5a20811d0a7b3bfb1fcb0737c8305675e9bdac396ef8cffb0b6bc4"
    "700c3d881c1945329b721b9080bed46b18105b7c9fea4f8276f0fcd09fe99ec"
    "52fa50b11e12a19eb9d091ecde701ab2879e2d7727386b28bbde8d62832e1ad"
    "822ea57b383cdd3767e8ee64e201bf00fe9cc8428ece3262550764fea47c69e"
    "e4339de98767f034d8852993fdefa315d9dcda71a74b665804706d4f9a8c139"
    "3670c2220e4ceac833620e0dc8175eb7a77b8b37c1a9d9940c67d44c8bc6b5f"
    "9e46273e2f5149d3d3148e8f7a02c4a4c3c998924b7d0e93528952034adc20d"
    "c342404a8606f0c07cb2b98c4a5434e69b69282daf952f586b9eed4b4f1ef0c"
    "fe5c6d156d14fb5057c8c32a355d07e2f56737d1ccfad573d42c840bbe8b750"
    "388211f2c0c5d6a1e34e7741389a742dff58bb0b9f339707a349a09519ca78d"
    "5e4f1baaf2598ab9001c15824494eecc17735e69a193e5437cbe44c6f156a0b"
    "b8df4fed5edefd4f56f4ef0b4d8cc40fe623836da3c5e662005825c9d344074"
    "be2306d6241c163fe92a6ce40ff60538d7464f5a06b6bb9ca1e6f18491ca3c7"
    "d6c00e299cbb1ca1c525a981fc6c6f2bb05f709101099b8bd0d2c2a628d94c6"
    "1aa97fdd58c9f357359fbd5be9e8f0f534f4481fb780d58e3e599e01fdd5a7f"
    "c5fb7e01b76fd58b2f264947d2149fefa57577ef326e264fc827939329031d9"
    "01be7579ecf5fccdab11c615c1a053f198297c0723faf8b17ea3335d49df2bf"
    "dd17271c2b64745b1f412d87297edd4404a4ae5312debf73b66afcc3d884b93"
    "8de41b6ee87265ce624897f3557ebe2d97e6fb17f1dc6a893e48dfa16ef2bff"
    "d8f3e06f0a1fcf44c7f2efa372e0ff61344c93f4a2a66538fcc134cd0bf94d5"
    "4c969cda4392af70608cbab6cfa340b674ba3a59385c0ed9bb236ff6ed10e1e"
    "5a9d4b6529c075dc1ac23cfdae18ab1651a5ee747322e51e3cc6035ca929789"
    "00924e661a2694a47873569baa95fd821711dc53a1e0299ed707e337b570591"
    "a3f61a5e39f8a75771da1613e8236c9b1b94cb5617fdaf2424d68a7fbd83ebf"
    "356fc87e8a805bee5bbd20a55a70881394d7624b1dcf5a135f1cf40b842eca3"
    "3d46b72447e0a2e85adf6c26efa6cc73b63573840f7b6229fb03ab45a8b639b"
    "5a66bbd6f63d10e59db49d7a9c9af3e3aeb79b7b756e24d5002917e7e788018"
    "4f80fcc605a1ba825c779e6083fd7fb0920bbcee021ec8e35427391b871b149"
    "c306c2dbda602044cd53ec424dd70cfd1c14a23c9964c039258cff4b75112f8"
    "15d9717433c1989ec398cd2acd67c89be82a409e0ef8f3e9ea8ec8b51b5ea5a"
    "005b5e735978d9a2987a76d62a2af230e30dc6327f7c0d153add27c7e8a320e"
    "4df6c05ab91fe0b9f6f9e13c50f39454066776503eb2ec84b74b4b2d5228627"
    "d81c938f7201610c9b703e4fd283a94835b7387db2880443a050d3eb0859aa1"
    "efd0f9bb7613b6b918ec2f7b5bb3e7722105b595e7973a93e3de8153a0f8e5b"
    "fd1aa6cefc6285fea85e8381ddcce98b31dda33db2a3c80ac04df14b872c805"
    "15373f231c3653fb2db799b32e83e59fb0f5763febca3d291b49bf83dd7ebd6"
    "1229300b65d44964d9e679f6061a0b2ea1bcd9f5af9bf710047237d87d13394"
    "ea8b4627c6997589d0b58379d025b076460eab88d6615ee92b0aa6c47f721f9"
    "7e0b5bbe721f06544d0a1bb81402697f2d72ad32c791dab45064b4d18460602"
    "9494b268feaebb268e7f92352dc3482f857c14885aabbad98a43e5f8fa5d77d"
    "61dc22f23080b9e6403c76f5fb862d7520ab85ae7c1d0e339729f664e7d668f"
    "4b9d1301acabb62fda5940db236ea9d2ca896cbb6a13eda6120fa5881453cb4"
    "490438460c00db4cd4bdf5df993d3a8d5726c756015eed542e0a4b910570f39"
    "7211c3f84f6a0d038e82270f94543e8da1e8d0cffd8f4f561daaf6003ad1fad"
    "fdd89c50f057a79225d8647aead74b33216e328c4204686b4ae93ce5f7ee25e"
    "1c83fe2cb72c67589aa4865d278ff7a112d09c16707de8acd61b49b901a3266"
    "e8ef55f1351fdc3013154635e51e649cbf31fc9b32f6956800834ca73e0b75b"
    "2b54d7125257eb6c24ebff52b741109be6da99bb6e0ffab85c3c219550ec3fc"
    "b12e2e4d0234627b061193c290baa1be73241be70925c08d33e6efdd44eca9a"
    "5160bdc5b47bd1f9d3f2cbf38848cf1aaa2a4827f86e43e06246b3bf94cb0b9"
    "f050c89533a3be9ffecefebd1a92e04197f18d7fadc0bfc8664de18425d5c03"
    "59b58049267934756f513bd68ea427b38f15213f42cce05cd59f5ea502967ec"
    "6a096daaa5e5d2a373227f2fe4514e27dfa012d708f7e94a286452972b5fab4"
    "581ecee3df40bad802cbb50b1a5d9dd3323a5f7c61ab893b16782a0ba64fd42"
    "10c30ac00f9d21b9124e5e5b323f43badf56761e1eea5c86ff61f19ce1485f4"
    "2cf6cadd751bbfb2ef87229eee5068ef6e209f123d29a571a374974ceac2e77"
    "f143faba60fc5d16f88d801fa01d879420b5d1393ad5b2bc913e3b0ba7155a6"
    "7648196573126273cccc79f2eac32ab68d72cc0f7170feca9c9726af9d65962"
    "663d5281372386ec88bd2fa82316f687535ecd39f00658523708ca4785529f5"
    "93baf100597ed00c15ae8ff87baa295871680b4096ac03a550f0f015297198b"
    "1a93f38cfefbeceabc099c1026664d77f616b4f069cf8bf53d2684b9a4d933c"
    "3c65a3aef21559527bfc6586e0247efa244a0a355b43751bc09be8012699468"
    "a8c332d60b11bb4881bf56b92ead10e059ac40f83a4d6725cacbc1bb307c839"
    "c4edc8b5484b9e2935842e867e739223f2eaaaff04d9701cfa49e3f80be4f2d"
    "1b7e8eb76fd7f33dfa79831f75ee65a75b7c7fff98254818f1ab77bca856656"
    "4d48e0012733dd426bf841f27f960394b1bacb8a3e36b96c41d751584cd580f"
    "ef1b6a8bf990487268348f682a27549ecbb9674b14f2fc97f203f3468f248ec"
    "3cf5171aa5e8a8d31a9a433c4f7644736aaf6695b28771fe66b4736e3afb322"
    "11ad534b05641600d2cdc79a251fc4c4e5540df9a40aaad329fedd49a429b20"
    "70e1345a4146c297ee2a03f056675054e83207d17de21242032c30398259440"
    "84e60cbd70eb4c469859824cd7d04340de0d19e614a0826a63c63e15c3372b1"
    "7515d4b6951ff6c612f65c3e6538fd0515bcb4814bb641fca5a45c7dae9"
)


class XiaoheiheParser(BaseParser):
    platform: ClassVar[Platform] = Platform(name="xiaoheihe", display_name="小黑盒")
    CHAR_TABLE: ClassVar[str] = "AB45STUVWZEFGJ6CH01D237IXYPQRKLMN89"
    API: ClassVar[str] = "https://api.xiaoheihe.cn"
    # 与网页端 (web_version 3.0) 保持一致的公共参数
    WEB_PARAMS: ClassVar[dict[str, str]] = {
        "app": "heybox",
        "os_type": "web",
        "x_app": "heybox_website",
        "x_client_type": "web",
        "x_os_type": "Windows",
        "x_client_version": "",
        "client_type": "web",
        "web_version": "3.0",
        "version": "999.0.4",
    }
    CAPTCHA_TIP: ClassVar[str] = (
        "小黑盒触发了人机验证，请在浏览器登录小黑盒并通过验证后，"
        "把 Cookie 填入插件的小黑盒解析器配置"
    )

    def __init__(self, config: PluginConfig, downloader: Downloader):
        super().__init__(config, downloader)
        self.mycfg = config.parser.xiaoheihe
        # 接口请求头: UA 交给 curl_cffi 的浏览器指纹, 避免与 TLS 指纹不一致
        self.api_headers = {
            "accept": "application/json, text/plain, */*",
            "referer": "https://www.xiaoheihe.cn/",
            "origin": "https://www.xiaoheihe.cn",
        }
        if self.mycfg.cookies:
            self.api_headers["cookie"] = self.mycfg.cookies
        # 媒体下载请求头, 不携带 Cookie
        self.headers["referer"] = "https://www.xiaoheihe.cn/"
        # 匿名设备 token, 首次解析时通过指纹接口换取, 之后复用
        self._anon_token: str | None = None

    @handle(
        "xiaoheihe.cn/app/bbs/link",
        r"xiaoheihe\.cn/app/bbs/link/(?P<link_id>[0-9a-z]+)",
    )
    async def _parse_bbs_web(self, searched: re.Match[str]):
        return await self._parse_bbs_by_link_id(searched.group("link_id"))

    @handle(
        "api.xiaoheihe.cn/v3/bbs/app/api/web/share",
        r"api\.xiaoheihe\.cn/v3/bbs/app/api/web/share\?[A-Za-z0-9._%&+=/#@?;-]*link_id=(?P<link_id>[0-9a-z]+)",
    )
    async def _parse_bbs_share(self, searched: re.Match[str]):
        return await self._parse_bbs_by_link_id(searched.group("link_id"))

    @handle(
        "api.xiaoheihe.cn/game/share_game_detail",
        r"api\.xiaoheihe\.cn/game/share_game_detail\?[A-Za-z0-9._%&+=/#@?;-]*appid=(?P<appid>[0-9a-z]+)[A-Za-z0-9._%&+=/#@?;-]*game_type=(?P<game_type>[a-z]+)",
    )
    async def _parse_game_share(self, searched: re.Match[str]):
        return await self._parse_game_by_appid(
            searched.group("appid"), searched.group("game_type")
        )

    @handle(
        "xiaoheihe.cn/app/topic/game",
        r"xiaoheihe\.cn/app/topic/game/(?P<game_type>[a-z]+)/(?P<appid>[0-9a-z]+)",
    )
    async def _parse_game_web(self, searched: re.Match[str]):
        return await self._parse_game_by_appid(
            searched.group("appid"), searched.group("game_type")
        )

    # ---------------- 帖子 ----------------

    async def _parse_bbs_by_link_id(self, link_id: str):
        link = await self._fetch_link(link_id)

        title = self._clean_text(str(link.get("title") or "")) or None
        final_url = f"https://www.xiaoheihe.cn/app/bbs/link/{link_id}"
        author = self._build_author(link)

        body_text, image_urls = self._parse_body_text_and_images(link)
        video_content = self._build_video_content(link)
        show_body_text = bool(self.mycfg.show_body_text)
        text_content = TextContent(body_text) if show_body_text and body_text else None

        contents: list[MediaContent] = []
        if image_urls:
            contents.extend(
                self.create_image_contents(image_urls, headers=self.headers)
            )
        if video_content is not None:
            contents.append(video_content)
        if text_content is not None:
            contents.append(text_content)

        info_parts: list[str] = []
        if image_urls:
            info_parts.append(f"正文图片 {len(image_urls)} 张")
        if video_content is not None:
            info_parts.append("正文视频 1 个")

        return self.result(
            title=title,
            text=None if show_body_text else (body_text or None),
            url=final_url,
            author=author,
            contents=contents,
            send_groups=self._build_send_groups(contents),
            extra={
                "info": "，".join(info_parts) if info_parts else None,
                "body_image_urls": image_urls,
                "source_kind": "xiaoheihe_link_tree",
                "has_video": bool(link.get("has_video")),
                "video_url": link.get("video_url"),
            },
        )

    async def _fetch_link(self, link_id: str) -> dict[str, Any]:
        params = {
            "link_id": link_id,
            "is_first": "1",
            "page": "1",
            "index": "1",
            "limit": "20",
            "owner_only": "0",
        }
        payload = await self._api_get(
            "/bbs/app/link/tree", params, cookies=await self._device_cookies()
        )
        result = payload.get("result")
        link = result.get("link") if isinstance(result, dict) else None
        if not isinstance(link, dict):
            raise ParseException("小黑盒 link/tree 缺少 link 节点")
        return link

    async def _device_cookies(self) -> dict[str, str] | None:
        """配置里没有 x_xhh_tokenid 时, 用指纹接口换一个匿名设备 token"""
        if self._extract_xhh_tokenid_from_cookies():
            return None
        if self._anon_token is None:
            device_id = await self._fetch_device_id()
            if not device_id:
                raise ParseException("小黑盒 deviceprofile 未返回 deviceId")
            self._anon_token = f"B{device_id}"
        return {"x_xhh_tokenid": self._anon_token}

    def _extract_xhh_tokenid_from_cookies(self) -> str | None:
        cookie_header = self.api_headers.get("cookie", "")
        matched = re.search(r"(?:^|;\s*)x_xhh_tokenid=([^;]+)", cookie_header)
        return matched.group(1) if matched else None

    async def _fetch_device_id(self) -> str | None:
        payload = {
            "appId": "heybox_website",
            "organization": "0yD85BjYvGFAvHaSQ1mc",
            "ep": V4_EP,
            "data": V4_DATA,
            "os": "web",
            "encode": 5,
            "compress": 2,
        }
        response = await self._request_json(
            "POST",
            "https://fp-it.portal101.cn/deviceprofile/v4",
            json=payload,
        )
        detail = response.get("detail") or {}
        device_id = detail.get("deviceId")
        return str(device_id) if device_id else None

    # ---------------- 游戏 ----------------

    async def _parse_game_by_appid(self, appid: str, game_type: str):
        appid = appid.strip()
        if not appid:
            raise ParseException("无效的小黑盒游戏 appid")
        game_type = game_type.strip().lower() or "pc"

        game = await self._fetch_game_detail(appid, game_type)
        steam_appid = self._pick_steam_appid(game, appid)
        intro = await self._fetch_game_intro(steam_appid) if steam_appid else {}

        web_url = f"https://www.xiaoheihe.cn/app/topic/game/{game_type}/{appid}"
        title = self._build_game_title(game)
        desc = self._build_game_desc(game, intro)
        image_urls = self._extract_game_images(game)
        video_entries = self._extract_game_videos(game)

        show_body_text = bool(self.mycfg.show_body_text)
        text_content = TextContent(desc) if show_body_text and desc else None

        contents: list[MediaContent] = []
        if image_urls:
            contents.extend(
                self.create_image_contents(image_urls, headers=self.headers)
            )
        for video_url, video_cover in video_entries:
            contents.append(self._build_video_content_from_url(video_url, video_cover))
        if text_content is not None:
            contents.append(text_content)

        info_parts: list[str] = []
        if image_urls:
            info_parts.append(f"游戏图片 {len(image_urls)} 张")
        if video_entries:
            info_parts.append(f"游戏视频 {len(video_entries)} 个")

        return self.result(
            title=title,
            text=None if show_body_text else (desc or None),
            url=web_url,
            contents=contents,
            send_groups=self._build_send_groups(contents),
            extra={
                "info": "，".join(info_parts) if info_parts else None,
                "body_image_urls": image_urls,
                "source_kind": "xiaoheihe_game_detail",
                "game_appid": appid,
                "steam_appid": steam_appid,
                "game_type": game_type,
                "video_url": video_entries[0][0] if video_entries else None,
                "video_urls": [item[0] for item in video_entries],
            },
        )

    async def _fetch_game_detail(self, appid: str, game_type: str) -> dict[str, Any]:
        """网页端按平台走不同的详情接口"""
        if game_type == "pc":
            path, params = "/game/get_game_detail/", {"steam_appid": appid}
        elif game_type == "console":
            path, params = (
                "/game/console/get_game_detail/",
                {"appid": appid, "platf": game_type},
            )
        else:
            path, params = (
                "/game/mobile/get_game_detail/",
                {"appid": appid, "platf": game_type},
            )
        payload = await self._api_get(path, params)
        result = payload.get("result")
        if not isinstance(result, dict) or not result:
            raise ParseException("小黑盒游戏详情为空")
        return result

    async def _fetch_game_intro(self, steam_appid: int) -> dict[str, Any]:
        try:
            payload = await self._api_get(
                "/game/game_introduction/",
                {"steam_appid": steam_appid, "return_json": 1},
            )
        except ParseException as e:
            logger.debug(f"[小黑盒] 游戏简介获取失败: {e}")
            return {}
        result = payload.get("result")
        return result if isinstance(result, dict) else {}

    @staticmethod
    def _build_send_groups(contents: list[MediaContent]) -> list[SendGroup]:
        """视频从合并转发里拆出来单独发送"""
        send_groups: list[SendGroup] = []
        primary = [cont for cont in contents if not isinstance(cont, VideoContent)]
        if primary:
            send_groups.append(SendGroup(contents=primary))
        for cont in contents:
            if isinstance(cont, VideoContent):
                send_groups.append(SendGroup(contents=[cont], force_merge=False))
        return send_groups

    def _pick_steam_appid(
        self, game: dict[str, Any], fallback_appid: str
    ) -> int | None:
        value = game.get("steam_appid") or fallback_appid
        try:
            return int(str(value).strip())
        except ValueError:
            return None

    def _build_game_title(self, game: dict[str, Any]) -> str:
        name = str(game.get("name") or "").strip()
        name_en = str(game.get("name_en") or "").strip()
        if name and name_en:
            return f"{name}（{name_en}）"
        return name or name_en or "小黑盒游戏详情"

    def _build_game_desc(self, game: dict[str, Any], intro: dict[str, Any]) -> str:
        lines: list[str] = []
        # 详情接口的简介是纯文本摘要, 比 Steam 图文页剥掉标签后更可读
        intro_text = self._format_game_intro_text(
            str(game.get("about_the_game") or intro.get("about_the_game") or "")
        )
        if intro_text:
            lines.append(intro_text)

        tags = self._build_game_tags(game)
        if tags:
            lines.append(f"类型：{tags}")

        score = str(game.get("score") or "").strip()
        comment_stats = game.get("comment_stats")
        score_comment = (
            comment_stats.get("score_comment")
            if isinstance(comment_stats, dict)
            else None
        )
        if score:
            if isinstance(score_comment, int) and score_comment > 0:
                lines.append(
                    f"小黑盒评分：{score}（{self._format_people_count(score_comment)}）"
                )
            else:
                lines.append(f"小黑盒评分：{score}")

        release_date = self._format_cn_ymd_to_dotted(
            str(intro.get("release_date") or "")
        )
        if release_date:
            lines.append(f"发布时间：{release_date}")

        developer = self._extract_company_text(intro.get("developers"))
        if developer:
            lines.append(f"开发商：{developer}")
        publisher = self._extract_company_text(intro.get("publishers"))
        if publisher:
            lines.append(f"发行商：{publisher}")

        if isinstance(game.get("price"), dict):
            price_data = game["price"]
            price = str(
                price_data.get("initial") or price_data.get("current") or ""
            ).strip()
            if price:
                lines.append(f"价格：¥ {price.replace('¥', '').strip()}")
            lowest_price = str(price_data.get("lowest_price") or "").strip()
            if lowest_price:
                lines.append(f"史低价格：¥ {lowest_price.replace('¥', '').strip()}")

        if isinstance(game.get("heybox_price"), dict):
            cost_coin = game["heybox_price"].get("cost_coin")
            yuan = self._format_yuan_from_coin(cost_coin)
            if yuan:
                lines.append(f"当前价格：¥ {yuan}")

        return "\n\n".join(line for line in lines if line).strip()

    @staticmethod
    def _build_game_tags(game: dict[str, Any]) -> str:
        """common_tags 里的平台特性 (中文/单人...) 和游戏标签"""
        features: list[str] = []
        tags: list[str] = []
        for tag in game.get("common_tags") or []:
            if not isinstance(tag, dict):
                continue
            if tag.get("type") == "steam_aggre":
                for item in tag.get("desc_list") or []:
                    if cleaned := re.sub(r"[^一-鿿A-Za-z0-9]+", "", str(item)):
                        features.append(cleaned)
            elif tag.get("type") == "simple_tag" and tag.get("desc"):
                tags.append(str(tag["desc"]).strip())

        parts: list[str] = []
        if features:
            parts.append(f"[ {' '.join(features)} ]")
        if tags:
            parts.append(f"[ {' '.join(tags)} ]")
        return " ".join(parts)

    @staticmethod
    def _iter_screenshots(game: dict[str, Any]):
        for item in game.get("screenshots") or []:
            if isinstance(item, dict) and item.get("url"):
                yield item

    def _extract_game_images(self, game: dict[str, Any]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for item in self._iter_screenshots(game):
            if item.get("type") == "movie":
                continue
            url = str(item["url"]).strip()
            key = url.split("?", 1)[0]
            if url.startswith("http") and key not in seen:
                seen.add(key)
                result.append(url)
        if not result and str(game.get("image") or "").startswith("http"):
            result.append(str(game["image"]))
        return result

    def _extract_game_videos(
        self, game: dict[str, Any]
    ) -> list[tuple[str, str | None]]:
        mode = str(self.mycfg.video_send_mode or "first").lower()
        if mode == "none":
            return []

        results: list[tuple[str, str | None]] = []
        for item in self._iter_screenshots(game):
            if item.get("type") != "movie":
                continue
            cover = str(item.get("thumbnail") or "").strip() or None
            results.append((str(item["url"]).strip(), cover))
            if mode != "all":
                break
        return results

    @staticmethod
    def _extract_company_text(items: Any) -> str:
        if not isinstance(items, list):
            return ""
        values: list[str] = []
        for item in items:
            if (
                isinstance(item, dict)
                and isinstance(item.get("value"), str)
                and item.get("value")
            ):
                values.append(item["value"])
        return ",".join(values)

    @staticmethod
    def _format_people_count(count: int) -> str:
        if count >= 10000:
            return f"{count / 10000:.1f} 万人评价"
        return f"{count} 人评价"

    @staticmethod
    def _format_yuan_from_coin(coin: Any) -> str:
        try:
            value = int(coin) / 1000.0
        except (TypeError, ValueError):
            return ""
        if abs(value - round(value)) < 1e-9:
            return str(int(round(value)))
        return f"{value:.2f}"

    def _format_game_intro_text(self, text: str) -> str:
        if not text:
            return ""
        stripped = self._strip_tags(text)
        stripped = stripped.replace("　", " ").replace("\xa0", " ")
        stripped = re.sub(r"\n{3,}", "\n\n", stripped).strip()
        return stripped

    @staticmethod
    def _strip_tags(text: str) -> str:
        if not text:
            return ""
        value = re.sub(r"(?is)<script[^>]*>.*?</script>", "", text)
        value = re.sub(r"(?is)<style[^>]*>.*?</style>", "", value)
        value = re.sub(r"(?is)<video[^>]*>.*?</video>", "", value)
        value = re.sub(r"(?is)<img[^>]*>", "", value)
        value = re.sub(r"(?i)</p\s*>", "\n\n", value)
        value = re.sub(r"(?i)<p[^>]*>", "", value)
        value = re.sub(r"(?i)</div\s*>", "\n", value)
        value = re.sub(r"(?i)<div[^>]*>", "", value)
        value = re.sub(r"(?i)<li[^>]*>", "\n・", value)
        value = re.sub(r"(?i)</li\s*>", "\n", value)
        value = re.sub(r"(?i)</(ul|ol)\s*>", "\n", value)
        value = re.sub(r"(?i)</h[1-6]\s*>", "\n", value)
        value = re.sub(r"(?i)<h[1-6][^>]*>", "\n", value)
        value = re.sub(r"(?i)<br\s*/?>", "\n", value)
        value = re.sub(r"<[^>]+>", "", value)
        value = html.unescape(value)
        value = value.replace("\r\n", "\n").replace("\r", "\n")
        value = re.sub(r"\n{3,}", "\n\n", value).strip()
        return value

    @staticmethod
    def _format_cn_ymd_to_dotted(text: str) -> str:
        if not text:
            return ""
        value = html.unescape(text).strip()
        value = re.sub(r"\s+", "", value)
        matched = re.match(r"^(\d{4})年(\d{1,2})月(\d{1,2})日?$", value)
        if matched:
            year, month, day = (
                matched.group(1),
                int(matched.group(2)),
                int(matched.group(3)),
            )
            return f"{year}.{month}.{day}"
        matched = re.match(r"^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})$", value)
        if matched:
            year, month, day = (
                matched.group(1),
                int(matched.group(2)),
                int(matched.group(3)),
            )
            return f"{year}.{month}.{day}"
        return text.strip()

    # ---------------- 帖子内容提取 ----------------

    def _build_author(self, link: dict[str, Any]):
        user = link.get("user") or {}
        if not isinstance(user, dict):
            return None
        name = self._clean_text(str(user.get("username") or user.get("nickname") or ""))
        if not name:
            return None
        avatar = str(user.get("avatar") or "") or None
        desc = self._clean_text(str(link.get("description") or "")) or None
        return self.create_author(name=name, avatar_url=avatar, description=desc)

    def _build_video_content(self, link: dict[str, Any]):
        if not link.get("has_video"):
            return None
        video_url = str(link.get("video_url") or "").strip()
        if not video_url:
            return None
        return self._build_video_content_from_url(video_url, None)

    def _build_video_content_from_url(
        self, video_url: str, cover_url: str | None = None
    ):
        path = (urlparse(video_url).path or "").lower()
        if path.endswith(".m3u8"):
            task = self.downloader.ytdlp_download_video_relaxed(
                video_url, headers=self.headers, proxy=self.proxy
            )
            return self.create_video_content_by_task(
                task, cover_url, headers=self.headers
            )
        return self.create_video_content(video_url, cover_url, headers=self.headers)

    def _parse_body_text_and_images(
        self, link: dict[str, Any]
    ) -> tuple[str, list[str]]:
        raw_text = link.get("text")
        if not isinstance(raw_text, str) or not raw_text.strip():
            return "", []
        try:
            blocks = json.loads(raw_text)
        except json.JSONDecodeError:
            return self._clean_text(raw_text), []

        if not isinstance(blocks, list):
            return self._clean_text(raw_text), []

        text_parts: list[str] = []
        image_urls: list[str] = []
        seen_images: set[str] = set()

        for block in blocks:
            if not isinstance(block, dict):
                continue

            block_type = str(block.get("type") or "")
            if block_type == "img":
                url = self._normalize_image_url(str(block.get("url") or "").strip())
                dedup_key = self._image_dedup_key(url)
                if url and dedup_key and dedup_key not in seen_images:
                    seen_images.add(dedup_key)
                    image_urls.append(url)
                continue

            html_text = str(block.get("text") or "")
            if html_text:
                cleaned = self._html_block_to_text(html_text)
                if cleaned:
                    text_parts.append(cleaned)
                for image_url in self._extract_images_from_html_block(html_text):
                    dedup_key = self._image_dedup_key(image_url)
                    if dedup_key and dedup_key not in seen_images:
                        seen_images.add(dedup_key)
                        image_urls.append(image_url)

        text = "\n\n".join(part for part in text_parts if part).strip()
        return text, image_urls

    def _extract_images_from_html_block(self, html_block: str) -> list[str]:
        urls: list[str] = []
        seen_keys: set[str] = set()
        for matched in re.finditer(
            r"data-original=\"([^\"]+)\"|src=\"([^\"]+)\"", html_block, re.I
        ):
            candidate = matched.group(1) or matched.group(2) or ""
            normalized = self._normalize_image_url(candidate)
            dedup_key = self._image_dedup_key(normalized)
            if normalized and dedup_key and dedup_key not in seen_keys:
                seen_keys.add(dedup_key)
                urls.append(normalized)
        return urls

    def _normalize_image_url(self, url: str) -> str:
        if not url:
            return ""
        url = html.unescape(url)
        if not url.startswith("http"):
            return ""
        if "/bbs/" not in url:
            return ""
        return url

    def _image_dedup_key(self, url: str) -> str:
        if not url:
            return ""
        base = url.split("?", 1)[0]
        base = base.replace("imgheybox1.max-c.com", "imgheybox.max-c.com")
        return base

    def _html_block_to_text(self, html_block: str) -> str:
        fragment = html.unescape(html_block)
        fragment = re.sub(r"<br\s*/?>", "\n", fragment, flags=re.I)
        fragment = re.sub(r"</p>\s*<p[^>]*>", "\n", fragment, flags=re.I)
        fragment = re.sub(r"<img[^>]*>", "", fragment, flags=re.I)
        fragment = re.sub(r"<[^>]+>", "", fragment)
        lines = [self._clean_text(line) for line in fragment.splitlines()]
        lines = [line for line in lines if line]
        return "\n\n".join(lines).strip()

    @staticmethod
    def _clean_text(text: str) -> str:
        text = html.unescape(text.replace("\xa0", " "))
        text = re.sub(r"[ \t\r\f\v]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    # ---------------- 签名 ----------------

    def _sign_path(self, path: str) -> dict[str, str | int]:
        now = int(time.time())
        nonce = (
            hashlib.md5((str(now) + str(random.random())).encode()).hexdigest().upper()
        )
        hkey = self._ov(path, now + 1, nonce)
        return {"hkey": hkey, "_time": now, "nonce": nonce}

    def _ov(self, path: str, ts: int, nonce: str) -> str:
        path = "/" + "/".join(p for p in path.split("/") if p) + "/"
        interleaved = self._interleave(
            [
                self._av(str(ts), -2),
                self._sv(path),
                self._sv(nonce),
            ]
        )[:20]
        md5_hex = hashlib.md5(interleaved.encode()).hexdigest()
        prefix = self._av(md5_hex[:5], -4)
        suffix = str(
            sum(self._mix_columns([ord(c) for c in md5_hex[-6:]])) % 100
        ).zfill(2)
        return prefix + suffix

    def _av(self, text: str, cut: int) -> str:
        table = self.CHAR_TABLE[:cut]
        return "".join(table[ord(c) % len(table)] for c in text)

    def _sv(self, text: str) -> str:
        return "".join(self.CHAR_TABLE[ord(c) % len(self.CHAR_TABLE)] for c in text)

    @staticmethod
    def _interleave(parts: list[str]) -> str:
        result: list[str] = []
        max_len = max(len(part) for part in parts)
        for i in range(max_len):
            for part in parts:
                if i < len(part):
                    result.append(part[i])
        return "".join(result)

    @staticmethod
    def _xtime(value: int) -> int:
        return ((value << 1) ^ 27) & 0xFF if value & 128 else value << 1

    @classmethod
    def _mul3(cls, value: int) -> int:
        return cls._xtime(value) ^ value

    @classmethod
    def _mul6(cls, value: int) -> int:
        return cls._mul3(cls._xtime(value))

    @classmethod
    def _mul12(cls, value: int) -> int:
        return cls._mul6(cls._mul3(cls._xtime(value)))

    @classmethod
    def _mul14(cls, value: int) -> int:
        return cls._mul12(value) ^ cls._mul6(value) ^ cls._mul3(value)

    @classmethod
    def _mix_columns(cls, col: list[int]) -> list[int]:
        values = list(col)
        while len(values) < 4:
            values.append(0)
        mixed = [
            cls._mul14(values[0])
            ^ cls._mul12(values[1])
            ^ cls._mul6(values[2])
            ^ cls._mul3(values[3]),
            cls._mul3(values[0])
            ^ cls._mul14(values[1])
            ^ cls._mul12(values[2])
            ^ cls._mul6(values[3]),
            cls._mul6(values[0])
            ^ cls._mul3(values[1])
            ^ cls._mul14(values[2])
            ^ cls._mul12(values[3]),
            cls._mul12(values[0])
            ^ cls._mul6(values[1])
            ^ cls._mul3(values[2])
            ^ cls._mul14(values[3]),
        ]
        if len(values) > 4:
            mixed.extend(values[4:])
        return mixed

    # ---------------- 请求 ----------------

    async def _api_get(
        self,
        path: str,
        params: dict[str, Any],
        *,
        cookies: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """带公共参数和 hkey 签名请求小黑盒接口, 非 ok 状态统一抛 ParseException"""
        query = {**self.WEB_PARAMS, **self._sign_path(path), **params}
        payload = await self._request_json(
            "GET", self.API + path, params=query, cookies=cookies
        )
        status = payload.get("status")
        if status == "ok":
            return payload
        if status == "show_captcha":
            raise ParseException(self.CAPTCHA_TIP)
        raise ParseException(
            f"小黑盒接口返回 {status}: {payload.get('msg') or '无详细信息'}"
        )

    async def _request_json(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        cookies: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        def do_request():
            return curl_requests.request(
                method,
                url,
                params=params,
                json=json,
                cookies=cookies,
                headers=self.api_headers,
                impersonate="chrome131",
                proxies={"https": self.proxy, "http": self.proxy}
                if self.proxy
                else None,
                timeout=self.cfg.common_timeout,
            )

        try:
            response = await asyncio.to_thread(do_request)
        except curl_requests.RequestsError as exc:
            raise ParseException(f"小黑盒请求失败: {exc}") from exc
        try:
            payload = response.json()
        except Exception as exc:
            raise ParseException(
                f"小黑盒接口返回非 JSON (HTTP {response.status_code}): {url}"
            ) from exc
        if not isinstance(payload, dict):
            raise ParseException(f"小黑盒接口返回格式异常: {url}")
        return payload
