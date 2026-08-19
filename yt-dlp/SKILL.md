---
name: yt-dlp
description: 视频平台内容处理套件。整理 YouTube 或B站视频文稿（优先找第一方现成文稿，否则下最佳字幕、清洗、翻译并导出 DOCX）时使用。B站支持复用 Arc/Chrome 登录态提取人工 CC 与平台 AI 字幕；无字幕时停止，不自动 ASR。开工前必须先运行 yt check 检查依赖。
---

# yt-dlp 套件

围绕 `yt-dlp` 搭的一组视频处理能力，按平台和用途拆成独立技能，全部封装在本文件夹内。

本套件以一个自包含目录统一组织共享脚本和平台子技能。支持子技能发现的 agent 可直接匹配
`youtube-transcript` 或 `bilibili-transcript`；其它 agent 也可按本文件的统一入口调用，
不依赖某个单一 agent 平台的工作流。所有脚本均为纯标准库 Python 实现，无需虚拟环境。

## 依赖与环境检查（每次开工第一步）

本套件不假设用户电脑上预装了任何东西。**接到任务后、执行任何下载动作之前，
先运行 `yt check`**，确认依赖是否齐全；缺什么它会直接打印安装命令，agent 应
按输出主动安装或提示用户安装。

| 依赖 | 用途 | 检查方式 | 安装命令（缺的时候） |
|---|---|---|---|
| Python 3.9+ | 所有脚本运行 | `yt check` | 装系统 Python 或用 Homebrew / 官网安装 |
| `yt-dlp` 可执行文件 | YouTube / B站下载字幕 | `yt check` | macOS：`brew install yt-dlp`；通用：`python3 -m pip install -U yt-dlp` |
| `yt_dlp` Python 库 | 仅 B站子技能（`bili` 脚本需要 import） | `yt check` | `python3 -m pip install -U yt-dlp`（pip 安装会同时提供命令与库） |
| 浏览器登录态 | YouTube 需要 Chrome/Safari 已登录；B站需要 Arc/Chrome 已登录 | 运行时脚本会报错并提示 | 在对应浏览器里登录平台后重试 |

可选环境变量（不设也能跑）：

| 变量 | 作用 |
|---|---|
| `YT_TMP_ROOT` | 临时工作区根目录，默认 `~/.cache/yt-transcript/`（Windows 为 `%LOCALAPPDATA%\yt-transcript`） |
| `YT_OUTPUT_DIR` | DOCX 成稿输出目录，默认当前工作目录下 `yt-transcript-output/` |
| `YTDLP_PATH` | 显式指定 yt-dlp 可执行文件路径 |
| `YT_COOKIES_FROM` | YouTube cookies 浏览器，默认 `chrome`，可设 `safari` / `none` |
| `BILI_BROWSER` | B站 cookies 浏览器，默认 `arc`，可设 `chrome`（非 macOS 必须设 chrome） |
| `BILI_BROWSER_PROFILE` | 浏览器 profile 路径或名称，不设则用默认 profile |

## 子技能索引

| 子技能 | 能做什么 | 状态 |
|---|---|---|
| [youtube-transcript](skills/youtube-transcript/SKILL.md) | YouTube 视频出文稿：下最优字幕 → 清洗 → 非中文翻译 → 导出 DOCX | ✅ 可用 |
| [bilibili-transcript](skills/bilibili-transcript/SKILL.md) | B站：内存复用浏览器登录态，取人工/AI字幕，查优质现成稿，导出 DOCX | ✅ 可用 |
| media-download | 下载视频/音频本体 | 🚧 未建 |

## 统一入口

进入本目录后执行 `./yt <子命令>`；也可用绝对路径或 `python3 <本目录>/yt <子命令>`。
下文均以 `yt` 指代该入口。

| 命令 | 用途 |
|---|---|
| `yt check` | **检查依赖与运行环境**（开工第一步） |
| `yt doc <url>` | YouTube：探测 → 下最优字幕 → 洗成文稿 |
| `yt subs <url>` | 只下字幕（加 `--json` 输出结构化元信息） |
| `yt clean <file>` | 只清洗一个已有的 vtt/srt |
| `yt bili probe <url>` | B站只探测登录态、字幕轨和现成文稿线索 |
| `yt bili subs <url>` | B站下载最佳字幕 |
| `yt bili doc <url>` | B站下载最佳字幕并生成带时间戳底稿 |
| `yt rm <id>` / `yt rm --all` | 把临时工作目录移入废纸篓（可恢复） |

## 共用约定

- **临时工作区**：`YT_TMP_ROOT` 或默认 `~/.cache/yt-transcript/<video_id>/`。成果写入目的地后，务必 `yt rm <video_id>` 清掉。
- **成果落点**：普通 DOCX 文件。默认输出到 `YT_OUTPUT_DIR` 或当前工作目录下 `yt-transcript-output/`；由 `scripts/to_docx.py` 生成，**不写思源、不依赖任何笔记软件**。
- **cookies**：YouTube 默认从 Chrome 读，换浏览器设 `YT_COOKIES_FROM=safari`。B站默认从 Arc 读且只驻留内存，改用 Chrome 设 `BILI_BROWSER=chrome`；非 macOS 上 Arc 不可用，必须设 `BILI_BROWSER=chrome`。
- **yt-dlp 定位**：`scripts/common.py` 按「`YTDLP_PATH` → PATH → 常见安装路径」的顺序查找，并校验版本。过旧会直接报错并给升级命令。

## 扩展本套件时

新增子技能 = 在 `skills/` 下建目录写 `SKILL.md`，在上面的索引表里加一行。
公共能力（yt-dlp 定位、临时目录、语言判定、Markdown→DOCX 转换）在 `scripts/` 下，复用它，别各写一套。

保持现有目录结构：

```
yt-dlp/
├── skills/<技能名>/SKILL.md      # 各平台子技能
├── scripts/                     # 共享脚本（common/fetch/clean/to_docx）
└── yt                           # 跨平台统一命令入口
```
