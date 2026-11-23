"""文档工具：view_document —— PDF / Word 按页看，文字 + 页面图一起挂进上下文。

PDF：pdftotext 抽文字，pdftoppm / mutool / gs 渲染成 PNG；
DOCX：zip+XML 抽文字，docx 里的插图直接抽出来，装了 soffice 还能整页转图。
所有页面图都用 `VIEW_IMAGE:` 行返回，agent 下一轮自动挂成多模态消息。
"""

from __future__ import annotations

from typing import Any

from .. import documents as docs
from .. import providers as provider_store
from ..config import settings
from ..errors import AppError
from ..security import active_root, display_path, resolve_in_sandbox
from ..security import display_path, resolve_in_sandbox
from .base import ToolContext, ToolRegistry


def _vision_on() -> bool:
    try:
        return bool(provider_store.active_meta(settings).get("vision"))
    except Exception:  # noqa: BLE001 - 读不到配置就按能看处理
        return True


def register(registry: ToolRegistry) -> None:
    def view_document(ctx: ToolContext, args: dict[str, Any]) -> str:
        raw = str(args.get("path") or "").strip()
        if not raw:
            raise AppError("path 不能为空（相对 workspace 或绝对路径都行）")
        path = resolve_in_sandbox(raw)
        if not path.exists() or not path.is_file():
            raise AppError(f"文件不存在：{display_path(path)}")
        suffix = path.suffix.lower()
        if not docs.is_document(suffix):
            raise AppError(f"只认 pdf / docx / doc，这个是 {suffix or '无后缀'}")

        try:
            first, last = docs.parse_pages(args.get("pages"), docs.page_count(path) if suffix == ".pdf" else 0)
        except ValueError as exc:
            raise AppError(str(exc)) from exc
        dpi = int(args.get("dpi") or docs.DEFAULT_DPI)
        text_limit = int(args.get("text_max") or 6000)

        info = docs.read_text(path, first, last, limit=text_limit)
        total = info.pages
        lines: list[str] = [f"文档：{display_path(path)}"]
        if total:
            lines.append(f"总页数：{total} · 本次看第 {first}-{last} 页")
        else:
            lines.append("页数：未知（不是 PDF 分页格式）")
        lines.append(f"文字引擎：{info.engine}")

        body = info.text.strip()
        if body:
            lines.append("")
            lines.append(body)
            if len(body) >= text_limit:
                lines.append("\n…（文字到上限被截断，把 text_max 调大或分页看）")
        else:
            lines.append("\n（这一部分没抽到文字：可能是扫描件，或需要 soffice / poppler）")

        images: list[Any] = []
        render_note = ""
        can_see = _vision_on()
        if not can_see:
            render_note = "当前模型被判定为不支持看图，只给了文字；要用页面图去「提供商 → 视觉」打开。"
        else:
            out_dir = active_root() / "_docpages" / re_slug(path.stem)
            pdf = path
            if suffix == ".pdf":
                result = docs.render_pdf(path, out_dir, first, last, dpi)
            elif suffix == ".docx":
                media = docs.docx_images(path, limit=docs.MAX_PAGES_PER_CALL)
                for idx, raw_png in enumerate(media, start=1):
                    out_dir.mkdir(parents=True, exist_ok=True)
                    target = out_dir / f"inline-{idx}.png"
                    target.write_bytes(raw_png)
                    images.append(target)
                pdf = docs.convert_to_pdf(path, out_dir / "_converted") or path
                if pdf.suffix.lower() == ".pdf":
                    result = docs.render_pdf(pdf, out_dir / "pages", first, last, dpi)
                else:
                    result = docs.RenderResult(error="没装 soffice，Word 只能给文字（Termux：pkg install libreoffice）")
            else:
                pdf = docs.convert_to_pdf(path, out_dir / "_converted") or path
                result = (
                    docs.render_pdf(pdf, out_dir / "pages", first, last, dpi)
                    if pdf.suffix.lower() == ".pdf"
                    else docs.RenderResult(error="没装 soffice，老 .doc 只能给文字（Termux：pkg install libreoffice）")
                )
            if result.images:
                images.extend(result.images)
                lines.append("")
                lines.append(f"页面图：{len(result.images)} 张（{result.engine} · {dpi} dpi）")
            elif result.error:
                render_note = result.error
                lines.append("")
                lines.append(f"页面图没出来：{result.error}")

        if images:
            lines.append("")
            for image in images:
                lines.append(f"VIEW_IMAGE: {image.resolve()}")
            lines.append("上面这些页面图会在下一轮自动挂到上下文里，模型能直接看到版式、表格、印章、图表。")
        elif render_note:
            lines.append("")
            lines.append(render_note)

        if total and last < total:
            remaining = total - last
            lines.append("")
            lines.append(f"还有 {remaining} 页没看：接着调 view_document pages=\"{last + 1}-{min(total, last + docs.MAX_PAGES_PER_CALL)}\"。")

        return "\n".join(lines)

    registry.register(
        "view_document",
        "看 PDF / Word：按页抽文字，并把页面渲染成图片直接喂给多模态模型（表格、版式、印章、扫描件都能看）。"
        "默认看前 4 页，用 pages=\"5-8\" 接着看下一片，dpi 可调清晰度。"
        "凡是要读 pdf / docx / doc 的内容，必须用这个工具；不要用 run_python 自己解 PDF 或 docx，那是看不见页面的。",
        {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "文档路径，相对 workspace 或绝对路径"},
                "pages": {
                    "type": "string",
                    "description": "页码或范围：\"2\"、\"1-4\"、\"all\"；默认前 4 页，单次最多 12 页",
                },
                "dpi": {"type": "integer", "description": "渲染清晰度，默认 110，范围 72-220（越高越清楚也越占内存）"},
                "text_max": {"type": "integer", "description": "文字上限字符数，默认 6000"},
            },
            "required": ["path"],
        },
        view_document,
    )


def re_slug(text: str) -> str:
    keep = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in text)
    return (keep.strip("-")[:40] or "doc")
