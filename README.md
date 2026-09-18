
<div align="center">

![:name](https://count.getloli.com/@astrbot_plugin_parser?name=astrbot_plugin_parser&theme=minecraft&padding=6&offset=0&align=top&scale=1&pixelated=1&darkmode=auto)

# astrbot_plugin_parser

_✨ 链接解析器 ✨_  

[![License](https://img.shields.io/badge/License-MIT-green.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)
[![AstrBot](https://img.shields.io/badge/AstrBot-4.5.1%2B-orange.svg)](https://github.com/AstrBotDevs/AstrBot)
[![GitHub](https://img.shields.io/badge/Fork-yun474-blue)](https://github.com/yun474/astrbot_plugin_parser_comment)
[![Upstream](https://img.shields.io/badge/上游-Zhalslar-lightgrey)](https://github.com/Zhalslar/astrbot_plugin_parser)

</div>

## 📖 介绍

当前支持的平台和类型：

| 平台    | 触发的消息形态                    | 视频 | 图集 | 音频 |
| ------- | --------------------------------- | ---- | ---- | ---- |
| B 站    | av 号/BV 号/链接/短链/卡片/小程序 | ✅​  | ✅​  | ✅​  |
| 抖音    | 链接(分享链接，兼容电脑端链接)    | ✅​  | ✅​  | ❌️  |
| 微博    | 链接(博文，视频，show, 文章)      | ✅​  | ✅​  | ❌️  |
| 小红书  | 链接(含短链)/卡片                 | ✅​  | ✅​  | ❌️  |
| 小黑盒  | 链接/卡片                         | ✅​  | ✅​  | ❌️  |
| 知乎    | 链接/卡片                         | ✅​  | ✅​  | ❌️  |
| 快手    | 链接(包含标准链接和短链)          | ✅​  | ✅​  | ❌️  |
| acfun   | 链接                              | ✅​  | ❌️  | ❌️  |
| youtube | 链接(含短链)                      | ✅​  | ❌️  | ✅​  |
| tiktok  | 链接                              | ✅​  | ❌️  | ❌️  |
| instagram | 链接                            | ✅​  | ✅​  | ❌️  |
| twitter | 链接                              | ✅​  | ✅​  | ❌️  |
| Iwara   | 视频/图片链接                     | ✅​  | ✅​  | ❌️  |
| 微信视频号 | 视频号分享链接（需元宝 Cookie） | ✅​  | ❌️  | ❌️  |

本插件目标：凡是链接皆可解析！尽请期待更新（如果可以,请提交PR）

---

## 🎨 效果图

插件默认启用 PIL 实现的通用媒体卡片渲染，效果图如下

<div align="center">

<img src="https://raw.githubusercontent.com/fllesser/nonebot-plugin-parser/refs/heads/resources/resources/renderdamine/video.png" width="160" />
<img src="https://raw.githubusercontent.com/fllesser/nonebot-plugin-parser/refs/heads/resources/resources/renderdamine/9_pic.png" width="160" />
<img src="https://raw.githubusercontent.com/fllesser/nonebot-plugin-parser/refs/heads/resources/resources/renderdamine/4_pic.png" width="160" />
<img src="https://raw.githubusercontent.com/fllesser/nonebot-plugin-parser/refs/heads/resources/resources/renderdamine/repost_video.png" width="160" />
<img src="https://raw.githubusercontent.com/fllesser/nonebot-plugin-parser/refs/heads/resources/resources/renderdamine/repost_2_pic.png" width="160" />

</div>

---

## 💿 安装

推荐从当前 fork 安装：<https://github.com/yun474/astrbot_plugin_parser_comment>

也可以在astrbot的插件市场搜索astrbot_plugin_parser，点击安装，等待完成即可

## ⚙️ 配置

请在astrbot的插件配置面板查看并修改

### 官 Bot 适配模式

使用 QQ 官方机器人时可开启 `官 Bot 适配模式`。开关开启后直接生效，不依赖适配器上报的平台名称：

- 不再使用 QQ 贴表情仲裁，命中链接后先发送“云云帮你发视频，稍等一下哦...”提示。
- 完全禁用合并转发节点，即使达到转发阈值或解析器要求强制合并，也不会构造 `Nodes`。
- 视频、图片等媒体仍使用标准消息组件发送，由 AstrBot QQ 官方适配器按官方富媒体接口上传并发送。

腾讯富媒体接口（文档 2026-07 版）对视频的限制：mp4 软限制 30 MB（超过会降级成文件卡片，不再是可播放视频），硬限制 200 MB；被动回复 5 分钟内有效、每条消息最多回复 5 次；文件上传还有每日总量配额。AstrBot 4.27.3 起大文件走分片上传，更早版本只能 base64 直传，超过 10 MB 会 413。为此官 Bot 模式默认采用“小文件优先”策略：

- `官 Bot 视频体积上限`：默认 30 MB，与“资源最大大小”取较小值；检测到 AstrBot 不支持分片上传时自动按 10 MB 处理。超过上限的视频会提示“此项媒体超过大小限制”而不是上传一个文件卡片；想要文件卡片可以把上限调到 200。
- `官 Bot 模式强制最低清晰度`：默认开启，无视各解析器的清晰度配置，抖音 540P、B站 360P、Iwara 360P。抖音会在此基础上继续按体积上限降档。
- `图集拼图阈值`：官 Bot 模式和 LLM 工具模式下，一条结果里的图片超过这个数量（默认 4）时会按原比例拼成一张长图发送，只占一条消息；设为 0 关闭。

### LLM 工具模式

开启 `LLM 工具模式` 后，插件只提供 `parse_media_link` 工具，不再监听普通消息主动解析：

- 普通消息中的链接不会触发解析、贴表情仲裁或“开始解析”提示。
- LLM 调用工具并传入完整链接后才开始解析。
- 视频、图片等媒体使用标准消息组件直接发送到当前会话，不包装进 LLM 工具结果或合并转发节点。
- 工具向 LLM 返回 JSON 格式的成功状态、平台、标题及失败原因。

### B站视频海报

B站视频解析时会先渲染一张海报再发视频：封面（带时长角标）、标题、UP 主头像和昵称、发布时间、分区、播放 / 弹幕 / 点赞 / 投币 / 收藏 / 转发 / 评论七项数据、简介、“热门收录”之类的荣誉标签，登录后还会带上 AI 总结。多 P 视频会标出当前分 P。

- 海报单独作为一条消息先发出来，视频紧随其后，不会被塞进合并转发里。
- B站解析器的 `渲染视频海报` 开关可以关掉它；关掉后沿用原版的默认卡片逻辑。
- 海报渲染失败时自动退回默认 PIL 卡片，不影响视频发送。

### B站评论区

评论区图片按 B站网页的样子渲染：头像、昵称（大会员粉色）、等级徽章、`UP` / `置顶` 标记、“UP主觉得很赞”、发布时间、IP 属地、点赞数、评论配图和 B站表情（`[doge]` 之类会显示成图片），每条热评下面带楼中楼（接口返回的前几条回复，通常 3 条，和网页一致），更多回复显示“共 N 条回复”。

- `评论区显示楼中楼` 可以关掉楼中楼，只留一级评论。
- 广告文本过滤同样作用于楼中楼。

B站访客态评论接口目前通常只返回 3 条，并会把分页标记为结束。若要按 `comment_limit` 获取更多评论，请填写有效的 B站 Cookie，或使用 `登录B站` 命令扫码登录。扫码凭证会自动用于评论接口。

### HTML 渲染引擎

海报和评论区图都是 HTML 模板渲染，由全局配置 `HTML 渲染引擎` 决定用什么截图：

- `自动`（默认）：优先本地 Playwright；没装或起不来时退回 AstrBot 自带的网络文转图接口（分辨率低一些，但不需要浏览器）。
- `本地 Playwright`：只用本地浏览器。插件依赖里带了 `playwright` 包，第一次渲染时会自动执行 `playwright install chromium`（约 150 MB，只需一次）。Linux / Docker 缺系统库时请手动执行 `playwright install --with-deps chromium`；想显示 Emoji 的话装一下 `fonts-noto-color-emoji`。
- `AstrBot 网络渲染`：只用网络接口。

### 分享卡片

QQ 里直接分享的 B站小程序卡片、PC 端的结构化分享卡片都能触发解析，卡片前面带 @ 或文字也没关系。LLM 工具模式下 LLM 看不见卡片内容，所以卡片会直接唤醒解析（不贴表情、不发提示，媒体直接发到当前会话）。

### 抖音清晰度

抖音解析器新增 `视频清晰度` 选项（1080P / 720P / 540P，默认 1080P）。所选档位不存在，或文件超过 `资源最大大小` 时，会自动降到更低一档；直链下载失败会自动轮换 CDN 线路重试。

同一 ttwid 短时间内请求过多时，抖音会对 `www.iesdouyin.com` 返回人机验证页，插件会改用 `m.douyin.com` 分享页兜底；若两边都被拦，会在日志和会话里提示“抖音触发了人机验证”，请降低解析频率稍后再试。

### 小黑盒

- 游戏详情页改为直接请求小黑盒 Web 接口（网站已改为纯前端渲染，旧的页面数据提取方式已失效），PC / 主机 / 手游均可解析。
- 帖子接口 `bbs/app/link/tree` 目前对未验证过的设备会要求完成腾讯滑块验证，插件无法代替完成。遇到提示“小黑盒触发了人机验证”时，请在浏览器登录小黑盒并完成验证后，把该浏览器的 Cookie 粘贴到小黑盒解析器的 `Cookies` 配置中。

### 失败提示

解析或下载失败时，会话里的提示会带上具体原因（如 `此项媒体下载失败: HTTP 403`、`此项媒体超过时长限制`、`解析失败: 视频不可用`），日志中也会有同样的一行说明。不想在会话里看到这些提示，可关闭 `提示下载失败项`。

## 🎉 指令

|   指令   |         权限          |        说明        |
| :------: | :-------------------: |  :---------------: |
| 开启解析 |      ADMIN            |     开启当前会话的解析功能      |
| 关闭解析 |      ADMIN            |    关闭当前会话的解析功能      |
|  blogin  |      ADMIN           |   扫码获取 B 站凭证 |

---

## 🧠 插件工作流程

当插件运行后，每一条消息的处理流程如下：

1. **消息接收**  
   监听所有消息事件，获取消息链与原始文本内容  
   - 支持普通文本、链接、卡片（Json 组件）

2. **基础过滤**  
   - 跳过已被禁用的会话  
   - 跳过空消息  
   - 若消息首段为 `@` 且目标不是本 Bot，则不解析

3. **链接提取与匹配**  
   - 若消息链里有分享卡片（小程序 / 结构化消息），先从 Json 中提取 URL；LLM 工具模式下只有卡片会唤醒解析  
   - 使用「关键词 + 正则」双重匹配，定位对应解析器  
   - 未匹配到解析规则则直接退出

4. **仲裁判定（Emoji Like Arbiter）**  
   - 仅在 `aiocqhttp` 平台生效  
   - 通过固定表情进行 Bot 间仲裁  
   - 未胜出的 Bot 自动放弃解析
   - 若开启官 Bot 适配模式，则跳过贴表情仲裁并发送文字提示
   - 若开启 LLM 工具模式，则普通消息入口直接退出，不执行仲裁或提示

5. **防抖判定（Link Debouncer）**  
   - 对同一会话内的相同链接进行时间窗口限制  
   - 命中防抖规则则跳过解析，避免短时间重复处理

6. **内容解析**  
   - 调用对应平台解析器获取媒体信息  
   - 生成统一的 `ParseResult` 数据结构

7. **媒体下载与消息构建**  
   - 下载视频 / 图片 / 音频 / 文件  
   - 根据配置决定音频发送方式  
   - 可按配置提示下载失败项

8. **卡片渲染（可选）**  
   - 在非简洁模式或无直传媒体时生成媒体卡片  
   - 使用 PIL 渲染并缓存图片  
   - B站视频改用 HTML 模板渲染海报（Playwright / AstrBot 网络渲染），评论区图同理

9. **消息合并与发送**  
    - 当消息段数量超过阈值时自动合并为转发消息  
    - 官 Bot 适配模式下不合并为转发节点，媒体交由 QQ 官方富媒体接口发送
    - 最终将结果发送到对应会话

---

## 🧩 扩展

插件支持自定义解析器，通过继承 `BaseParser` 类并实现 `platform`, `handle` 即可。

示例解析器请看 [示例解析器](https://github.com/yun474/astrbot_plugin_parser_comment/blob/main/core/parsers/example.py)

---

## 🎉 致谢

本项目核心代码来自[nonebot-plugin-parser](https://github.com/fllesser/nonebot-plugin-parser)，请前往原仓库给作者点个Star!
