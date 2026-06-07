"""Render the trusted package methodology brief to PDF.

The renderer uses reportlab when available and falls back to a tiny stdlib PDF
writer otherwise. This keeps the data handoff package reproducible in the
low-dependency project environment.
"""

from __future__ import annotations

import argparse
import re
import textwrap
from pathlib import Path


def _inline_plain(text: str) -> str:
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    return text


def _escape_pdf_text(text: str) -> str:
    text = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    return text.encode("latin-1", errors="replace").decode("latin-1")


def _parse_markdown(input_md: Path) -> list[tuple[str, str]]:
    lines: list[tuple[str, str]] = []
    for raw_line in input_md.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            lines.append(("space", ""))
        elif line.startswith("# "):
            lines.append(("title", _inline_plain(line[2:])))
        elif line.startswith("## "):
            lines.append(("heading", _inline_plain(line[3:])))
        elif line.startswith("> "):
            lines.append(("quote", _inline_plain(line[2:])))
        elif line.startswith("- "):
            lines.append(("bullet", _inline_plain(line[2:])))
        elif re.match(r"^\d+\.\s+", line):
            lines.append(("bullet", _inline_plain(re.sub(r"^\d+\.\s+", "", line))))
        else:
            lines.append(("body", _inline_plain(line)))
    return lines


def _render_stdlib_pdf(input_md: Path, output_pdf: Path) -> None:
    parsed = _parse_markdown(input_md)
    page_width = 595
    page_height = 842
    margin_x = 48
    margin_y = 48
    max_y = page_height - margin_y
    min_y = margin_y

    pages: list[list[tuple[str, str, float, float]]] = [[]]
    y = max_y

    def add_line(text: str, font: str, size: float, leading: float, indent: float = 0) -> None:
        nonlocal y
        if y < min_y + leading:
            pages.append([])
            y = max_y
        pages[-1].append((text, font, margin_x + indent, y))
        y -= leading

    for kind, text in parsed:
        if kind == "space":
            y -= 4
            continue
        if kind == "title":
            for part in textwrap.wrap(text, width=62):
                add_line(part, "F2", 17, 22)
            y -= 3
            continue
        if kind == "heading":
            y -= 5
            for part in textwrap.wrap(text, width=76):
                add_line(part, "F2", 12, 16)
            continue
        if kind == "quote":
            for part in textwrap.wrap(text, width=82):
                add_line(part, "F1", 9, 12, indent=12)
            y -= 3
            continue
        if kind == "bullet":
            wrapped = textwrap.wrap(text, width=88) or [""]
            add_line("- " + wrapped[0], "F1", 9, 12, indent=10)
            for part in wrapped[1:]:
                add_line("  " + part, "F1", 9, 12, indent=10)
            continue
        for part in textwrap.wrap(text, width=94) or [""]:
            add_line(part, "F1", 9, 12)

    objects: dict[int, bytes] = {
        3: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        4: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>",
    }
    page_numbers: list[int] = []
    next_obj = 5
    for page in pages:
        content_lines = ["BT"]
        for text, font, x, line_y in page:
            size = 17 if font == "F2" and line_y > 760 else (12 if font == "F2" else 9)
            content_lines.append(f"/{font} {size} Tf")
            content_lines.append(f"1 0 0 1 {x:.2f} {line_y:.2f} Tm")
            content_lines.append(f"({_escape_pdf_text(text)}) Tj")
        content_lines.append("ET")
        stream = "\n".join(content_lines).encode("latin-1", errors="replace")
        content_obj = next_obj
        page_obj = next_obj + 1
        next_obj += 2
        objects[content_obj] = b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream"
        objects[page_obj] = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {page_width} {page_height}] "
            f"/Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> /Contents {content_obj} 0 R >>"
        ).encode("latin-1")
        page_numbers.append(page_obj)

    kids = " ".join(f"{page_num} 0 R" for page_num in page_numbers)
    objects[1] = b"<< /Type /Catalog /Pages 2 0 R >>"
    objects[2] = f"<< /Type /Pages /Kids [{kids}] /Count {len(page_numbers)} >>".encode("latin-1")

    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    pdf = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for obj_num in sorted(objects):
        offsets.append(len(pdf))
        pdf.extend(f"{obj_num} 0 obj\n".encode("latin-1"))
        pdf.extend(objects[obj_num])
        pdf.extend(b"\nendobj\n")
    xref_offset = len(pdf)
    max_obj = max(objects)
    pdf.extend(f"xref\n0 {max_obj + 1}\n".encode("latin-1"))
    pdf.extend(b"0000000000 65535 f \n")
    offset_by_obj = {obj_num: offset for obj_num, offset in zip(sorted(objects), offsets[1:])}
    for obj_num in range(1, max_obj + 1):
        pdf.extend(f"{offset_by_obj.get(obj_num, 0):010d} 00000 n \n".encode("latin-1"))
    pdf.extend(
        f"trailer\n<< /Size {max_obj + 1} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n".encode("latin-1")
    )
    output_pdf.write_bytes(bytes(pdf))


