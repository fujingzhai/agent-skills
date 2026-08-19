# agent-skills

可分享、可复用的 AI agent 技能合集。每个技能都是自包含目录：自带 `SKILL.md` 说明与全部脚本，复制即用，不绑定任何单一 agent 平台。

## 目录

| 技能 | 说明 |
|---|---|
| [yt-dlp](yt-dlp/SKILL.md) | YouTube / B站视频文稿整理：探测来源 → 下载最佳字幕 → 清洗校错 → 翻译 → 导出 DOCX |

## 安装

把仓库 clone 下来后，将 `yt-dlp/` 整个目录复制到你的 agent 所识别的 skills 目录即可。
不同 agent 的 skills 目录不同，例如：

- Claude Code：`~/.claude/skills/`（或项目 `.claude/skills/`）
- 其他支持 SKILL.md 的 agent：按其文档指定的 skills 目录
- 任何 agent：只要能读到本仓库，直接按 `yt-dlp/SKILL.md` 中的路径规则调用脚本也能用

```bash
git clone https://github.com/fujingzhai/agent-skills.git
```

## 依赖

每个技能所需的系统依赖都在各自 `SKILL.md` 的「依赖与环境检查」章节声明，
并带有自检命令。以 `yt-dlp` 为例：

- Python 3.9+
- `yt-dlp` 可执行文件（YouTube / B站字幕下载）
- `yt_dlp` Python 库（仅 B站子技能需要）

安装后先运行 `yt check`，它会逐项检查并给出缺失项的安装命令。agent 应主动执行自检，
不要假设用户电脑上已装好任何工具。

## 添加新技能

在仓库根目录新建一个文件夹，例如 `my-skill/`，内含：

```text
my-skill/
├── SKILL.md      # 技能说明：用途、依赖、流程、自检命令
└── ...           # 技能需要的脚本与资源
```

提交并推送即可。保持每个技能自包含：不写死本机路径，不引用系统外散落的配置。
