"""Rebuild deck slides 10, 11 and 12 as native, editable PowerPoint text.

    python presentations/build_slides_10_12_native.py

All three used to be a single generated PNG.  They are now real text boxes, real
tables and real rounded panels, so every number can be edited in PowerPoint.
The copy is cut to what has to be read from the back of a room — roughly 55 words
per panel — and the explanations live in the spoken script instead.

Style follows the deck's own convention rather than python-pptx defaults: size,
weight, colour and typeface live in ``a:pPr/a:defRPr`` and the runs carry no
``rPr``.  This matters because nothing useful is inherited here — the master's
body font (Golos Text) is not installed on this machine, so a bare ``add_textbox``
renders 18 pt Calibri.  Mixed-size paragraphs are the one exception and use
run-level formatting.

The deck has no native tables, so the single table below neutralises PowerPoint's
default table style explicitly: this deck's ``accent1`` is ``EC0B43``, so the
inherited "Medium Style 2" would render crimson banding.

Idempotent: every shape except the white card and the title is dropped and
rebuilt, so the script can be re-run after editing the copy here.  Close
PowerPoint first — it holds the pre-edit copy in memory and will clobber the
write.
"""

from __future__ import annotations

import argparse
import copy
from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE, MSO_SHAPE_TYPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Inches, Pt

ROOT = Path(__file__).resolve().parents[1]
DECK = ROOT / "presentations" / "BigData_ITMO.pptx"

SLIDE_A = "Part 4 — PySpark: Windows vs Cluster"
SLIDE_B = "Part 5 — It was measuring start-up"
SLIDE_C = "Part 6 — The overall effect on the site"

FONT = "Arial"
INK = "121212"        # headings and body
GREY = "565656"       # captions and footnotes
BLUE = "1F77B4"       # left panel accent (deck accent, slides 2/3/6)
VIOLET = "8F00FF"     # right panel accent (deck accent, slide 13 cards)
MAGENTA = "C026D3"    # the deck's "result" colour, used for the closing lines

# The white card the panels sit on, and the band we lay out inside it.
CARD_LEFT, CARD_TOP = 0.42, 1.02
COL_L, COL_R, COL_W = 0.62, 5.08, 4.30
PANEL_TOP, PANEL_H = 1.50, 2.92   # panels end at 4.42
FOOT_TOP, FOOT_H = 4.48, 0.70     # closing lines end at 5.18, inside the card


# --------------------------------------------------------------- helpers --
def _def_rpr(paragraph, size, bold, colour):
    """Write the deck's a:pPr/a:defRPr styling; runs then need no rPr."""
    pPr = paragraph._p.get_or_add_pPr()
    for old in pPr.findall(qn("a:defRPr")):
        pPr.remove(old)
    defRPr = pPr.makeelement(qn("a:defRPr"), {})
    defRPr.set("sz", str(int(round(size * 100))))
    defRPr.set("b", "1" if bold else "0")
    fill = defRPr.makeelement(qn("a:solidFill"), {})
    clr = fill.makeelement(qn("a:srgbClr"), {"val": colour})
    fill.append(clr)
    defRPr.append(fill)
    defRPr.append(defRPr.makeelement(qn("a:latin"), {"typeface": FONT}))
    pPr.append(defRPr)  # defRPr must be the last child of pPr


def _write(text_frame, paras, align=PP_ALIGN.LEFT, gap=0):
    """Fill a text frame. Each para is (text, size, bold, colour) or a list of
    (text, size, bold, colour) run tuples for a mixed-size line. ``gap`` is the
    space before every paragraph after the first, in points."""
    text_frame.word_wrap = True
    for i, spec in enumerate(paras):
        p = text_frame.paragraphs[0] if i == 0 else text_frame.add_paragraph()
        p.alignment = align
        if i and gap:
            p.space_before = Pt(gap)
        if isinstance(spec, list):
            # mixed sizes in one line -> run-level formatting
            for text, size, bold, colour in spec:
                r = p.add_run()
                r.text = text
                r.font.name = FONT
                r.font.size = Pt(size)
                r.font.bold = bold
                r.font.color.rgb = RGBColor.from_string(colour)
        else:
            text, size, bold, colour = spec
            _def_rpr(p, size, bold, colour)
            p.add_run().text = text