def _render_reportlab_pdf(input_md: Path, output_pdf: Path) -> None:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import ListFlowable, ListItem, Paragraph, SimpleDocTemplate, Spacer

    styles = getSampleStyleSheet()
    title = ParagraphStyle("TitleCustom", parent=styles["Title"], fontName="Helvetica-Bold", fontSize=18, leading=22, textColor=colors.HexColor("#1F2937"), spaceAfter=8)
    heading = ParagraphStyle("HeadingCustom", parent=styles["Heading2"], fontName="Helvetica-Bold", fontSize=12, leading=15, textColor=colors.HexColor("#9A4D00"), spaceBefore=10, spaceAfter=5)
    body = ParagraphStyle("BodyCustom", parent=styles["BodyText"], fontName="Helvetica", fontSize=9, leading=12, spaceAfter=5)
    quote = ParagraphStyle("QuoteCustom", parent=body, leftIndent=8, borderColor=colors.HexColor("#F2B56B"), borderWidth=1, borderPadding=6, backColor=colors.HexColor("#FFF7ED"), textColor=colors.HexColor("#374151"))
    bullet_style = ParagraphStyle("BulletCustom", parent=body, leftIndent=8, firstLineIndent=0, spaceAfter=2)
    story = []
    pending_bullets: list[str] = []

    def inline(text: str) -> str:
        text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        text = re.sub(r"`([^`]+)`", r"<font face='Courier'>\1</font>", text)
        text = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", text)
        return text

    def flush_bullets() -> None:
        nonlocal pending_bullets
        if not pending_bullets:
            return
        story.append(ListFlowable([ListItem(Paragraph(inline(item), bullet_style), leftIndent=8) for item in pending_bullets], bulletType="bullet", start="circle", leftIndent=14))
        story.append(Spacer(1, 3))
        pending_bullets = []

    for kind, text in _parse_markdown(input_md):
        if kind == "space":
            flush_bullets()
            story.append(Spacer(1, 3))
        elif kind == "title":
            flush_bullets()
            story.append(Paragraph(inline(text), title))
        elif kind == "heading":
            flush_bullets()
            story.append(Paragraph(inline(text), heading))
        elif kind == "quote":
            flush_bullets()
            story.append(Paragraph(inline(text), quote))
        elif kind == "bullet":
            pending_bullets.append(text)
        else:
            flush_bullets()
            story.append(Paragraph(inline(text), body))
    flush_bullets()
    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(str(output_pdf), pagesize=A4, rightMargin=16 * mm, leftMargin=16 * mm, topMargin=14 * mm, bottomMargin=14 * mm, title="Methodology Brief: Text Features for PPO", author="FinPortfolio IR")
    doc.build(story)


def render_pdf(input_md: Path, output_pdf: Path) -> None:
    try:
        import reportlab  # noqa: F401
    except ModuleNotFoundError:
        _render_stdlib_pdf(input_md, output_pdf)
    else:
        _render_reportlab_pdf(input_md, output_pdf)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    render_pdf(args.input, args.output)
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
