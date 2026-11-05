"""Deterministic document pass before extraction: page count, page text and red-hued spans."""

from __future__ import annotations

import io
from dataclasses import dataclass, field

import pdfplumber

MAX_PAGES = 40


class DocumentTooLong(Exception):
    def __init__(self, pages: int):
        self.pages = pages
        super().__init__(f"The document has {pages} pages; at most {MAX_PAGES} are accepted.")


class DocumentUnreadable(Exception):
    """A damaged or password-protected PDF."""

    def __init__(self, file_name: str = "The document"):
        super().__init__(f"{file_name} cannot be read; it may be damaged or password-protected.")


@dataclass(frozen=True)
class RedSpan:
    page: int  # 1-based
    text: str
    top: float
    x0: float


@dataclass(frozen=True)
class DocumentPass:
    page_count: int
    pages: list[str] = field(default_factory=list)  # Text per page; empty for image-only pages.
    red_spans: list[RedSpan] = field(default_factory=list)

    def tagged_text(self) -> str:
        return "\n\n".join(f"[Page {n}]\n{text}" for n, text in enumerate(self.pages, 1) if text)


def is_red(color) -> bool:
    """Any red-dominant fill: RGB with red well above green and blue, or CMYK magenta+yellow."""
    if not color:
        return False
    values = list(color)
    if len(values) == 3:
        r, g, b = values
        return r > 0.5 and r - g > 0.25 and r - b > 0.25
    if len(values) == 4:
        c, m, y, k = values
        return m > 0.5 and y > 0.3 and c < 0.3 and k < 0.5
    return False


def page_count(data: bytes) -> int:
    with pdfplumber.open(io.BytesIO(data)) as document:
        return len(document.pages)


def analyze(data: bytes, media_type: str) -> DocumentPass:
    """Text and red spans of a PDF; images get no text pass and go to the model as they are."""
    if media_type != "application/pdf":
        return DocumentPass(1)
    try:
        return _analyze_pdf(data)
    except DocumentTooLong:
        raise
    except Exception as exc:  # pdfminer raises many unrelated types on malformed input.
        raise DocumentUnreadable() from exc


def _analyze_pdf(data: bytes) -> DocumentPass:
    pages: list[str] = []
    spans: list[RedSpan] = []
    with pdfplumber.open(io.BytesIO(data)) as document:
        if len(document.pages) > MAX_PAGES:
            raise DocumentTooLong(len(document.pages))
        for number, page in enumerate(document.pages, 1):
            pages.append(page.extract_text() or "")
            current: list[dict] = []
            for word in page.extract_words(extra_attrs=["non_stroking_color"]):
                red = is_red(word.get("non_stroking_color"))
                same_line = current and abs(current[-1]["top"] - word["top"]) < 2
                if red and same_line:
                    current.append(word)
                    continue
                if current:
                    spans.append(_span(number, current))
                    current = []
                if red:
                    current = [word]
            if current:
                spans.append(_span(number, current))
    return DocumentPass(len(pages), pages, spans)


def _span(page: int, words: list[dict]) -> RedSpan:
    return RedSpan(
        page=page,
        text=" ".join(w["text"] for w in words),
        top=round(words[0]["top"], 1),
        x0=round(words[0]["x0"], 1),
    )