def _textbox(slide, left, top, width, height, paras, align=PP_ALIGN.LEFT, gap=0):
    box = slide.shapes.add_textbox(Inches(left), Inches(top), Inches(width), Inches(height))
    box.fill.background()
    box.line.fill.background()
    tf = box.text_frame
    for side in ("left", "right", "top", "bottom"):
        setattr(tf, f"margin_{side}", Inches(0.03))
    _write(tf, paras, align, gap)
    return box


def _panel(slide, left, top, width, height, accent, paras=(), gap=0):
    shape = slide.shapes.add_shape(
        MSO_SHAPE.ROUNDED_RECTANGLE, Inches(left), Inches(top), Inches(width), Inches(height)
    )
    shape.adjustments[0] = 0.04  # matches the host card's <a:gd name="adj" val="4000"/>
    shape.fill.solid()
    shape.fill.fore_color.rgb = RGBColor.from_string("FFFFFF")
    shape.line.color.rgb = RGBColor.from_string(accent)
    shape.line.width = Pt(1.2)
    shape.shadow.inherit = False
    tf = shape.text_frame
    tf.vertical_anchor = MSO_ANCHOR.TOP
    tf.margin_left = tf.margin_right = Inches(0.08)
    tf.margin_top = tf.margin_bottom = Inches(0.05)
    if paras:
        _write(tf, paras, gap=gap)
    return shape


def _table(slide, left, top, width, height, rows, col_widths):
    frame = slide.shapes.add_table(
        len(rows), len(rows[0]), Inches(left), Inches(top), Inches(width), Inches(height)
    )
    tbl = frame.table
    tbl.first_row = tbl.horz_banding = tbl.first_col = tbl.vert_banding = False
    for col, w in zip(tbl.columns, col_widths):
        col.width = Inches(w)
    for r, row in enumerate(rows):
        for c, text in enumerate(row):
            cell = tbl.cell(r, c)
            cell.fill.solid()
            cell.fill.fore_color.rgb = RGBColor.from_string("FFFFFF")
            cell.margin_left = cell.margin_right = Inches(0.06)
            cell.margin_top = cell.margin_bottom = Inches(0.03)
            cell.vertical_anchor = MSO_ANCHOR.MIDDLE
            p = cell.text_frame.paragraphs[0]
            p.alignment = PP_ALIGN.LEFT if c == 0 else PP_ALIGN.RIGHT
            run = p.add_run()
            run.text = text
            run.font.name = FONT
            run.font.size = Pt(10.5)
            run.font.bold = r == 0
            run.font.color.rgb = RGBColor.from_string(GREY if r == 0 else INK)
    return frame


