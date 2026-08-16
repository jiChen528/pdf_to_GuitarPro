# PDF 吉他谱 → Guitar Pro (.gp) 转换器

把 PDF 吉他谱（六线谱 tab）自动转换成 Guitar Pro 文件，Guitar Pro 8 可直接打开编辑。

> ⚠️ **目前仅支持「文字版 PDF」**：Guitar Pro、MuseScore 等软件导出的矢量谱子。
> 扫描件 / 图片型 PDF（无文字层）暂不支持，拖入后程序会提示并拒绝转换。

# 我想说的一些话：

我承认现在这个程序只处于一个很粗糙的版本，还没有办法识别推弦、颤音和装饰音
如果节奏型标在了五线谱上也无法正常识别，甚至纯六线谱的节奏偶尔也会有一点问题
我做这个东西的初衷就是可以让吉他手更加方便一点以后有机会的会会改进的

## 快速开始（推荐：直接使用 exe）

下载 **`PDF2GP.exe`**（单文件，双击即用，无需安装 Python）：

1. 双击运行，打开图形界面；
2. 把 PDF 谱子**拖进窗口**（或点击窗口选择文件）；
3. 转换完成后在 **PDF 所在目录**生成同名 `.gp` 文件，可一键用 Guitar Pro 打开。

也支持命令行方式（无界面）：

```bash
PDF2GP.exe 乐谱.pdf      # 在 PDF 同目录生成 乐谱.gp
```

> 系统要求：Windows 10/11，需安装 [Node.js](https://nodejs.org)（写 .gp 文件时调用）。
> 未安装 Node 时程序会给出中文提示。

## 功能特性

- **一键转换**：拖入 PDF → 同目录生成 `.gp`，自动识别调弦（失败时按标准调弦兜底）
- **节奏识别**：从符干/符尾(tick)/附点/休止符/音符椭圆还原四分、八分、十六分、附点与休止、全音符与二分音符
- **技巧识别**：
  - **连奏滑音 (sl)**：弧线上方 `sl.` → 起点标 GP「连奏滑音」，终点不动
  - **击勾弦 (h/p)**：弧线上方 `H`/`P` → 起点标「击勾弦」（Hammer-on / Pull-off）
  - **无头无尾滑音**：单音符旁的小斜线——音符前向上=滑入上行、向下=滑入下行；音符后向下=滑出下行、向上=滑出上行
  - **延音线**：同品位弧线、括号目标 `(7)`、空白目标，以及「持音留白」（全/二分音符品位继承延音目标）
- **右手闷音 (P.M.)**：谱面上方 `P.M.` 字样 + 虚线范围 → 范围内音符（含 P.M. 正下方）标 Palm Mute
- **左手闷音 (x)**：X 符号 → Dead note（tab 中显示小 x）
- **三连音**：系统下方数字 `3` + 实线括号 → tuplet 3:2
- 默认乐器：**Stratocaster Overdrive** 电吉他音色（GP8 RSE 音色库）
- 和弦指法图自动过滤，多页谱按阅读顺序拼接成一首完整的歌

## 从源码运行

```bash
pip install -r requirements.txt    # pymupdf（GUI 拖拽另需 tkinterdnd2）
npm install                        # @coderline/alphatab
python gui.py                      # 打开图形界面
python gui.py 乐谱.pdf             # 或命令行直接转换
```

高级 CLI（含实验性 OCR / LLM 引擎，默认 auto 自动选文字层直读）：

```bash
python pdf2gp.py 乐谱.pdf --open
```

> 说明：OCR / LLM 引擎为实验性功能，依赖需自行安装（见 `requirements.txt` 中的注释），
> 不在 exe 与默认流程中提供。

## 构建 exe

```bash
pip install pyinstaller tkinterdnd2
npm install
build_exe.bat        # 在根目录生成 PDF2GP.exe
```

## 工作原理（文字层引擎）

```
PDF 文字层 ──品位数字(word 坐标)──> 归属弦线 -> 按 x 聚成拍(和弦)
PDF 矢量路径 ──横线段聚类──> 弦线/系统；细高矩形──> 小节线
             ──竖线段──> 符干(四分)；符干末端短竖线 tick──> 八分/十六分
音乐字形(U+E2xx/E4xx) ──> 独立符尾/休止符/附点
         ──(alphaTab Gp7Exporter)──> 标准 .gp（GP7 格式）
         ──(后处理)──> 注入音色、三连音 Tuplet、弦序修正
```

## 已知限制

- **仅支持文字版 PDF**；手写谱 / 扫描件暂不支持
- 只支持单轨；谱头调弦识别失败时按标准调弦 EADGBE
- 识别不到节奏的拍按八分音符兜底

## 目录结构

```
gui.py                       图形界面 + 命令行入口（exe 打包入口）
pdf2gp.py                    高级 CLI（--engine text/ocr/llm，默认 auto）
gpengine/
  text_extractor.py          文字层直读 + 矢量几何 + 节奏/技巧识别
  gpwriter.py                spec → .gp（调 node 写文件器 + GP8 后处理）
  alphatab_writer.js         alphaTab Gp7Exporter 写 .gp
  pdfrender.py / tabparser.py / llm_engine.py   实验性 OCR/LLM 引擎
tests/                       端到端测试与合成样本
build_exe.bat                PyInstaller 打包脚本
PDF2GP.exe                   单文件可执行程序（根目录）
```
