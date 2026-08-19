"""探测并下载"最好的那一条"字幕。

选轨优先级（这是整个脚本的核心，别随手改）：

  1. 人工字幕，中文        → 直接可用，不必翻译
  2. 人工字幕，视频原语言  → 质量最高的外语来源
  3. 人工字幕，其它任意    → 按 PREFERRED_MANUAL 顺序
  4. 自动字幕，仅原生语言轨 → 兜底

第 4 步有一条硬规矩：**只取原生语言轨，绝不取 YouTube 的自动翻译轨**。
automatic_captions 里除原生轨外那几百种语言，全是 YouTube 拿原生轨机翻的结果，
等于在识别误差上再叠一层翻译误差——用户看到的"把 [music] 翻译进正文"就是这么来的。
翻译这一步交给 agent 做，它至少能看见上下文。
"""

import argparse
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

from common import (PREFERRED_MANUAL, die, is_chinese, run_ytdlp, save_json,
                    video_id_of, workdir)

# 只保留需要的字段，info.json 全量有好几 MB
INFO_FIELDS = ["id", "title", "channel", "uploader", "upload_date",
               "duration_string", "duration", "language", "webpage_url",
               "channel_url", "uploader_url", "description"]

# ── 描述栏里的"现成文稿"线索 ─────────────────────────────────────────
# 实测 Lex Fridman / Dwarkesh / Lenny's 三家都在描述里明写 Transcript: <url>，
# 这是找第一方文稿最省事的一条路，零额外网络开销。
HINT_WORDS = re.compile(r"transcript|文稿|逐字稿|字幕稿|show\s?notes", re.I)

# 社交、赞助、分发平台：这些链接不可能是文稿，滤掉免得刷屏。
# 必须按**主机名**比，不能拿子串比——"dropbox.com" 里含着 "x.com"，
# 而 Lenny's 的公开文稿存档恰恰就在 dropbox 上，子串匹配会把它误杀。
NOISE_HOSTS = {"x.com", "twitter.com", "instagram.com", "linkedin.com",
               "facebook.com", "tiktok.com", "youtube.com", "youtu.be",
               "apple.com", "spotify.com", "discord.gg", "discord.com",
               "patreon.com", "amazon.com", "amzn.to", "t.me", "threads.net",
               "weibo.com", "bilibili.com"}

URL_RE = re.compile(r"https?://[^\s<>()\[\]，。、]+")


def _is_noise(url):
    host = urlparse(url).netloc.lower()
    host = host[4:] if host.startswith("www.") else host
    return any(host == n or host.endswith("." + n) for n in NOISE_HOSTS)


def transcript_hints(desc):
    """从视频描述里挖"现成文稿"的候选链接。

    两种写法都要认：`Transcript: <url>` 同行（Dwarkesh、Lenny's），
    以及标记单独占一行、URL 在随后一两行（Lex）。
    """
    if not desc:
        return []

    lines = [ln.strip() for ln in desc.splitlines()]
    hits, seen = [], set()

    def add(url, label):
        url = url.rstrip(".,;)")
        if url in seen or _is_noise(url):
            return
        seen.add(url)
        hits.append({"url": url, "label": label})

    for i, line in enumerate(lines):
        if not HINT_WORDS.search(line):
            continue
        urls = URL_RE.findall(line)
        same_line = bool(urls)
        if not urls:  # 标记独占一行，URL 在下面
            for nxt in lines[i + 1:i + 3]:
                urls = URL_RE.findall(nxt)
                if urls:
                    break
        for u in urls:
            # 一行里挂多个链接时，各取各自前面那段文字当标签
            raw = line[:line.index(u)] if same_line else line
            label = re.sub(r"\s+", " ", URL_RE.sub("", raw)).strip(" *:：-—•·") or "transcript"
            add(u, label.split("　")[-1].strip(" *:：-—•·") or "transcript")

    # 兜底：URL 自身路径里就带 transcript
    for line in lines:
        for u in URL_RE.findall(line):
            if HINT_WORDS.search(u):
                add(u, "链接路径含 transcript")

    return hits


def probe(url):
    """拿视频元信息 + 两类字幕清单。"""
    proc = run_ytdlp(["--skip-download", "--dump-single-json", url], timeout=180)
    try:
        info = json.loads(proc.stdout)
    except json.JSONDecodeError:
        die("yt-dlp 返回的不是 JSON，可能是网络或 cookies 问题。")

    meta = {k: info.get(k) for k in INFO_FIELDS}
    manual = {k: v for k, v in (info.get("subtitles") or {}).items() if k != "live_chat"}
    auto = info.get("automatic_captions") or {}
    return meta, manual, auto


