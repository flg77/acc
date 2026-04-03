"""
Generate IMPLEMENTATION_SPEC_v0.1.0.pdf from the markdown source.
Run: python docs/gen_pdf.py
"""

import re
from pathlib import Path
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.lib import colors
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Preformatted,
    HRFlowable, Table, TableStyle, PageBreak
)
from reportlab.lib.enums import TA_LEFT, TA_CENTER

# ── Paths ──────────────────────────────────────────────────────────────────
DOCS_DIR = Path(__file__).parent
MD_FILE  = DOCS_DIR / "IMPLEMENTATION_SPEC_v0.1.0.md"
PDF_FILE = DOCS_DIR / "IMPLEMENTATION_SPEC_v0.1.0.pdf"

# ── Colour palette ─────────────────────────────────────────────────────────
C_BG_CODE  = colors.HexColor("#1e1e2e")
C_FG_CODE  = colors.HexColor("#cdd6f4")
C_BORDER   = colors.HexColor("#45475a")
C_H1       = colors.HexColor("#89b4fa")
C_H2       = colors.HexColor("#89dceb")
C_H3       = colors.HexColor("#a6e3a1")
C_H4       = colors.HexColor("#fab387")
C_BODY     = colors.HexColor("#cdd6f4")
C_PAGE_BG  = colors.HexColor("#11111b")
C_TABLE_H  = colors.HexColor("#313244")
C_TABLE_R  = colors.HexColor("#1e1e2e")
C_TABLE_A  = colors.HexColor("#181825")

# ── Styles ─────────────────────────────────────────────────────────────────
base = getSampleStyleSheet()

def make_style(name, parent_name="Normal", **kwargs):
    parent = base[parent_name]
    return ParagraphStyle(name=name, parent=parent, **kwargs)

S_BODY   = make_style("AccBody",   fontSize=9,  leading=14, textColor=C_BODY,
                       fontName="Helvetica")
S_H1     = make_style("AccH1",     fontSize=18, leading=22, textColor=C_H1,
                       fontName="Helvetica-Bold", spaceAfter=8, spaceBefore=16)
S_H2     = make_style("AccH2",     fontSize=14, leading=18, textColor=C_H2,
                       fontName="Helvetica-Bold", spaceAfter=6, spaceBefore=12)
S_H3     = make_style("AccH3",     fontSize=11, leading=15, textColor=C_H3,
                       fontName="Helvetica-Bold", spaceAfter=4, spaceBefore=10)
S_H4     = make_style("AccH4",     fontSize=10, leading=14, textColor=C_H4,
                       fontName="Helvetica-BoldOblique", spaceAfter=3, spaceBefore=8)
S_CODE   = make_style("AccCode",   fontSize=7.5, leading=11, textColor=C_FG_CODE,
                       fontName="Courier", leftIndent=0, backColor=C_BG_CODE,
                       borderPadding=(4, 6, 4, 6))
S_META   = make_style("AccMeta",   fontSize=8,  leading=12, textColor=C_BORDER,
                       fontName="Helvetica-Oblique")
S_TOC    = make_style("AccToc",    fontSize=9,  leading=14, textColor=C_H2,
                       fontName="Helvetica", leftIndent=0.4*cm)
S_TOC_H  = make_style("AccTocH",   fontSize=10, leading=14, textColor=C_H1,
                       fontName="Helvetica-Bold")
S_HR     = make_style("AccHR",     fontSize=1)

