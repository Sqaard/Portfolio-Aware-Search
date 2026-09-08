"""Bring presentations/BigData_ITMO.pptx in line with the re-measured numbers.

    python presentations/build_bigdata_deck_assets.py --only performance
    python presentations/refresh_bigdata_deck_numbers.py

Two jobs:

1. **Slide 10** — re-embed ``slide12_performance.png`` after the figure is
   regenerated.  The blob of the existing image part is replaced in place, so the
   relationship, the z-order and the shape geometry are untouched.

2. **Slide 13 (Conclusion)** — retext the two stat cards: ``53`` becomes the
   measured Big Data test count and ``16.7×`` becomes the honest heavy-load
   figure.  The cards carry no ``a:rPr`` at all — every bit of formatting lives in
   ``a:pPr/a:defRPr`` — so only ``run.text`` is assigned.  Using ``text_frame.text``
   or ``paragraph.text`` would rebuild the paragraph, drop ``defRPr`` and silently
   revert both cards to theme defaults.

Idempotent: a card already carrying the new text is left alone.  Close PowerPoint
first — it holds the pre-edit copy in memory and will clobber the write.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE

ROOT = Path(__file__).resolve().parents[1]
DECK = ROOT / "presentations" / "BigData_ITMO.pptx"
ASSETS = ROOT / "presentations" / "assets" / "bigdata_itmo"

PERFORMANCE_SLIDE = "Part 4 — PySpark: Windows vs Cluster"
PERFORMANCE_IMAGE = ASSETS / "slide12_performance.png"

CONCLUSION_SLIDE = "Conclusion"
# card headline -> (old headline, new headline, expected caption, new caption)
CARD_EDITS = [
    ("53", "42", "Big Data tests", "Big Data tests"),
    ("16.7×", "2.3×", "cluster speed-up", "on real work"),
]


def _find_slide(prs, title):
    for slide in prs.slides:
        if slide.shapes.title is not None and slide.shapes.title.text_frame.text.strip() == title:
            return slide
    return None


def _refresh_picture(prs, slide) -> bool:
    pics = [sh for sh in slide.shapes if sh.shape_type == MSO_SHAPE_TYPE.PICTURE]
    if len(pics) != 1:
        raise LookupError(f"expected one picture on {CONCLUSION_SLIDE!r}, found {len(pics)}")
    pic = pics[0]
    part = slide.part.related_part(pic._element.blip_rId)

    users = sum(
        1
        for s in prs.slides
        for sh in s.shapes
        if sh.shape_type == MSO_SHAPE_TYPE.PICTURE
        and s.part.related_part(sh._element.blip_rId) is part
    )
    if users != 1:
        raise RuntimeError(f"image part is shared by {users} pictures; refusing to overwrite")

    blob = PERFORMANCE_IMAGE.read_bytes()
    if part.blob == blob:
        print("slide 10 figure already current")
        return False
    part._blob = blob
    print(f"slide 10 figure re-embedded ({len(blob):,} bytes)")
    return True


def _retext_cards(slide) -> bool:
    changed = False
    for shape in slide.shapes:
        if not shape.has_text_frame or shape.is_placeholder:
            continue
        paras = shape.text_frame.paragraphs
        if len(paras) != 2 or not all(len(p.runs) == 1 for p in paras):
            continue
        head, caption = paras[0].runs[0], paras[1].runs[0]
        for old_head, new_head, old_cap, new_cap in CARD_EDITS:
            if head.text == old_head and caption.text.strip() == old_cap:
                head.text = new_head
                caption.text = new_cap
                print(f"card retexted: {old_head!r}/{old_cap!r} -> {new_head!r}/{new_cap!r}")
                changed = True
    return changed


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deck", default=str(DECK))
    args = parser.parse_args(argv)
    deck = Path(args.deck)

    lock = deck.with_name("~$" + deck.name)
    if lock.exists():
        print(f"REFUSING: {lock.name} exists - the deck is open in PowerPoint. Close it first.")
        return 1
    if not PERFORMANCE_IMAGE.exists():
        print(f"missing figure: {PERFORMANCE_IMAGE}")
        return 1

    prs = Presentation(str(deck))

    perf = _find_slide(prs, PERFORMANCE_SLIDE)
    if perf is None:
        print(f"slide not found: {PERFORMANCE_SLIDE!r}")
        return 1
    concl = _find_slide(prs, CONCLUSION_SLIDE)
    if concl is None:
        print(f"slide not found: {CONCLUSION_SLIDE!r}")
        return 1

    changed = _refresh_picture(prs, perf)
    changed = _retext_cards(concl) or changed

    if not changed:
        print("nothing to do")
        return 0

    prs.save(str(deck))
    print(f"saved {deck}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
