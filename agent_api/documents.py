"""PDF / Word：抽文字、按页拆片、渲染成图片（给多模态模型真正「看」）。

全部走标准库 + 系统里现成的命令行工具，不装任何 pip 包：

- PDF 文字：优先 `pdftotext`（poppler），没有就退回内置的极简解析（够看普通文本 PDF）
- PDF 图片：`pdftoppm` / `mutool draw` / `gs`，三者有其一即可
- DOCX 文字：zipfile + XML，永远可用
- DOCX / DOC 图片：先用 `soffice` 转 PDF，再交给上面的 PDF 渲染器

Termux 里开图片能力只要一句 `pkg install poppler`；Word 想转图再装 libreoffice。
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import zipfile
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

logger = logging.getLogger("agent.documents")

SUPPORTED = {".pdf", ".docx", ".doc"}
DEFAULT_DPI = 110
MAX_PAGES_PER_CALL = 12
MAX_TEXT_CHARS = 8000


def _which(*names: str) -> str | None:
    for name in names:
        path = shutil.which(name)
        if path:
            return path
    return None


def pdftotext_bin() -> str | None:
    return _which("pdftotext")


def pdfinfo_bin() -> str | None:
    return _which("pdfinfo")


def pdftoppm_bin() -> str | None:
    return _which("pdftoppm")


def mutool_bin() -> str | None:
    return _which("mutool")


def gs_bin() -> str | None:
    return _which("gs", "gswin64c")


def soffice_bin() -> str | None:
    return _which("soffice", "libreoffice")


def render_engine() -> str:
    if pdftoppm_bin():
        return "pdftoppm"
    if mutool_bin():
        return "mutool"
    if gs_bin():
        return "gs"
    return ""


def is_document(suffix: str) -> bool:
    return suffix.lower() in SUPPORTED


def page_count(path: Path) -> int:
    """PDF 总页数；拿不到就返回 0（未知）。"""
    if pdfinfo_bin():
        try:
            out = subprocess.run(
                [pdfinfo_bin() or "pdfinfo", str(path)],
                capture_output=True,
                timeout=30,
                check=False,
            ).stdout.decode("utf-8", "replace")
            found = re.search(r"^Pages:\s+(\d+)", out, re.M)
            if found:
                return int(found.group(1))
        except (OSError, subprocess.SubprocessError):
            pass
    if mutool_bin():
        try:
            out = subprocess.run(
                [mutool_bin() or "mutool", "info", str(path)],
                capture_output=True,
                timeout=30,
                check=False,
            ).stdout.decode("utf-8", "replace")
            found = re.search(r"Pages:\s+(\d+)", out)
            if found:
                return int(found.group(1))
        except (OSError, subprocess.SubprocessError):
            pass
    try:
        raw = path.read_bytes()
    except OSError:
        return 0
    return len(re.findall(rb"/Type\s*/Page\b", raw))


def parse_pages(spec: str | None, total: int, default_last: int = 4) -> tuple[int, int]:
    """把 "1-3" / "5" / "all" 解析成 (first, last)，页码从 1 开始，含端点。"""
    if not spec or not str(spec).strip():
        return 1, min(default_last, total) if total else default_last
    text = str(spec).strip().lower()
    if text in {"all", "全部", "所有"}:
        return 1, total if total else default_last
    found = re.match(r"^(\d+)\s*(?:[-~至]\s*(\d+))?$", text)
    if not found:
        raise ValueError(f"pages 看不懂：{spec}（写 2、1-4、all 都行）")
    first = max(1, int(found.group(1)))
    last = max(first, int(found.group(2) or first))
    if last - first + 1 > MAX_PAGES_PER_CALL:
        last = first + MAX_PAGES_PER_CALL - 1
    return first, last


def _decode_pdf_literal(raw: bytes) -> str:
    body = raw[1:-1]
    body = re.sub(rb"\\\\([nrtbf])", lambda m: {b"n": b"\n", b"r": b"\r", b"t": b"\t", b"b": b"\b", b"f": b"\f"}[m.group(1)], body)
    body = re.sub(rb"\\\\([()\\\\])", rb"\1", body)
    body = re.sub(rb"\\\\(\d{1,3})", lambda m: bytes([int(m.group(1))]) if int(m.group(1)) < 256 else b"", body)
    try:
        return body.decode("utf-8")
    except UnicodeDecodeError:
        return body.decode("latin-1", "ignore")


def _pdf_text_builtin(path: Path) -> str:
    """极简 PDF 文字抽取：解 FlateDecode，扫 BT/ET 里的 Tj/TJ。中文靠 ToUnicode 的复杂字体不一定准。"""
    try:
        raw = path.read_bytes()
    except OSError:
        return ""
    pieces: list[str] = []
    for stream in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", raw, re.S):
        body = stream.group(1)
        try:
            body = zlib.decompress(body)
        except zlib.error:
            pass
        if b"BT" not in body:
            continue
        for block in re.findall(rb"BT(.*?)ET", body, re.S):
            for token in re.finditer(rb"(\((?:\\.|[^()\\])*\))\s*Tj|\[(.*?)\]\s*TJ", block, re.S):
                if token.group(1):
                    pieces.append(_decode_pdf_literal(token.group(1)))
                elif token.group(2):
                    inner = re.findall(rb"(\((?:\\.|[^()\\])*\))", token.group(2), re.S)
                    pieces.append("".join(_decode_pdf_literal(one) for one in inner))
            pieces.append("\n")
    text = "".join(pieces)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def extract_pdf_text(path: Path, first: int = 1, last: int = 0, limit: int = MAX_TEXT_CHARS) -> tuple[str, str]:
    """返回 (文字, 引擎说明)。last=0 表示到末尾。"""
    if pdftotext_bin():
        cmd = [pdftotext_bin() or "pdftotext", "-layout", "-enc", "UTF-8"]
        if first > 1:
            cmd += ["-f", str(first)]
        if last:
            cmd += ["-l", str(last)]
        cmd += [str(path), "-"]
        try:
            out = subprocess.run(cmd, capture_output=True, timeout=60, check=False)
            text = out.stdout.decode("utf-8", "replace")
            if text.strip():
                return text[:limit], "pdftotext"
            return "", "pdftotext（这一页几乎没有可选文字，可能是扫描件）"
        except (OSError, subprocess.SubprocessError) as exc:
            logger.warning("pdftotext 失败：%s", exc)
    text = _pdf_text_builtin(path)
    return text[:limit], "内置解析（中文/扫描件可能不全，装 poppler 更准）"


_DOCX_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def docx_text(path: Path, limit: int = MAX_TEXT_CHARS) -> str:
    """纯标准库读 .docx（zip + XML），段落、表格都摊平成文字。"""
    try:
        with zipfile.ZipFile(path) as zf:
            xml = zf.read("word/document.xml")
    except (KeyError, zipfile.BadZipFile, OSError):
        return ""
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError:
        return ""
    lines: list[str] = []
    for para in root.iter(_DOCX_NS + "p"):
        chunk = "".join(node.text or "" for node in para.iter(_DOCX_NS + "t"))
        chunk = chunk.strip()
        if chunk:
            lines.append(chunk)
    text = "\n".join(lines)
    return text[:limit]


def docx_images(path: Path, limit: int = 6) -> list[bytes]:
    """docx 里自带的插图（word/media/*），原样返回字节，方便落盘给模型看。"""
    out: list[bytes] = []
    try:
        with zipfile.ZipFile(path) as zf:
            for name in zf.namelist():
                if not name.startswith("word/media/"):
                    continue
                suffix = Path(name).suffix.lower()
                if suffix not in {".png", ".jpg", ".jpeg", ".gif", ".webp"}:
                    continue
                out.append(zf.read(name))
                if len(out) >= limit:
                    break
    except (zipfile.BadZipFile, OSError):
        pass
    return out


def _run(cmd: list[str], timeout: int = 90) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, timeout=timeout, check=False)


def convert_to_pdf(path: Path, out_dir: Path) -> Path | None:
    """用 soffice 把 doc/docx 转成 PDF，返回 PDF 路径；没有 soffice 就 None。"""
    binary = soffice_bin()
    if not binary:
        return None
    out_dir.mkdir(parents=True, exist_ok=True)
    home = out_dir / ".lo-profile"
    home.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env.setdefault("HOME", str(home))
    try:
        _run(
            [
                binary,
                "--headless",
                "--norestore",
                f"-env:UserInstallation=file://{home}",
                "--convert-to",
                "pdf",
                "--outdir",
                str(out_dir),
                str(path),
            ],
            timeout=180,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("soffice 转换失败：%s", exc)
        return None
    target = out_dir / (path.stem + ".pdf")
    return target if target.exists() else None


@dataclass
class RenderResult:
    images: list[Path] = field(default_factory=list)
    engine: str = ""
    error: str = ""


def render_pdf(path: Path, out_dir: Path, first: int, last: int, dpi: int = DEFAULT_DPI) -> RenderResult:
    """把 PDF 的第 first..last 页渲染成 PNG。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("page-*.png"):
        try:
            old.unlink()
        except OSError:
            pass
    prefix = out_dir / "page"
    dpi = max(72, min(int(dpi or DEFAULT_DPI), 220))

    if pdftoppm_bin():
        cmd = [
            pdftoppm_bin() or "pdftoppm",
            "-png",
            "-r",
            str(dpi),
            "-f",
            str(first),
            "-l",
            str(last),
            str(path),
            str(prefix),
        ]
        try:
            _run(cmd, timeout=120)
        except (OSError, subprocess.SubprocessError) as exc:
            return RenderResult(engine="pdftoppm", error=f"渲染失败：{exc}")
        return RenderResult(images=sorted(out_dir.glob("page-*.png")), engine="pdftoppm")

    if mutool_bin():
        try:
            _run(
                [mutool_bin() or "mutool", "draw", "-r", str(dpi), "-o", str(out_dir / "page-%d.png"),
                 str(path), f"{first}-{last}"],
                timeout=120,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return RenderResult(engine="mutool", error=f"渲染失败：{exc}")
        images = []
        for idx in range(first, last + 1):
            p = out_dir / f"page-{idx}.png"
            if p.exists():
                images.append(p)
        return RenderResult(images=images, engine="mutool")

    if gs_bin():
        try:
            _run(
                [
                    gs_bin() or "gs",
                    "-dSAFER",
                    "-dBATCH",
                    "-dNOPAUSE",
                    "-sDEVICE=png16m",
                    f"-r{dpi}",
                    f"-dFirstPage={first}",
                    f"-dLastPage={last}",
                    f"-sOutputFile={out_dir / 'page-%d.png'}",
                    str(path),
                ],
                timeout=180,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return RenderResult(engine="gs", error=f"渲染失败：{exc}")
        images = []
        for idx in range(first, last + 1):
            p = out_dir / f"page-{idx}.png"
            if p.exists():
                images.append(p)
        return RenderResult(images=images, engine="gs")

    return RenderResult(error="这台机器没有 PDF 渲染器（Termux 里跑一句 pkg install poppler 就好）")


@dataclass
class DocText:
    text: str = ""
    engine: str = ""
    pages: int = 0
    first: int = 1
    last: int = 0


def read_text(path: Path, first: int = 1, last: int = 0, limit: int = MAX_TEXT_CHARS) -> DocText:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        pages = page_count(path)
        text, engine = extract_pdf_text(path, first, last or pages, limit)
        return DocText(text=text, engine=engine, pages=pages, first=first, last=last or pages)
    if suffix == ".docx":
        return DocText(text=docx_text(path, limit), engine="zip + XML", pages=0)
    if suffix == ".doc":
        pdf = convert_to_pdf(path, path.parent / "_converted")
        if pdf:
            pages = page_count(pdf)
            text, engine = extract_pdf_text(pdf, first, last or pages, limit)
            return DocText(text=text, engine="soffice → pdftotext", pages=pages, first=first, last=last or pages)
        return DocText(text="", engine="需要 soffice（Termux 里 pkg install libreoffice）才能读老 .doc", pages=0)
    return DocText(text="", engine=f"不支持的后缀 {suffix or '（无）'}", pages=0)


def expand_attachment(path: Path, out_dir: Path, max_pages: int = 6, dpi: int = DEFAULT_DPI) -> dict[str, Any]:
    """给对话附件用：一段文字 + 前几页图片。

    返回 {"text": str, "note": str, "images": [Path], "pages": int}
    """
    info = read_text(path, 1, 0, limit=7000)
    note_parts: list[str] = []
    images: list[Path] = []
    if info.pages:
        note_parts.append(f"共 {info.pages} 页")
    if info.text.strip():
        first, last = 1, info.pages or 0
        head = f"（第 {first}-{last or '末'} 页，{info.engine}）" if info.pages else f"（{info.engine}）"
        text = f"[文档 {path.name} {head}]\n{info.text.strip()}"
        if len(info.text) >= 7000:
            text += "\n…（文字太长已截断，后续页用 view_document 按页看）"
    else:
        text = f"[文档 {path.name}：没抽到文字（{info.engine}）]"

    if path.suffix.lower() == ".docx":
        media = docx_images(path, limit=max_pages)
        for idx, raw in enumerate(media, start=1):
            suffix = ".png"
            target = out_dir / f"inline-{idx}{suffix}"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
            images.append(target)

    if path.suffix.lower() in {".pdf", ".docx", ".doc"}:
        pdf = path
        if path.suffix.lower() != ".pdf":
            pdf = convert_to_pdf(path, out_dir / "_converted") or path
        if pdf.suffix.lower() == ".pdf":
            last = min(info.pages or max_pages, max_pages)
            result = render_pdf(pdf, out_dir / "pages", 1, max(1, last), dpi)
            if result.images:
                images.extend(result.images)
                note_parts.append(f"前 {len(result.images)} 页已渲染成图片（{result.engine} · {dpi} dpi）")
            elif result.error:
                note_parts.append(result.error)

    return {"text": text, "note": " · ".join(note_parts), "images": images, "pages": info.pages}