# ----------------------------------------------------------- slide bodies --
def build_slide_a(slide):
    """Part 4 — the measurement, and the two mechanisms behind it."""

    _textbox(slide, COL_L, 1.16, 8.76, 0.30, [(
        "Same job on Windows: 2 → 11.4 s  ·  4 → 21.6 s  ·  12 → 66.8 s. "
        "More splitting, slower.", 12, True, INK)])

    _panel(slide, COL_L, PANEL_TOP, COL_W, PANEL_H, BLUE, gap=4, paras=[
        ("Why the cluster is faster", 14, True, INK),
        ("4.0 s   ·   16.7×", 22, True, BLUE),
        ("12 pieces × 4 steps = 48 tasks, on 4 cluster cores", 10.5, False, GREY),
        ("Each task needs its own Python process. Linux copies a running one; Windows "
         "builds a fresh one — ~1.25 s each, and Spark starts them one at a time.",
         11, False, INK),
        ("48 × 1.25 s ≈ 60 of the 66.8 s.", 11, True, INK),
        ("Controls: 12.8 s on 1, 4 or 12 cores; worker.reuse=false changes nothing (64.1 s).",
         11, False, INK),
    ])

    _panel(slide, COL_R, PANEL_TOP, COL_W, PANEL_H, VIOLET, gap=4, paras=[
        ("Why SQL is faster", 14, True, INK),
        ("1.3 s   ·   51×", 22, True, VIOLET),
        ("same job in SQL, same 12 pieces, same laptop", 10.5, False, GREY),
        ("No Python of ours inside: split / explode / group by run in Spark's own "
         "engine. There is nothing to start.", 11, False, INK),
        ("Pieces stop mattering: 1.2 s at 4, 1.3 s at 12.", 11, True, INK),
        ("And Windows beats the cluster, 1.2 s against 1.9 s.", 11, False, INK),
    ])

    _textbox(slide, COL_L, FOOT_TOP, 8.76, FOOT_H, gap=2, paras=[
        ("One cost, seen twice: starting a Python process. Linux makes it cheap; "
         "SQL makes it unnecessary.", 12.5, True, MAGENTA),
        ("The hand-written path is what ships — only it is byte-identical. "
         "SQL's tokenizer is not ours.", 11, False, GREY),
        ("39 MB corpus: almost all of this time is start-up. Next slide.", 11, False, GREY),
    ])


def build_slide_b(slide):
    """Part 5 — the same job on the largest corpus the project owns."""

    _textbox(slide, COL_L, 1.16, 8.76, 0.30, [(
        "Real data: 3,026 SEC filings, 376 MiB, 59.7 M tokens — everything this "
        "project has.", 12, True, INK)])

    _panel(slide, COL_L, PANEL_TOP, COL_W, PANEL_H, BLUE, gap=5, paras=[
        ("The gap collapses", 14, True, INK),
        [("2.3×", 20, True, BLUE), ("   best run to best run", 12, False, GREY)],
        [("1.4×", 20, True, BLUE), ("   run for run, same setting", 12, False, GREY)],
        ("The 16.7× was Python start-up, not computing power.", 11, False, INK),
        ("The law on the last slide flips too: here 12 pieces is the fastest Windows "
         "run, not the slowest.", 11, False, INK),
        ("--partitions never took effect on the cluster, so that spread is variance, "
         "not tuning.", 11, False, INK),
    ])

    _panel(slide, COL_R, PANEL_TOP, COL_W, PANEL_H, VIOLET)
    _textbox(slide, 5.20, 1.54, 4.06, 0.30, [("Where the time goes", 14, True, INK)])
    _table(slide, 5.20, 1.90, 4.06, 0.90,
           [("", "wall clock", "start-up *", "residual"),
            ("Windows, 12 threads", "89.3 s", "~60 s", "~29 s"),
            ("Cluster, 4 cores", "63.6 s", "~0 s", "~64 s")],
           (1.62, 0.86, 0.78, 0.80))
    _textbox(slide, 5.20, 2.92, 4.06, 1.36, gap=4, paras=[
        ("On compute alone the laptop is ~2.2× faster: ~29 s against ~64 s.", 11, False, INK),
        ("* modelled, not timed here: measured with zero data, so corpus-independent.",
         11, False, GREY),
        ("Same laptop — the cluster is Docker Desktop on it, 2 × 2 cores.", 11, False, GREY),
    ])

    _textbox(slide, COL_L, FOOT_TOP, 8.76, FOOT_H, gap=2, paras=[
        ("All the compute this project owns is ~65 core-seconds — less than Windows "
         "spends starting processes.", 12.5, True, MAGENTA),
        ("A comparison decided by computing is not reachable with this data.",
         11, False, GREY),
    ])