# ── Helpers ────────────────────────────────────────────────────────────────
def esc(text: str) -> str:
    """Escape XML special chars for ReportLab Paragraph."""
    return (text
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;"))


def code_block(lines: list[str]) -> list:
    """Render a fenced code block as a dark Preformatted block."""
    text = "\n".join(lines)
    return [Preformatted(text, S_CODE), Spacer(1, 6)]


def table_from_md(header: list[str], rows: list[list[str]]) -> Table:
    """Build a styled ReportLab Table from parsed markdown table data."""
    col_count = len(header)
    col_width  = (A4[0] - 3*cm) / col_count

    data = [header] + rows
    t = Table(data, colWidths=[col_width]*col_count, repeatRows=1)
    style = TableStyle([
        # Header row
        ("BACKGROUND",   (0, 0), (-1, 0),  C_TABLE_H),
        ("TEXTCOLOR",    (0, 0), (-1, 0),  C_H2),
        ("FONTNAME",     (0, 0), (-1, 0),  "Helvetica-Bold"),
        ("FONTSIZE",     (0, 0), (-1, 0),  8),
        ("BOTTOMPADDING",(0, 0), (-1, 0),  6),
        ("TOPPADDING",   (0, 0), (-1, 0),  6),
        # Body rows — alternating
        ("FONTNAME",     (0, 1), (-1, -1), "Helvetica"),
        ("FONTSIZE",     (0, 1), (-1, -1), 8),
        ("TEXTCOLOR",    (0, 1), (-1, -1), C_BODY),
        ("TOPPADDING",   (0, 1), (-1, -1), 4),
        ("BOTTOMPADDING",(0, 1), (-1, -1), 4),
        ("ROWBACKGROUNDS",(0,1),(-1,-1),   [C_TABLE_R, C_TABLE_A]),
        # Grid
        ("GRID",         (0, 0), (-1, -1), 0.5, C_BORDER),
        ("VALIGN",       (0, 0), (-1, -1), "TOP"),
        ("WORDWRAP",     (0, 0), (-1, -1), True),
    ])
    t.setStyle(style)
    return t


def dark_page(canvas, doc):
    """Page background + footer."""
    canvas.saveState()
    canvas.setFillColor(C_PAGE_BG)
    canvas.rect(0, 0, A4[0], A4[1], fill=1, stroke=0)
    # Footer
    canvas.setFont("Helvetica", 7)
    canvas.setFillColor(C_BORDER)
    canvas.drawString(1.5*cm, 0.8*cm,
                      "Agentic Cell Corpus — Implementation Specification v0.1.0")
    canvas.drawRightString(A4[0]-1.5*cm, 0.8*cm, f"Page {doc.page}")
    canvas.restoreState()


# ── Markdown Parser ────────────────────────────────────────────────────────
def parse_md(md_text: str) -> list:
    """Convert markdown text to a list of ReportLab flowables."""
    flowables = []
    lines = md_text.splitlines()
    i = 0

    def flush_paragraph(buf):
        text = " ".join(buf).strip()
        if text:
            # Handle inline code
            text = re.sub(r'`([^`]+)`',
                          lambda m: f'<font name="Courier" color="#f38ba8">{esc(m.group(1))}</font>',
                          esc(text))
            # Handle bold
            text = re.sub(r'\*\*([^*]+)\*\*',
                          lambda m: f'<b>{m.group(1)}</b>', text)
            # Handle italic
            text = re.sub(r'\*([^*]+)\*',
                          lambda m: f'<i>{m.group(1)}</i>', text)
            flowables.append(Paragraph(text, S_BODY))
            flowables.append(Spacer(1, 4))

    para_buf = []
    in_code  = False
    code_buf = []
    in_table = False
    table_header = []
    table_rows   = []

    while i < len(lines):
        line = lines[i]

        # --- Fenced code block ---
        if line.strip().startswith("```"):
            if in_code:
                flowables.extend(code_block(code_buf))
                code_buf = []
                in_code  = False
            else:
                flush_paragraph(para_buf)
                para_buf = []
                in_code  = True
            i += 1
            continue

        if in_code:
            code_buf.append(line)
            i += 1
            continue

        # --- Horizontal rule ---
        if re.match(r'^---+\s*$', line):
            flush_paragraph(para_buf)
            para_buf = []
            flowables.append(Spacer(1, 6))
            flowables.append(HRFlowable(width="100%", thickness=0.5,
                                        color=C_BORDER))
            flowables.append(Spacer(1, 6))
            i += 1
            continue

        # --- Headings ---
        h_match = re.match(r'^(#{1,4})\s+(.*)', line)
        if h_match:
            flush_paragraph(para_buf)
            para_buf = []
            level = len(h_match.group(1))
            text  = esc(h_match.group(2))
            # Strip markdown link anchors like {#anchor}
            text  = re.sub(r'\{#[^}]+\}', '', text).strip()
            style = {1: S_H1, 2: S_H2, 3: S_H3, 4: S_H4}.get(level, S_H4)
            if level == 1:
                flowables.append(PageBreak())
            flowables.append(Paragraph(text, style))
            i += 1
            continue

        # --- Table ---
        if line.startswith("|"):
            flush_paragraph(para_buf)
            para_buf = []
            # Collect table lines
            t_lines = []
            while i < len(lines) and lines[i].startswith("|"):
                t_lines.append(lines[i])
                i += 1
            # Parse header, separator, rows
            if len(t_lines) >= 2:
                def split_row(r):
                    cells = [c.strip() for c in r.strip("|").split("|")]
                    return cells
                header = split_row(t_lines[0])
                body   = [split_row(r) for r in t_lines[2:] if r.strip()]
                # Wrap cells as Paragraphs
                h_para = [Paragraph(f"<b>{esc(c)}</b>", S_BODY) for c in header]
                b_para = [[Paragraph(esc(c), S_BODY) for c in row] for row in body]
                flowables.append(table_from_md(h_para, b_para))
                flowables.append(Spacer(1, 8))
            continue

        # --- Blank line = paragraph break ---
        if not line.strip():
            flush_paragraph(para_buf)
            para_buf = []
            i += 1
            continue

        # --- Bullet list item ---
        bullet_match = re.match(r'^(\s*)[-*]\s+(.*)', line)
        if bullet_match:
            flush_paragraph(para_buf)
            para_buf = []
            indent = len(bullet_match.group(1))
            text   = bullet_match.group(2)
            # inline formatting
            text = re.sub(r'`([^`]+)`',
                          lambda m: f'<font name="Courier" color="#f38ba8">{esc(m.group(1))}</font>',
                          esc(text))
            text = re.sub(r'\*\*([^*]+)\*\*',
                          lambda m: f'<b>{m.group(1)}</b>', text)
            bullet_style = ParagraphStyle(
                "Bullet", parent=S_BODY,
                leftIndent=(1 + indent//2)*cm, bulletIndent=0.5*cm,
                spaceAfter=2
            )
            flowables.append(Paragraph(f"\u2022 {text}", bullet_style))
            i += 1
            continue

        # --- Numbered list ---
        num_match = re.match(r'^(\s*)\d+\.\s+(.*)', line)
        if num_match:
            flush_paragraph(para_buf)
            para_buf = []
            text = num_match.group(2)
            text = re.sub(r'`([^`]+)`',
                          lambda m: f'<font name="Courier" color="#f38ba8">{esc(m.group(1))}</font>',
                          esc(text))
            text = re.sub(r'\*\*([^*]+)\*\*',
                          lambda m: f'<b>{m.group(1)}</b>', text)
            num_style = ParagraphStyle(
                "Num", parent=S_BODY, leftIndent=1*cm, spaceAfter=2
            )
            flowables.append(Paragraph(f"\u2022 {text}", num_style))
            i += 1
            continue

        # --- Metadata / italic lines (leading *) ---
        if line.startswith("*") and line.endswith("*") and line.count("*") == 2:
            flush_paragraph(para_buf)
            para_buf = []
            flowables.append(Paragraph(esc(line.strip("*")), S_META))
            flowables.append(Spacer(1, 4))
            i += 1
            continue

        # --- Regular paragraph text ---
        para_buf.append(line)
        i += 1

    flush_paragraph(para_buf)
    return flowables


# ── Main ───────────────────────────────────────────────────────────────────
def main():
    md_text = MD_FILE.read_text(encoding="utf-8")

    doc = SimpleDocTemplate(
        str(PDF_FILE),
        pagesize=A4,
        leftMargin=1.5*cm, rightMargin=1.5*cm,
        topMargin=2*cm,    bottomMargin=2*cm,
        title="Agentic Cell Corpus — Implementation Specification v0.1.0",
        author="Michael",
        subject="ACC Implementation Specification",
    )

    story = parse_md(md_text)
    doc.build(story, onFirstPage=dark_page, onLaterPages=dark_page)
    print(f"PDF written to: {PDF_FILE}")


if __name__ == "__main__":
    main()
