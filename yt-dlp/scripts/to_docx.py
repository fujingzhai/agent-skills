#!/usr/bin/env python3
"""把整理好的 Markdown 稿子转成普通 DOCX 文件。

纯标准库实现（zip + OOXML），不依赖 pandoc / python-docx，任何 Python 3.9+
环境都能直接跑。支持本套件文稿里用到的 Markdown 子集：

  # 标题          → Word 内置标题样式
  > 引述          → 左边框引述样式（元信息块）
  - / 1. 列表     → 带缩进的列表段
  **加粗**        → 加粗
  ==高亮==        → 黄色字符底纹
  <sup>(说明)</sup> → 上标小字（Word 里做成小号上标）
  <span style="color: #d23f31;">存疑</span> → 红色字
  [00:01:02]      → 灰色小号时间戳
  [文字](url)     → 蓝色下划线文字

用法：
  python3 to_docx.py -o 输出.docx part1.md [part2.md ...]
  python3 to_docx.py --title "标题" -o 输出.docx part1.md

多个 md 会按顺序拼成一个文档，适合长文分批翻译后合并。
"""

import argparse
import re
import sys
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"

# 行内标记：一次扫一遍，按捕获组判断类型
TOKEN_RE = re.compile(
    r"(?P<sup><sup>.*?</sup>)"
    r"|(?P<span><span\b[^>]*>.*?</span>)"
    r"|(?P<bold>\*\*.+?\*\*|__.+?__)"
    r"|(?P<hlig>==.+?==)"
    r"|(?P<code>`[^`]+`)"
    r"|(?P<link>\[[^\]]+\]\([^)]+\))"
    r"|(?P<italic>\*[^*\s][^*]*\*|(?<![A-Za-z0-9])_[^_]+_)"
    r"|(?P<ts>\[\d{2}:\d{2}:\d{2}\])"
)

TS_RE = re.compile(r"^\[\d{2}:\d{2}:\d{2}\]")


def _text_of(html_like: str) -> str:
    """剥掉 <sup> / <span> 等外壳，返回内部文本。"""
    return re.sub(r"</?[^>]+>", "", html_like)


def _rpr(xml_bits):
    if not xml_bits:
        return ""
    return "<w:rPr>" + "".join(xml_bits) + "</w:rPr>"


def _run(text: str, rpr: str = "") -> str:
    return (
        "<w:r>"
        + (rpr or "")
        + f'<w:t xml:space="preserve">{escape(text)}</w:t>'
        + "</w:r>"
    )


def _para(style: str, runs_xml: str, extra_ppr: str = "") -> str:
    ppr = f'<w:pPr><w:pStyle w:val="{style}"/>' + extra_ppr + "</w:pPr>"
    return f"<w:p>{ppr}{runs_xml}</w:p>"


def _inline_runs(text: str):
    """把一段 Markdown 行内文本解析成若干 <w:r> 的 XML 片段。"""
    out = []
    pos = 0
    for m in TOKEN_RE.finditer(text):
        if m.start() > pos:
            out.append(_run(text[pos:m.start()]))
        kind = m.lastgroup
        token = m.group(0)
        if kind == "bold":
            out.append(_run(_text_of(token[2:-2]) if token.startswith("**") else _text_of(token[2:-2]), _rpr(["<w:b/>"])))
        elif kind == "hlig":
            out.append(_run(_text_of(token[2:-2]), _rpr(["<w:highlight w:val=\"yellow\"/>"])))
        elif kind == "code":
            out.append(_run(token[1:-1], _rpr([
                '<w:rFonts w:ascii="Menlo" w:hAnsi="Menlo" w:eastAsia="宋体"/>',
                '<w:color w:val="C7254E"/>',
            ])))
        elif kind == "italic":
            inner = token[1:-1]
            out.append(_run(inner, _rpr(["<w:i/>"])))
        elif kind == "sup":
            out.append(_run(_text_of(token), _rpr([
                '<w:vertAlign w:val="superscript"/>',
                '<w:sz w:val="18"/>',
            ])))
        elif kind == "span":
            span_text, color, memo = _span_info(token)
            if color:
                out.append(_run(span_text, _rpr([f'<w:color w:val="{color}"/>'])))
            else:
                out.append(_run(span_text))
            if memo:
                out.append(_run(f"（{memo}）", _rpr([
                    '<w:vertAlign w:val="superscript"/>',
                    '<w:sz w:val="18"/>',
                    '<w:color w:val="595959"/>',
                ])))
        elif kind == "link":
            lm = re.fullmatch(r"\[([^\]]+)\]\(([^)]+)\)", token)
            out.append(_run(lm.group(1), _rpr([
                '<w:color w:val="0563C1"/>',
                '<w:u w:val="single"/>',
            ])))
        elif kind == "ts":
            out.append(_run(token, _rpr([
                '<w:color w:val="808080"/>',
                '<w:sz w:val="18"/>',
            ])))
        pos = m.end()
    if pos < len(text):
        out.append(_run(text[pos:]))
    return "".join(out)


