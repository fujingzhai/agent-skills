"""把 VTT/SRT 洗成可读文稿。

自动字幕和人工字幕的脏法完全不同，所以分开处理：

  自动字幕：滚动式重复（每个 cue 都带着上一个 cue 的尾巴）、逐词内联时间戳
            <00:00:12.720><c>word</c>、声音事件标记插在句子中间。
  人工字幕：一般已是干净句子，只需去掉定位参数和声音标记。

声音事件标记（[Music]/[音乐]/♪）必须剥掉：YouTube 会把它塞进句子中间，
下游任何逐句翻译都会把它当成句子成分译出来，正文里就会冒出莫名其妙的"音乐"。
"""

import argparse
import html
import re
from pathlib import Path

# 声音事件标记：中英日常见写法都收
SOUND_TAG = re.compile(
    r"\[(music|applause|laughter|laughs|sound|noise|silence|inaudible|"
    r"clears throat|coughs|sighs|音楽|音乐|掌声|掌聲|笑声|笑聲|静音|无声)\]",
    re.I)
MUSIC_NOTE = re.compile(r"[♪♫🎵🎶]")
INLINE_TS = re.compile(r"<\d{2}:\d{2}:\d{2}[.,]\d{3}>")
TAG = re.compile(r"</?c[^>]*>|</?v[^>]*>|</?i>|</?b>|</?u>")
CUE_TIME = re.compile(r"(\d{2}:\d{2}:\d{2})[.,](\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2})")


def parse_cues(raw):
    """解析 VTT / SRT，返回 [(秒, 文本)]。两种格式的差别只在头部和序号行。"""
    raw = raw.replace("\r\n", "\n").replace("\r", "\n")
    if raw.startswith("WEBVTT"):
        parts = raw.split("\n\n", 1)
        raw = parts[1] if len(parts) > 1 else ""

    cues = []
    for block in raw.split("\n\n"):
        lines = [l for l in block.strip().split("\n") if l.strip()]
        if not lines:
            continue
        # SRT 的纯数字序号行
        if lines[0].strip().isdigit():
            lines = lines[1:]
        if not lines:
            continue
        m = CUE_TIME.search(lines[0])
        if not m:
            continue
        h, mi, s = map(int, m.group(1).split(":"))
        cues.append((h * 3600 + mi * 60 + s, " ".join(lines[1:])))
    return cues


def clean_text(text):
    text = INLINE_TS.sub("", text)
    text = TAG.sub("", text)
    text = html.unescape(text)
    text = SOUND_TAG.sub("", text)
    text = MUSIC_NOTE.sub("", text)
    text = re.sub(r"\{\\an?\d\}", "", text)          # ASS 定位残留
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def dedup_rolling(cues):
    """去掉自动字幕的滚动重复，保留每段的起始时间。

    自动字幕每个 cue 都会把上一个 cue 的尾巴重抄一遍，用来做屏幕上的滚动效果。
    直接拼接会得到大量重复，所以逐个剥掉与上文重叠的前缀。
    """
    out, prev = [], ""
    for sec, t in cues:
        if not t or t == prev:
            continue
        if prev and t.startswith(prev):
            t = t[len(prev):].strip()
        elif prev:
            # 找最长的重叠前缀，10 字符以下不算（太短容易误伤）
            for k in range(min(len(prev), len(t)), 10, -1):
                if t.startswith(prev[-k:]):
                    t = t[k:].strip()
                    break
        if t:
            out.append((sec, t))
            prev = t
    return out


def to_paragraphs(cues, kind, gap=3, timestamps=True):
    """合并成段落。时间间隔大或遇到句末标点且够长时断段。"""
    texts = [clean_text(t) for _, t in cues]
    times = [s for s, _ in cues]

    if kind == "auto":
        kept = dedup_rolling(list(zip(times, texts)))
        merged, buf, start = [], [], None
        for sec, t in kept:
            if start is None:
                start = sec
            buf.append(t)
            joined = " ".join(buf)
            if len(joined) > 320 and re.search(r"[.!?。！？]$", t):
                merged.append((start, joined))
                buf, start = [], None
        if buf:
            merged.append((start or 0, " ".join(buf)))
        paras = merged
    else:
        paras, buf, start, last = [], [], None, None
        for sec, t in zip(times, texts):
            if not t:
                continue
            if start is None:
                start = sec
            if last is not None and sec - last > gap and buf:
                paras.append((start, " ".join(buf)))
                buf, start = [], sec
            buf.append(t)
            last = sec
            joined = " ".join(buf)
            if len(joined) > 320 and re.search(r"[.!?。！？]$", t):
                paras.append((start, joined))
                buf, start = [], None
        if buf:
            paras.append((start or 0, " ".join(buf)))

    lines = []
    for sec, text in paras:
        text = re.sub(r"\s+([,.!?;:])", r"\1", text).strip()
        if not text:
            continue
        if timestamps:
            h, rem = divmod(int(sec), 3600)
            m, s = divmod(rem, 60)
            lines.append(f"[{h:02d}:{m:02d}:{s:02d}] {text}")
        else:
            lines.append(text)
    return lines


def main():
    ap = argparse.ArgumentParser(description="VTT/SRT → 可读文稿")
    ap.add_argument("subtitle")
    ap.add_argument("-o", "--out", help="输出路径，默认同目录 transcript.txt")
    ap.add_argument("--kind", choices=["auto", "manual"], default="auto",
                    help="字幕类型，决定是否做滚动去重")
    ap.add_argument("--no-timestamps", action="store_true")
    args = ap.parse_args()

    src = Path(args.subtitle)
    cues = parse_cues(src.read_text(encoding="utf-8"))
    if not cues:
        raise SystemExit(f"没解析出任何字幕块：{src}")

    lines = to_paragraphs(cues, args.kind, timestamps=not args.no_timestamps)
    out = Path(args.out) if args.out else src.parent / "transcript.txt"
    out.write_text("\n\n".join(lines) + "\n", encoding="utf-8")

    body = " ".join(lines)
    # 中文按字数、西文按词数，两种都报一个免得误判规模
    cjk = len(re.findall(r"[一-鿿]", body))
    words = len(body.split())
    print(f"段落 {len(lines)}　{'汉字 ' + str(cjk) if cjk > words / 2 else '词 ' + str(words)}")
    print(f"文稿 {out}")


if __name__ == "__main__":
    main()
