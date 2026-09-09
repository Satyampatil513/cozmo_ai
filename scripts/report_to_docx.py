"""Render docs/TECHNICAL_REPORT.md to a .docx, sized to hold the 6-page cap.

    python scripts/report_to_docx.py [--in docs/TECHNICAL_REPORT.md] [--out docs/TECHNICAL_REPORT.docx]

Handles the subset of Markdown the report uses: ATX headings, paragraphs, `-` bullets,
pipe tables, images (scaled so a two-panel figure stays ~2 in tall), block quotes, and
inline **bold** / *italic* / `code`. Not a general converter - just enough to regenerate the
submission doc from the source of truth in one command.
"""
from __future__ import annotations

import argparse
import os
import re

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MONO = "Consolas"
IMG_MAX_W = Inches(6.3)
IMG_MAX_H = Inches(1.25)          # keeps seven figures inside the six-page budget
ACCENT = RGBColor(0x1B, 0x4D, 0x6B)
MUTED = RGBColor(0x5A, 0x66, 0x70)
_INLINE = re.compile(r"(\*\*.+?\*\*|\*[^*]+?\*|`[^`]+?`)")


def _add_runs(paragraph, text: str):
    for tok in _INLINE.split(text):
        if not tok:
            continue
        if tok.startswith("**") and tok.endswith("**"):
            paragraph.add_run(tok[2:-2]).bold = True
        elif tok.startswith("`") and tok.endswith("`"):
            r = paragraph.add_run(tok[1:-1]); r.font.name = MONO; r.font.size = Pt(9)
        elif tok.startswith("*") and tok.endswith("*"):
            paragraph.add_run(tok[1:-1]).italic = True
        else:
            paragraph.add_run(tok)


def _set_cell_shading(cell, fill: str):
    properties = cell._tc.get_or_add_tcPr()
    shading = OxmlElement("w:shd")
    shading.set(qn("w:fill"), fill)
    properties.append(shading)


def _add_page_number(paragraph):
    paragraph.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    run = paragraph.add_run("Cozmo AI  |  ")
    run.font.size = Pt(8)
    run.font.color.rgb = MUTED
    field = OxmlElement("w:fldSimple")
    field.set(qn("w:instr"), "PAGE")
    paragraph._p.append(field)


def _img_size(path: str):
    from PIL import Image
    with Image.open(path) as im:
        w, h = im.size
    ar = w / h
    width, height = IMG_MAX_W, Inches(IMG_MAX_W.inches / ar)
    if height > IMG_MAX_H:
        height, width = IMG_MAX_H, Inches(IMG_MAX_H.inches * ar)
    return width, height


def _flush_table(doc, rows: list[list[str]]):
    if len(rows) < 2:
        return
    body = [r for i, r in enumerate(rows) if not (i == 1 and set("".join(r)) <= set("-: "))]
    header, data = body[0], body[1:]
    t = doc.add_table(rows=1, cols=len(header))
    t.style = "Light Grid Accent 1"
    for j, cell in enumerate(header):
        _set_cell_shading(t.rows[0].cells[j], "1B4D6B")
        p = t.rows[0].cells[j].paragraphs[0]
        _add_runs(p, cell.strip())
        for run in p.runs:
            run.bold = True
            run.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
    for row in data:
        cells = t.add_row().cells
        for j in range(len(header)):
            _add_runs(cells[j].paragraphs[0], (row[j] if j < len(row) else "").strip())
    doc.add_paragraph()


def convert(md_path: str, out_path: str) -> None:
    doc = Document()
    for s in doc.sections:
        s.left_margin = s.right_margin = Inches(0.8)
        s.top_margin = s.bottom_margin = Inches(0.6)
        _add_page_number(s.footer.paragraphs[0])
    normal = doc.styles["Normal"].font
    normal.name = "Calibri"
    normal.size = Pt(9)
    normal.color.rgb = RGBColor(0x24, 0x2B, 0x31)
    for name, size, color in (("Title", 22, ACCENT), ("Heading 1", 14, ACCENT), ("Heading 2", 11, ACCENT), ("Heading 3", 10, ACCENT)):
        style = doc.styles[name]
        style.font.name = "Calibri"
        style.font.size = Pt(size)
        style.font.bold = True
        style.font.color.rgb = color
        style.paragraph_format.space_before = Pt(7 if name != "Title" else 0)
        style.paragraph_format.space_after = Pt(3)

    lines = open(md_path, encoding="utf-8").read().splitlines()
    table: list[list[str]] = []
    for raw in lines:
        line = raw.rstrip()
        if line.startswith("|") and line.endswith("|"):
            table.append([c for c in line.strip("|").split("|")])
            continue
        if table:
            _flush_table(doc, table)
            table = []

        if not line.strip() or line.strip() == "---":
            continue
        m = re.match(r"^(#{1,3})\s+(.*)$", line)
        if m:
            doc.add_heading(m.group(2).strip(), level=len(m.group(1)))
            continue
        m = re.match(r"^!\[(.*?)\]\((.*?)\)$", line)
        if m:
            path = os.path.join(os.path.dirname(md_path), m.group(2))
            if os.path.isfile(path):
                w, h = _img_size(path)
                doc.add_picture(path, width=w, height=h)
                doc.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
                cap = doc.add_paragraph()
                cap.alignment = WD_ALIGN_PARAGRAPH.CENTER
                r = cap.add_run(m.group(1)); r.italic = True; r.font.size = Pt(8)
                r.font.color.rgb = RGBColor(0x55, 0x55, 0x55)
            continue
        if line.startswith("> "):
            p = doc.add_paragraph(style="Intense Quote")
            _add_runs(p, line[2:])
            continue
        if re.match(r"^[-*]\s+", line):
            p = doc.add_paragraph(style="List Bullet")
            _add_runs(p, re.sub(r"^[-*]\s+", "", line))
            continue
        _add_runs(doc.add_paragraph(), line)
    if table:
        _flush_table(doc, table)

    doc.save(out_path)
    print(f"wrote {out_path}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="md", default=os.path.join(ROOT, "docs/TECHNICAL_REPORT.md"))
    ap.add_argument("--out", default=os.path.join(ROOT, "docs/TECHNICAL_REPORT.docx"))
    a = ap.parse_args()
    convert(a.md, a.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
