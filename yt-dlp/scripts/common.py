"""公共工具：yt-dlp 定位、临时目录、语言判定、依赖检查。

本文件是全套件的共享底座，所有路径都从环境动态推导，不依赖任何一台
特定电脑的目录结构，保证这套 skill 复制到任何机器、任何 agent 下都能用。
"""

import importlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

# ── 工作区 ───────────────────────────────────────────────────────────
# 所有中间产物都放在这里，处理完由 `yt rm` 清掉。
# 可用环境变量 YT_TMP_ROOT 覆盖；默认按平台放到用户缓存目录，不污染
# 用户文档区，也不假设存在 ~/AI-Space 之类的个人目录。
def _default_tmp_root() -> Path:
    env = os.environ.get("YT_TMP_ROOT")
    if env:
        return Path(env).expanduser()
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "yt-transcript"
    return Path.home() / ".cache" / "yt-transcript"


TMP_ROOT = _default_tmp_root()

# 中文语言代码（这些不需要翻译）
ZH_CODES = {"zh", "zh-Hans", "zh-Hant", "zh-CN", "zh-TW", "zh-HK", "zh-SG", "yue"}

# 人工字幕优先取的语言顺序：中文优先（省掉翻译），其次英文
PREFERRED_MANUAL = ["zh-Hans", "zh-CN", "zh", "zh-Hant", "zh-TW", "zh-HK", "en", "en-US", "en-GB"]

# yt-dlp 最低建议版本：再旧的可能遇到 403 / format not available
_MIN_VERSION = (2024, 11, 0)


def _parse_version(text: str):
    """把 yt-dlp 版本串解析成数字元组，解析失败返回 None。"""
    if not text:
        return None
    m = re.search(r"(\d{4})\.(\d{1,2})\.(\d{1,2})", text)
    if not m:
        return None
    try:
        return tuple(int(part) for part in m.groups())
    except ValueError:
        return None


def _ytdlp_candidates():
    """按优先级给出 yt-dlp 可执行文件的候选路径。"""
    seen = []
    # 1. 用户显式指定的路径
    explicit = os.environ.get("YTDLP_PATH")
    if explicit:
        seen.append(Path(explicit).expanduser())
    # 2. PATH 里的 yt-dlp（最通用的发现方式）
    which = shutil.which("yt-dlp")
    if which:
        seen.append(Path(which))
    # 3. 常见安装位置（macOS Homebrew / Linux / Windows）
    for p in (
        Path("/opt/homebrew/bin/yt-dlp"),
        Path("/usr/local/bin/yt-dlp"),
        Path("/usr/bin/yt-dlp"),
        Path.home() / ".local" / "bin" / "yt-dlp",
    ):
        if p not in seen:
            seen.append(p)
    return seen


def find_ytdlp():
    """定位可用的 yt-dlp，并校验版本。

    分发版改为「PATH 优先，多候选兜底」：任何正常安装都能被发现，
    版本过旧时直接报错并给出升级命令，而不是让调用方在几分钟后收到一个
    莫名其妙的 403。
    """
    errors = []
    for path in _ytdlp_candidates():
        if not path.exists():
            continue
        try:
            proc = subprocess.run(
                [str(path), "--version"], capture_output=True, text=True, timeout=20
            )
        except Exception as exc:
            errors.append(f"  {path}: {exc}")
            continue
        if proc.returncode != 0:
            errors.append(f"  {path}: 执行失败")
            continue
        ver = proc.stdout.strip()
        parsed = _parse_version(ver)
        if parsed and parsed < _MIN_VERSION:
            die(
                f"找到的 yt-dlp 版本 {ver}（{path}）过旧，需要 "
                f"{'.'.join(map(str, _MIN_VERSION))} 或更新。\n"
                f"升级：python3 -m pip install -U yt-dlp  或  brew upgrade yt-dlp"
            )
        if ver:
            return str(path), ver
        errors.append(f"  {path}: 无法读取版本")
    die(
        "找不到可用的 yt-dlp。\n"
        "安装命令（任选其一）：\n"
        "  macOS + Homebrew：  brew install yt-dlp\n"
        "  其它 / pip 用户：    python3 -m pip install -U yt-dlp\n"
        "装好后重跑一次；也可用环境变量 YTDLP_PATH 指向 yt-dlp 可执行文件。"
    )


def check_ytdlp_module():
    """检查 Python 是否能在当前解释器里 import yt_dlp（B站脚本需要）。

    返回 (ok, message)。不抛异常，供 `yt check` 汇总输出。
    """
    try:
        importlib.import_module("yt_dlp")
        return True, "已安装"
    except Exception:
        return False, (
            "未安装 yt-dlp 的 Python 库（B站子技能 bili 需要 import yt_dlp）。\n"
            "安装：python3 -m pip install -U yt-dlp"
        )


def run_ytdlp(args, timeout=300, check=True):
    """调 yt-dlp，默认带上浏览器 cookies（YouTube 现在基本都要）。"""
    ytdlp, _ = find_ytdlp()
    cookies = os.environ.get("YT_COOKIES_FROM", "chrome")
    cmd = [ytdlp]
    if cookies and cookies != "none":
        cmd += ["--cookies-from-browser", cookies]
    cmd += args

    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if check and proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()
        hint = ""
        blob = "\n".join(tail[-6:])
        if "Sign in to confirm" in blob or "cookies" in blob.lower():
            hint = (
                "\n提示：cookies 失效。先在 Chrome 里登录 YouTube；"
                "或设 YT_COOKIES_FROM=safari 换浏览器。"
            )
        elif "429" in blob:
            hint = "\n提示：被限流，等几分钟再试。"
        die("yt-dlp 失败：\n" + blob + hint)
    return proc


def video_id_of(url):
    """从 URL 抽视频 ID；抽不出就用 URL 的哈希，保证临时目录名稳定。

    也接受已经是裸 ID 的输入（`yt rm <id>` 会这么传）。
    """
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", url):
        return url
    if re.fullmatch(r"BV[0-9A-Za-z]{10}(?:_p\d+)?", url):
        return url
    bili = re.search(r"(BV[0-9A-Za-z]{10})", url)
    if bili:
        page = re.search(r"[?&]p=(\d+)", url)
        return bili.group(1) + (f"_p{page.group(1)}" if page else "")
    m = re.search(r"(?:v=|youtu\.be/|/shorts/|/embed/)([A-Za-z0-9_-]{11})", url)
    if m:
        return m.group(1)
    import hashlib
    return "url-" + hashlib.sha1(url.encode()).hexdigest()[:10]


def workdir(vid, create=True):
    d = TMP_ROOT / vid
    if create:
        d.mkdir(parents=True, exist_ok=True)
    return d


def is_chinese(lang):
    """判断语言代码是否中文。zh-Hans / zh-CN / zh 之类全算。"""
    if not lang:
        return False
    base = lang.split("-")[0].lower()
    return base in ("zh", "yue") or lang in ZH_CODES


def die(msg, code=1):
    print(msg, file=sys.stderr)
    sys.exit(code)


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def save_json(path, data):
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
