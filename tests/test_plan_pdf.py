"""The document pass finds page text and red spans and rejects long documents."""

import pytest
from fictional_pdf import build_pdf

from training_ai.plan_pdf import (
    DocumentTooLong,
    DocumentUnreadable,
    analyze,
    is_red,
    page_count,
)


def test_red_spans_carry_page_and_position_and_text_is_page_tagged():
    pdf = build_pdf(
        [
            [("Weekly calendar", False), ("Thu: intervals", True), ("Fri: easy run", False)],
            [("Sat: long ride", True), ("Sun: rest", False)],
            [],
        ]
    )
    result = analyze(pdf, "application/pdf")
    assert result.page_count == 3
    assert result.pages[0].splitlines()[1] == "Thu: intervals" and result.pages[2] == ""
    assert [(s.page, s.text) for s in result.red_spans] == [
        (1, "Thu: intervals"),
        (2, "Sat: long ride"),
    ]
    assert result.red_spans[0].top < result.red_spans[0].top + 1  # positions are numbers
    assert result.tagged_text().startswith("[Page 1]\nWeekly calendar")
    assert "[Page 3]" not in result.tagged_text()


def test_images_get_no_text_pass_and_long_documents_are_rejected():
    assert analyze(b"\x89PNG not a pdf", "image/png").pages == []
    long_pdf = build_pdf([[("page", False)]] * 41)
    assert page_count(long_pdf) == 41
    with pytest.raises(DocumentTooLong, match="41 pages"):
        analyze(long_pdf, "application/pdf")
    assert analyze(build_pdf([[("page", False)]] * 40), "application/pdf").page_count == 40


@pytest.mark.parametrize(
    "color, expected",
    [
        ((1, 0, 0), True),
        ((0.8, 0.2, 0.1), True),
        ((0.6, 0.5, 0.5), False),
        ((0, 0, 0), False),
        ((0.5,), False),
        ((0, 1, 1, 0), True),
        ((0, 0, 0, 1), False),
        (None, False),
    ],
)
def test_red_dominant_colors(color, expected):
    assert is_red(color) is expected


@pytest.mark.parametrize("data", [b"%PDF-", b"%PDF-1.7\nnot a pdf\n", b"%PDF-1.4\n1 0 obj\n<<"])
def test_unreadable_pdfs_are_reported_as_such(data):
    with pytest.raises(DocumentUnreadable, match="damaged or password-protected"):
        analyze(data, "application/pdf")
