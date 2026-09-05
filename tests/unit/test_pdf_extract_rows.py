"""A specification table's sub-rows must not be merged into one line.

pdfplumber calls two characters the same line when they sit within
``y_tolerance`` points of each other, and then sorts that line by x. On a tight
table — Vishay prints RDS(on) at TJ = 25 C and TJ = 125 C two points apart —
the default tolerance merges the two rows and INTERLEAVES them character by
character, so the reading came out as "T T J J = = 1 1 7 2 5 5 C C 0 0 . . ...".
No reader can recover the right number from that, and two passes of the
librarian's datasheet model returned different values every time, which the
agreement check then correctly refused. The part was unsourceable for a number
printed plainly on its datasheet.
"""

from __future__ import annotations

from pathlib import Path

from heaviside.pipeline.pdf_extract import extract_pdf_text


def _pdf(rows: list[tuple[float, float, str]]) -> bytes:
    """A one-page PDF placing each (x, y, text) with Helvetica 8."""
    draw = "\n".join(
        f"BT /F1 8 Tf {x:.2f} {y:.2f} Td ({t}) Tj ET" for x, y, t in rows)
    stream = draw.encode("latin-1")
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += (b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n"
            % (len(objs) + 1, xref))
    return bytes(out)


# two sub-rows of one table cell, 2 points apart, each in two columns —
# the shape that produced the interleave
_ROWS = [
    (100, 502, "VGS=10V TJ=25C"), (300, 502, "0.022"),
    (100, 500, "VGS=10V TJ=125C"), (300, 500, "0.037"),
]


def _write(tmp_path: Path) -> Path:
    p = tmp_path / "table.pdf"
    p.write_bytes(_pdf(_ROWS))
    return p


def test_tight_sub_rows_stay_apart_at_a_small_y_tolerance(tmp_path):
    text = extract_pdf_text(_write(tmp_path), y_tolerance=1)
    lines = [ln.strip() for ln in text.splitlines()
             if "VGS" in ln]
    assert len(lines) == 2, text
    hot = next(ln for ln in lines if "125C" in ln)
    cold = next(ln for ln in lines if "25C" in ln and "125C" not in ln)
    assert cold.endswith("0.022"), cold
    assert hot.endswith("0.037"), hot


def test_the_default_tolerance_really_does_interleave_them(tmp_path):
    """The counter-check: without the fix the rows come back shredded.

    If this ever stops holding, the test above is passing for a reason that has
    nothing to do with the bug it was written for.
    """
    text = extract_pdf_text(_write(tmp_path))
    body = [ln for ln in text.splitlines() if "GS" in ln]
    assert len(body) == 1, text          # two rows became one
    # and its characters are the two rows shuffled together, so neither
    # printed value survives intact
    assert "VVGGSS" in body[0], body[0]
    assert "0.022" not in body[0] and "0.037" not in body[0], body[0]
