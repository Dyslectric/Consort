"""Turn a string into a single SVG path using a TTF, with tracking control.

Emits path data in a 1000-unit em, y-down (SVG), baseline at y=0, first glyph
starting at x=0. Returns the advance width so callers can size a viewBox.
"""

import sys

from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont


def text_path(font_path, text, tracking=0.0, upem_out=1000.0):
    """tracking is in units of upem_out (e.g. 20 = 0.02em of extra letterspace)."""
    font = TTFont(font_path)
    upem = font["head"].unitsPerEm
    scale = upem_out / upem
    glyph_set = font.getGlyphSet()
    cmap = font.getBestCmap()
    hmtx = font["hmtx"]

    try:
        kern_pairs = {}
        gpos = font["GPOS"] if "GPOS" in font else None
    except Exception:
        gpos = None

    pen = SVGPathPen(glyph_set, ntos=lambda v: f"{v:.1f}")
    x = 0.0
    names = []
    for ch in text:
        gname = cmap.get(ord(ch))
        if gname is None:
            raise SystemExit(f"no glyph for {ch!r}")
        names.append(gname)

    for i, gname in enumerate(names):
        # y-flip: font space is y-up, SVG is y-down.
        tpen = TransformPen(pen, (scale, 0, 0, -scale, x, 0))
        glyph_set[gname].draw(tpen)
        adv = hmtx[gname][0] * scale
        x += adv + tracking

    total = x - tracking if names else 0.0
    return pen.getCommands(), total, font


def metrics(font):
    upem = font["head"].unitsPerEm
    os2 = font["OS/2"]
    return {
        "cap": os2.sCapHeight * 1000.0 / upem,
        "x": os2.sxHeight * 1000.0 / upem,
        "asc": font["hhea"].ascent * 1000.0 / upem,
        "desc": font["hhea"].descent * 1000.0 / upem,
    }


if __name__ == "__main__":
    fp, text = sys.argv[1], sys.argv[2]
    tracking = float(sys.argv[3]) if len(sys.argv) > 3 else 0.0
    d, w, font = text_path(fp, text, tracking)
    m = metrics(font)
    print(f"WIDTH {w:.1f}")
    print(f"CAP {m['cap']:.1f}  XH {m['x']:.1f}  ASC {m['asc']:.1f}  DESC {m['desc']:.1f}")
    print(d)
