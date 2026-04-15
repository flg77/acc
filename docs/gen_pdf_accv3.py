"""
Generate ACCv3.pdf from ACCv3.md using the shared parser in gen_pdf.py.
Run: python docs/gen_pdf_accv3.py
"""

from pathlib import Path
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate

import gen_pdf  # reuse styles, parser, dark_page

DOCS_DIR = Path(__file__).parent
MD_FILE  = DOCS_DIR / "ACCv3.md"
PDF_FILE = DOCS_DIR / "ACCv3.pdf"

TITLE    = "Agentic Cell Corpus v3 — Sovereign, Edge-First Agentic Computing"
AUTHOR   = "Michael"
SUBJECT  = "ACCv3 Draft Paper"
FOOTER   = "Agentic Cell Corpus v3 — Draft for reviewer circulation"


def accv3_page(canvas, doc):
    """Dark page background + ACCv3-specific footer."""
    canvas.saveState()
    canvas.setFillColor(gen_pdf.C_PAGE_BG)
    canvas.rect(0, 0, A4[0], A4[1], fill=1, stroke=0)
    canvas.setFont("Helvetica", 7)
    canvas.setFillColor(gen_pdf.C_BORDER)
    canvas.drawString(1.5 * cm, 0.8 * cm, FOOTER)
    canvas.drawRightString(A4[0] - 1.5 * cm, 0.8 * cm, f"Page {doc.page}")
    canvas.restoreState()


def main():
    md_text = MD_FILE.read_text(encoding="utf-8")

    doc = SimpleDocTemplate(
        str(PDF_FILE),
        pagesize=A4,
        leftMargin=1.5 * cm, rightMargin=1.5 * cm,
        topMargin=2 * cm,    bottomMargin=2 * cm,
        title=TITLE,
        author=AUTHOR,
        subject=SUBJECT,
    )

    story = gen_pdf.parse_md(md_text)
    doc.build(story, onFirstPage=accv3_page, onLaterPages=accv3_page)
    print(f"PDF written to: {PDF_FILE}")


if __name__ == "__main__":
    main()