def pick_track(meta, manual, auto):
    """挑一条轨，返回 (kind, lang, why)。kind 为 manual / auto。"""
    native = meta.get("language")

    # 1. 人工字幕里的中文
    for lang in manual:
        if is_chinese(lang):
            return "manual", lang, "人工字幕（中文，无需翻译）"

    # 2. 人工字幕里的视频原语言
    if native and native in manual:
        return "manual", native, f"人工字幕（视频原语言 {native}）"

    # 3. 人工字幕，按偏好顺序，再不行就任取一条
    for lang in PREFERRED_MANUAL:
        if lang in manual:
            return "manual", lang, f"人工字幕（{lang}）"
    if manual:
        lang = sorted(manual)[0]
        return "manual", lang, f"人工字幕（{lang}）"

    # 4. 自动字幕，只认原生轨
    if native:
        for cand in (f"{native}-orig", native):
            if cand in auto:
                return "auto", cand, f"自动字幕（原生语言轨 {cand}，质量有限）"
    orig = [k for k in auto if k.endswith("-orig")]
    if orig:
        lang = sorted(orig)[0]
        return "auto", lang, f"自动字幕（原生语言轨 {lang}，质量有限）"

    die("这个视频没有可用字幕。\n"
        "既没有人工字幕，自动字幕里也找不到原生语言轨——只有机器翻译轨，那种不能用。\n"
        "要出文稿只能下音频后本地转录，本子技能暂不支持。")


def download(url, vid, kind, lang, outdir):
    """下载选定的那一条字幕轨。"""
    flag = "--write-subs" if kind == "manual" else "--write-auto-subs"
    run_ytdlp(["--skip-download", flag, "--sub-langs", lang,
               "--sub-format", "vtt/srt/best",
               "-o", str(outdir / "%(id)s.%(ext)s"), url], timeout=300)

    hits = sorted(outdir.glob(f"{vid}.*.vtt")) or sorted(outdir.glob(f"{vid}.*.srt"))
    if not hits:
        hits = sorted(outdir.glob("*.vtt")) or sorted(outdir.glob("*.srt"))
    if not hits:
        die(f"字幕轨 {lang} 声称存在，实际没下下来。换一条轨或稍后重试。")
    # 语言代码带点号时（zh-Hans）可能有多个匹配，取最大的那个（内容最全）
    return max(hits, key=lambda p: p.stat().st_size)


def main():
    ap = argparse.ArgumentParser(description="探测并下载最优字幕轨")
    ap.add_argument("url")
    ap.add_argument("--json", action="store_true", help="只输出 JSON，供程序调用")
    args = ap.parse_args()

    vid = video_id_of(args.url)
    outdir = workdir(vid)

    meta, manual, auto = probe(args.url)
    kind, lang, why = pick_track(meta, manual, auto)
    hints = transcript_hints(meta.get("description"))
    sub_path = download(args.url, vid, kind, lang, outdir)

    result = {
        "video_id": vid,
        "title": meta.get("title"),
        "channel": meta.get("channel") or meta.get("uploader"),
        "upload_date": meta.get("upload_date"),
        "duration": meta.get("duration_string"),
        "url": meta.get("webpage_url") or args.url,
        "subtitle_kind": kind,
        "subtitle_lang": lang,
        "subtitle_path": str(sub_path),
        "needs_translation": not is_chinese(lang),
        "why": why,
        "manual_langs": sorted(manual),
        "channel_url": meta.get("channel_url") or meta.get("uploader_url"),
        "transcript_candidates": hints,
        "description": meta.get("description"),
        "workdir": str(outdir),
    }
    save_json(outdir / "meta.json", result)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"标题     {result['title']}")
        print(f"频道     {result['channel']}　{result['upload_date']}　{result['duration']}")
        print(f"选用轨   {why}")
        print(f"需翻译   {'是' if result['needs_translation'] else '否（中文）'}")
        print(f"字幕     {sub_path}")
        if hints:
            print(f"现成文稿 描述栏给出 {len(hints)} 条候选，先按 SKILL ⓪ 验一验：")
            for h in hints:
                print(f"         {h['url']}　（{h['label']}）")
        elif kind == "auto":
            print(f"现成文稿 描述栏没写。这条是自动字幕，按 SKILL ⓪ 去频道官网找一次："
                  f"{result['channel_url'] or '—'}")


if __name__ == "__main__":
    main()
