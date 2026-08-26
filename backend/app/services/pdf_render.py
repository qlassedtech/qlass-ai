import io

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    BaseDocTemplate, PageTemplate, Frame, Paragraph, Spacer, HRFlowable, PageBreak, NextPageTemplate, Table, TableStyle,
)

from app.config import REPO_ROOT

ACCENT = colors.HexColor("#2b3ec4")
ACCENT_DARK = colors.HexColor("#1c2a8f")
TEXT = colors.HexColor("#161829")
MUTED = colors.HexColor("#6b7089")
FOOTER_MUTED = colors.HexColor("#9497ab")
RULE = colors.HexColor("#e8e9f3")

PAGE_W, PAGE_H = A4
HEADER_H = 34 * mm
FOOTER_H = 16 * mm
MARGIN = 20 * mm
BODY_GAP = 8 * mm  # breathing room between the header/footer bands and the actual content frame

_styles = getSampleStyleSheet()
_question_style = ParagraphStyle(
    "Question", parent=_styles["Normal"], fontName="Helvetica-Bold", fontSize=11.5,
    textColor=TEXT, spaceBefore=0, spaceAfter=6, leading=15,
)
_answer_style = ParagraphStyle(
    "Answer", parent=_styles["Normal"], fontName="Helvetica-Bold", fontSize=11.5,
    textColor=ACCENT_DARK, spaceBefore=0, spaceAfter=10, leading=15,
)


def _resolve_logo_path(logo_url: str | None) -> str | None:
    if not logo_url or not logo_url.startswith("/static/"):
        return None
    path = REPO_ROOT / "backend" / logo_url.lstrip("/")
    return str(path) if path.exists() else None


def _draw_header(canvas, school_name: str, heading: str, subtitle: str, logo_path: str | None):
    canvas.saveState()
    canvas.setFillColor(ACCENT)
    canvas.rect(0, PAGE_H - HEADER_H, PAGE_W, HEADER_H, fill=1, stroke=0)
    canvas.setFillColor(ACCENT_DARK)
    canvas.rect(0, PAGE_H - HEADER_H - 1.2 * mm, PAGE_W, 1.2 * mm, fill=1, stroke=0)

    text_x = MARGIN
    if logo_path:
        logo_size = 18 * mm
        logo_y = PAGE_H - HEADER_H / 2 - logo_size / 2
        canvas.setFillColor(colors.white)
        canvas.roundRect(MARGIN, logo_y, logo_size, logo_size, 3 * mm, fill=1, stroke=0)
        canvas.drawImage(
            logo_path, MARGIN + 1.2 * mm, logo_y + 1.2 * mm, logo_size - 2.4 * mm, logo_size - 2.4 * mm,
            preserveAspectRatio=True, mask="auto",
        )
        text_x = MARGIN + logo_size + 8 * mm

    canvas.setFillColor(colors.white)
    canvas.setFont("Helvetica-Bold", 16)
    canvas.drawString(text_x, PAGE_H - HEADER_H / 2 + 4 * mm, school_name)
    canvas.setFont("Helvetica", 10.5)
    canvas.setFillColorRGB(1, 1, 1, alpha=0.85)
    canvas.drawString(text_x, PAGE_H - HEADER_H / 2 - 2 * mm, heading)
    if subtitle:
        canvas.setFont("Helvetica", 9)
        canvas.setFillColorRGB(1, 1, 1, alpha=0.7)
        canvas.drawString(text_x, PAGE_H - HEADER_H / 2 - 7.5 * mm, subtitle)
    canvas.restoreState()


def _draw_footer(canvas, page_num: int):
    canvas.saveState()
    canvas.setStrokeColor(RULE)
    canvas.setLineWidth(0.6)
    canvas.line(MARGIN, FOOTER_H, PAGE_W - MARGIN, FOOTER_H)

    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(MUTED)
    canvas.drawString(MARGIN, FOOTER_H - 6 * mm, f"Page {page_num}")

    label_font, brand_font, size = "Helvetica", "Helvetica-Bold", 8
    label, brand = "Powered by ", "Skoolgpt"
    total_w = canvas.stringWidth(label, label_font, size) + canvas.stringWidth(brand, brand_font, size)
    start_x = (PAGE_W - total_w) / 2
    canvas.setFont(label_font, size)
    canvas.setFillColor(FOOTER_MUTED)
    canvas.drawString(start_x, FOOTER_H - 6 * mm, label)
    canvas.setFont(brand_font, size)
    canvas.setFillColor(ACCENT)
    canvas.drawString(start_x + canvas.stringWidth(label, label_font, size), FOOTER_H - 6 * mm, brand)
    canvas.restoreState()