def build_slide_c(slide):
    """Part 6 — what the layer cost the site, and what it bought instead."""

    _textbox(slide, COL_L, 1.16, 8.76, 0.30, [(
        "The artifact the site serves: one SQLite search index over 26,368 documents, "
        "352 MB.", 12, True, INK)])

    _panel(slide, COL_L, PANEL_TOP, COL_W, PANEL_H, BLUE)
    _textbox(slide, 0.74, 1.54, 4.06, 0.30, [("What distribution cost", 14, True, INK)])
    _textbox(slide, 0.74, 1.86, 4.06, 0.40, [("72.6 s   ·   2.9× slower", 22, True, BLUE)])
    _table(slide, 0.74, 2.32, 4.06, 1.40,
           [("", "wall clock", "recorded in"),
            ("One machine", "~25 s", "stopwatch"),
            ("Docker cluster", "72.6 s", "manifest"),
            ("    – spread over cores", "24.8 s", ""),
            ("    – one writer, in turn", "47.8 s", "")],
           (2.30, 0.82, 0.94))
    _textbox(slide, 0.74, 3.84, 4.06, 0.46, [(
        "The spread-out phase parallelises. The write cannot: SQLite takes one writer.",
        11, False, INK)])

    _panel(slide, COL_R, PANEL_TOP, COL_W, PANEL_H, VIOLET)
    _textbox(slide, 5.20, 1.54, 4.06, 0.30,
             [("What the parity requirement bought", 14, True, INK)])
    _table(slide, 5.20, 1.92, 4.06, 1.68,
           [("what the parity work turned up", "scale"),
            ("a repeated tag counted twice", "37.2% of macro docs"),
            ("the updater double-counted rows", "now fixed"),
            ("the app silently ignored the index", "fell back to a scan"),
            ("release dates set too early", "36.6%, up to 87 d"),
            ("tests, where there was one script", "42")],
           (2.44, 1.62))
    _textbox(slide, 5.20, 3.74, 4.06, 0.50, [(
        "The first three were live in the product. Two builds forced to agree byte for "
        "byte is what found them.", 11, False, INK)])

    _textbox(slide, COL_L, FOOT_TOP, 8.76, FOOT_H, gap=2, paras=[
        ("Distribution bought the site no speed at all. What it bought is correctness.",
         12.5, True, MAGENTA),
        ("Paid once at build time, zero at query time. The cluster also wrote through a "
         "Docker mount — that cost is inside the 47.8 s.", 11, False, GREY),
        ("Scale is real but forward-looking: at 352 MB that is headroom, not a benefit "
         "already collected.", 11, False, GREY),
    ])


# ----------------------------------------------------------------- driver --
def _find_slide(prs, title):
    for slide in prs.slides:
        if slide.shapes.title is not None and slide.shapes.title.text_frame.text.strip() == title:
            return slide
    return None


def _strip(slide):
    """Drop everything except the title placeholder and the white card."""
    kept = 0
    title_el = slide.shapes.title._element if slide.shapes.title is not None else None
    for shape in list(slide.shapes):
        is_title = shape._element is title_el
        is_card = (
            shape.shape_type == MSO_SHAPE_TYPE.AUTO_SHAPE
            and abs(shape.left - Inches(CARD_LEFT)) < Inches(0.02)
            and abs(shape.top - Inches(CARD_TOP)) < Inches(0.02)
        )
        if is_title or is_card:
            kept += 1
            continue
        shape._element.getparent().remove(shape._element)
    if kept != 2:
        raise LookupError(f"expected title + card, kept {kept}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deck", default=str(DECK))
    args = parser.parse_args(argv)
    deck = Path(args.deck)

    lock = deck.with_name("~$" + deck.name)
    if lock.exists():
        print(f"REFUSING: {lock.name} exists - the deck is open in PowerPoint. Close it first.")
        return 1

    prs = Presentation(str(deck))
    for title, builder in ((SLIDE_A, build_slide_a), (SLIDE_B, build_slide_b),
                           (SLIDE_C, build_slide_c)):
        slide = _find_slide(prs, title)
        if slide is None:
            print(f"slide not found: {title!r}")
            return 1
        _strip(slide)
        builder(slide)
        print(f"rebuilt {title!r}: {len(slide.shapes)} native shapes, no picture")

    prs.save(str(deck))
    print(f"saved {deck}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
