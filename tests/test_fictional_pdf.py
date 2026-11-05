"""The fictional PDF helper produces documents that pdfplumber reads back as written."""

import io

import pdfplumber
from fictional_pdf import build_pdf, plan_pdf


def test_pages_text_and_colors_read_back():
    pdf = build_pdf([[("Week 1", False), ("Thu: intervals", True)], [("Week 2", False)], []])
    with pdfplumber.open(io.BytesIO(pdf)) as document:
        assert len(document.pages) == 3
        assert document.pages[0].extract_text().splitlines() == ["Week 1", "Thu: intervals"]
        assert document.pages[1].extract_text() == "Week 2"
        assert document.pages[2].extract_text() == ""
        colors = {c["text"]: tuple(c["non_stroking_color"]) for c in document.pages[0].chars}
        assert colors["W"] == (0, 0, 0) and colors["T"] == (1, 0, 0)


def test_plan_pdf_has_table_and_day_pages():
    with pdfplumber.open(io.BytesIO(plan_pdf(weeks=3))) as document:
        assert len(document.pages) == 2
        assert "W3 Sun" in document.pages[0].extract_text()
        assert "Week 3" in document.pages[1].extract_text()