def render_workbook_pdf(
    topic: str,
    class_: str | None,
    school_name: str,
    school_logo_url: str | None,
    questions: list[dict],
    include_answer_key: bool,
) -> bytes:
    logo_path = _resolve_logo_path(school_logo_url)
    subtitle = f"Class {class_} · Topic: {topic}" if class_ else f"Topic: {topic}"

    def worksheet_page(canvas, _doc):
        _draw_header(canvas, school_name, "Practice Worksheet", subtitle, logo_path)
        _draw_footer(canvas, canvas.getPageNumber())

    def answer_key_page(canvas, _doc):
        _draw_header(canvas, school_name, "Answer Key", subtitle, logo_path)
        _draw_footer(canvas, canvas.getPageNumber())

    buffer = io.BytesIO()
    doc = BaseDocTemplate(buffer, pagesize=A4)
    frame_top = PAGE_H - HEADER_H - BODY_GAP
    frame_bottom = FOOTER_H + BODY_GAP
    frame = Frame(MARGIN, frame_bottom, PAGE_W - 2 * MARGIN, frame_top - frame_bottom, id="body")
    doc.addPageTemplates([
        PageTemplate(id="worksheet", frames=[frame], onPage=worksheet_page),
        PageTemplate(id="answers", frames=[frame], onPage=answer_key_page),
    ])

    elements = [NextPageTemplate("worksheet")]
    for i, q in enumerate(questions, start=1):
        elements.append(Paragraph(f"{i}.  {q['question']}", _question_style))
        elements.append(Spacer(1, 6 * mm))
        elements.append(HRFlowable(width="100%", thickness=0.6, color=RULE))
        elements.append(Spacer(1, 10 * mm))

    if include_answer_key:
        elements.append(NextPageTemplate("answers"))
        elements.append(PageBreak())
        for i, q in enumerate(questions, start=1):
            elements.append(Paragraph(f"{i}.  {q['answer']}", _answer_style))

    doc.build(elements)
    return buffer.getvalue()


def render_school_statement_pdf(
    school_name: str,
    school_logo_url: str | None,
    period_label: str,
    opening_balance: float,
    closing_balance: float,
    total_topped_up: float,
    total_spent: float,
    spend_by_service: list[tuple[str, float]],
) -> bytes:
    """
    One-page monthly billing statement for a school's own credit_events
    ledger — opening/closing balance plus a per-service spend breakdown, so
    a school admin (or Qlass finance) can see where a month's spend went
    without querying the ledger directly.

    Amounts are prefixed "Rs." rather than "₹" — the base-14 Helvetica font
    has no Rupee glyph, so ₹ renders as a broken/substitute character in the
    actual PDF (confirmed by extracting text from a generated statement).
    Bundling a Unicode TTF font would fix it but isn't portable across the
    dev machine and the eventual OVH deployment target without shipping a
    font file, so plain ASCII is the safer choice here.
    """
    logo_path = _resolve_logo_path(school_logo_url)

    def statement_page(canvas, _doc):
        _draw_header(canvas, school_name, "Monthly Statement", period_label, logo_path)
        _draw_footer(canvas, canvas.getPageNumber())

    buffer = io.BytesIO()
    doc = BaseDocTemplate(buffer, pagesize=A4)
    frame_top = PAGE_H - HEADER_H - BODY_GAP
    frame_bottom = FOOTER_H + BODY_GAP
    frame = Frame(MARGIN, frame_bottom, PAGE_W - 2 * MARGIN, frame_top - frame_bottom, id="body")
    doc.addPageTemplates([PageTemplate(id="statement", frames=[frame], onPage=statement_page)])

    summary_style = ParagraphStyle(
        "Summary", parent=_styles["Normal"], fontName="Helvetica", fontSize=11, textColor=TEXT, leading=16,
    )
    label_style = ParagraphStyle(
        "SectionLabel", parent=_styles["Normal"], fontName="Helvetica-Bold", fontSize=12.5, textColor=ACCENT_DARK,
        spaceBefore=6 * mm, spaceAfter=3 * mm,
    )

    elements = [
        Paragraph("Account Summary", label_style),
        Table(
            [
                ["Opening balance", f"Rs. {opening_balance:,.2f}"],
                ["Credits topped up this period", f"Rs. {total_topped_up:,.2f}"],
                ["Credits spent this period", f"Rs. {total_spent:,.2f}"],
                ["Closing balance", f"Rs. {closing_balance:,.2f}"],
            ],
            colWidths=[100 * mm, 60 * mm],
            style=TableStyle([
                ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
                ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 10.5),
                ("TEXTCOLOR", (0, 0), (-1, -1), TEXT),
                ("LINEBELOW", (0, 0), (-1, -2), 0.4, RULE),
                ("LINEABOVE", (0, -1), (-1, -1), 0.8, ACCENT_DARK),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]),
        ),
        Paragraph("Spend by Service", label_style),
    ]

    if spend_by_service:
        rows = [["Service", "Amount"]] + [[svc, f"Rs. {amt:,.2f}"] for svc, amt in spend_by_service]
        elements.append(Table(
            rows,
            colWidths=[100 * mm, 60 * mm],
            style=TableStyle([
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
                ("FONTSIZE", (0, 0), (-1, -1), 10),
                ("TEXTCOLOR", (0, 0), (-1, -1), TEXT),
                ("BACKGROUND", (0, 0), (-1, 0), RULE),
                ("LINEBELOW", (0, 0), (-1, -1), 0.4, RULE),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]),
        ))
    else:
        elements.append(Paragraph("No usage this period.", summary_style))

    doc.build(elements)
    return buffer.getvalue()