def _span_info(token: str):
    """解析 <span ...>text</span>，返回 (文本, 颜色, 备注)。"""
    inner = _text_of(token)
    color = None
    cm = re.search(r"color:\s*(#[0-9A-Fa-f]{6}|[A-Za-z]+)", token)
    if cm:
        color = cm.group(1)
    memo = None
    mm = re.search(r"data-inline-memo-content=\"([^\"]*)\"", token)
    if mm:
        memo = mm.group(1)
    return inner, color, memo


def _strip_md_whitespace(line: str) -> str:
    return line.strip()


def _block_para(lines, style, extra_ppr=""):
    """把若干 markdown 行合并成一个段落（行内用空格连接）。"""
    text = " ".join(_strip_md_whitespace(ln) for ln in lines)
    return _para(style, _inline_runs(text), extra_ppr)


def _is_hr(line: str) -> bool:
    return bool(re.fullmatch(r"\s*(-{3,}|\*{3,}|_{3,})\s*", line))


def _heading_level(line: str):
    m = re.match(r"^(#{1,6})\s+(.*)$", line)
    if not m:
        return None, None
    return len(m.group(1)), m.group(2).strip()


def _quote_lines(line: str):
    return re.match(r"^\s*>\s?(.*)$", line)


def _list_item(line: str):
    m = re.match(r"^\s*[-*+]\s+(.*)$", line)
    if m:
        return "ul", m.group(1).strip()
    m = re.match(r"^\s*(\d+)[.)]\s+(.*)$", line)
    if m:
        return "ol", f"{m.group(1)}. {m.group(2).strip()}"
    return None, None


def _build_document(md_files, title=None):
    """解析多个 md 文件，返回 document.xml 的 body 内容与文档标题。"""
    body = []
    doc_title = title

    for path in md_files:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
        i = 0
        while i < len(lines):
            raw = lines[i]

            # 空行
            if not raw.strip():
                i += 1
                continue

            # 水平线
            if _is_hr(raw):
                i += 1
                continue

            # 标题
            level, text = _heading_level(raw)
            if level is not None:
                if doc_title is None and level == 1:
                    doc_title = text
                style = {1: "Heading1", 2: "Heading2", 3: "Heading3"}.get(level, "Heading3")
                body.append(_para(style, _inline_runs(text)))
                i += 1
                continue

            # 引述块（连续 > 合并为一段）
            if _quote_lines(raw):
                qlines = []
                while i < len(lines):
                    qm = _quote_lines(lines[i])
                    if not qm:
                        break
                    qlines.append(qm.group(1))
                    i += 1
                body.append(_block_para(qlines, "Quote"))
                continue

            # 列表项（连续同类各自成段）
            kind, item = _list_item(raw)
            if kind:
                while i < len(lines):
                    k2, item2 = _list_item(lines[i])
                    if k2 != kind:
                        break
                    if k2 == "ul":
                        runs = _run("• ", _rpr(['<w:color w:val="595959"/>'])) + _inline_runs(item2)
                    else:
                        runs = _inline_runs(item2)
                    body.append(_para("List", runs))
                    i += 1
                continue

            # 普通段落：把连续非空、非特殊块的行合并
            para_lines = [raw.strip()]
            i += 1
            while i < len(lines) and lines[i].strip():
                nxt = lines[i].strip()
                if (_heading_level(nxt)[0] is not None or _quote_lines(nxt)
                        or _is_hr(nxt) or _list_item(nxt)[0]):
                    break
                para_lines.append(nxt)
                i += 1
            body.append(_block_para(para_lines, "Normal"))

    return "".join(body), doc_title or "transcript"


