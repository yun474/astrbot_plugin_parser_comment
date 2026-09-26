# 内置符号字体

本目录附带未修改的 Noto 字体，均来自 Google Fonts 官方仓库：

- [Noto Sans Math](https://github.com/google/fonts/tree/main/ofl/notosansmath)：数学符号，包括 `⩌`（U+2A4C）和 `⩊`（U+2A4A）。授权见 `OFL-NotoSansMath.txt`。
- [Noto Sans Symbols 2](https://github.com/google/fonts/tree/main/ofl/notosanssymbols2)：补充几何、箭头及其他符号。授权见 `OFL-NotoSansSymbols2.txt`。

两个字体共约 2.2 MiB，仅作后备，保留原来的中文字体。HTML 使用 CSS 字体回退，本地读取插件文件，网络渲染内联字体。Pillow 通过 `coverage.json` 为原中文字体缺失的字符选用后备字体，测量与绘制使用同一字体；Emoji 继续由 Apilmoji 处理。

`coverage.json` 是从字体 cmap 生成的字符索引，不是手写的 Unicode 范围。更新原中文字体或这两款字体后，在开发环境安装 `fonttools` 和插件依赖并运行 `python tools/build_font_coverage.py` 重新生成。插件运行不需要 `fonttools`。
