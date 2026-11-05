"""Hand-built fictional plan PDFs for tests: text lines per page, red or black, no extra library."""

from __future__ import annotations

# A line is (text, red); a page is a list of lines.
Line = tuple[str, bool]


def _escape(text: str) -> bytes:
    return (
        text.encode("latin-1", "replace")
        .replace(b"\\", b"\\\\")
        .replace(b"(", b"\\(")
        .replace(b")", b"\\)")
    )


def _page_stream(lines: list[Line]) -> bytes:
    parts = [b"BT /F1 12 Tf 72 740 Td 16 TL"]
    for text, red in lines:
        parts.append(b"1 0 0 rg" if red else b"0 0 0 rg")
        parts.append(b"(" + _escape(text) + b") Tj T*")
    parts.append(b"ET")
    return b"\n".join(parts)


def build_pdf(pages: list[list[Line]]) -> bytes:
    """A PDF whose pages carry the given lines; an empty page list makes an image-free blank page."""
    objects: list[bytes] = [b"<</Type/Catalog/Pages 2 0 R>>"]
    font_index = 3
    page_indices = [4 + 2 * i for i in range(len(pages))]
    kids = b" ".join(b"%d 0 R" % i for i in page_indices)
    objects.append(b"<</Type/Pages/Kids[" + kids + b"]/Count %d>>" % len(pages))
    objects.append(b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica/Encoding/WinAnsiEncoding>>")
    for index, lines in zip(page_indices, pages, strict=True):
        stream = _page_stream(lines)
        objects.append(
            b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]/Contents %d 0 R"
            b"/Resources<</Font<</F1 %d 0 R>>>>>>" % (index + 1, font_index)
        )
        objects.append(b"<</Length %d>>\nstream\n" % len(stream) + stream + b"\nendstream")
    out = b"%PDF-1.4\n"
    offsets = []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    out += b"trailer<</Size %d/Root 1 0 R>>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    return out


def plan_pdf(weeks: int = 2, key_days: tuple[str, ...] = ("Thu",)) -> bytes:
    """A small fictional block: a weekly table page with red key sessions, then daily pages."""
    table: list[Line] = [("Fictional Block 3 - base period", False), ("Weekly calendar", False)]
    for week in range(1, weeks + 1):
        for day in ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"):
            table.append((f"W{week} {day}: swim/bike/run", day in key_days))
    days: list[Line] = [
        (f"Week {week}: Thu intervals 5 x 4 min, Sat long ride 3 h", False)
        for week in range(1, weeks + 1)
    ]
    return build_pdf([table, days])