def build_styles_xml():
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        f'<w:styles xmlns:w="{NS}">'
        '<w:style w:type="paragraph" w:default="1" w:styleId="Normal">'
        '<w:name w:val="Normal"/><w:qFormat/>'
        '<w:rPr>'
        '<w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:eastAsia="宋体"/>'
        '<w:sz w:val="22"/><w:szCs w:val="22"/>'
        "</w:rPr></w:style>"
        '<w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/>'
        '<w:basedOn w:val="Normal"/><w:qFormat/>'
        '<w:pPr><w:spacing w:after="240"/></w:pPr>'
        '<w:rPr><w:b/><w:sz w:val="32"/><w:szCs w:val="32"/></w:rPr></w:style>'
        '<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/>'
        '<w:basedOn w:val="Normal"/><w:qFormat/>'
        '<w:pPr><w:spacing w:before="360" w:after="160"/></w:pPr>'
        '<w:rPr><w:b/><w:sz w:val="28"/><w:szCs w:val="28"/></w:rPr></w:style>'
        '<w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/>'
        '<w:basedOn w:val="Normal"/><w:qFormat/>'
        '<w:pPr><w:spacing w:before="300" w:after="140"/></w:pPr>'
        '<w:rPr><w:b/><w:sz w:val="24"/><w:szCs w:val="24"/></w:rPr></w:style>'
        '<w:style w:type="paragraph" w:styleId="Heading3"><w:name w:val="heading 3"/>'
        '<w:basedOn w:val="Normal"/><w:qFormat/>'
        '<w:pPr><w:spacing w:before="240" w:after="120"/></w:pPr>'
        '<w:rPr><w:b/><w:sz w:val="22"/><w:szCs w:val="22"/></w:rPr></w:style>'
        '<w:style w:type="paragraph" w:styleId="Quote"><w:name w:val="Quote"/>'
        '<w:basedOn w:val="Normal"/>'
        '<w:pPr>'
        '<w:ind w:left="360"/>'
        '<w:pBdr><w:left w:val="single" w:sz="12" w:space="12" w:color="999999"/></w:pBdr>'
        "</w:pPr>"
        '<w:rPr><w:color w:val="595959"/></w:rPr></w:style>'
        '<w:style w:type="paragraph" w:styleId="List"><w:name w:val="List"/>'
        '<w:basedOn w:val="Normal"/>'
        '<w:pPr><w:ind w:left="560" w:hanging="280"/></w:pPr></w:style>'
        "</w:styles>"
    )


def _build_docx(out_path: Path, md_files, title=None):
    body_xml, doc_title = _build_document(md_files, title)

    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        f'<w:document xmlns:w="{NS}"><w:body>{body_xml}'
        '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
        '<w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440"/>'
        "</w:sectPr></w:body></w:document>"
    )

    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
        '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
        "</Types>"
    )

    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
        "</Relationships>"
    )

    core_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/">'
        f"<dc:title>{escape(doc_title)}</dc:title>"
        f"<dc:creator>yt-dlp skill</dc:creator>"
        f"<cp:lastModifiedBy>yt-dlp skill</cp:lastModifiedBy>"
        "</cp:coreProperties>"
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", rels)
        z.writestr("word/document.xml", document_xml)
        z.writestr("word/styles.xml", build_styles_xml())
        z.writestr("docProps/core.xml", core_xml)
    return doc_title


def main():
    ap = argparse.ArgumentParser(description="Markdown 文稿 → 普通 DOCX（纯标准库）")
    ap.add_argument("md_files", nargs="+", help="一个或多个 markdown 文件，按顺序合并")
    ap.add_argument("-o", "--out", help="输出 docx 路径；默认与第一个 md 同目录的 transcript.docx")
    ap.add_argument("--title", help="文档标题；缺省时取第一个一级标题")
    args = ap.parse_args()

    out = Path(args.out) if args.out else Path(args.md_files[0]).parent / "transcript.docx"
    title = _build_docx(out, args.md_files, args.title)
    print(f"已生成 {out}")
    print(f"标题   {title}")


if __name__ == "__main__":
    main()
