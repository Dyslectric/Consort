"""Generate every Consort brand asset from one definition.

The mark is a chord: four voices of different range sounding together. It is
emitted as filled stadium paths rather than strokes, so that consumers which
colour it through CSS ``fill`` (Zulip's message-feed watermark, the unread
favicon) work without special-casing, and so no renderer has to honour
stroke-linecap to get the shape right.

Geometry lives in a 1000x1000 box. The wordmark is Source Sans 3 Semibold
converted to outlines -- the app's own UI face -- so the lockup needs no webfont.
"""

import argparse
import os
import pathlib
import shutil
import subprocess
import sys

from wordmark import text_path

HERE = pathlib.Path(__file__).parent
FONT = HERE / "fonts" / "SourceSans3-Semibold.ttf"

# ------------------------------------------------------------------ constants

CAP = 660.0            # Source Sans 3 cap height, per 1000 em
TRACKING = 14.0        # extra letterspace, per 1000 em
MARK_H = 880.0         # mark box height in wordmark units
GAP = 190.0            # space between mark and wordmark
PAD = 20.0

BARS_X = [205, 415, 625, 835]
BARS_H = [510, 830, 630, 370]
BAR_W = 165

MARK_FROM, MARK_TO = "#2DD4BF", "#0E7490"   # diagonal, on the mark
WORD_FROM, WORD_TO = "#25C4B4", "#0D8A96"   # vertical, on the wordmark
SOLID = "#0D9488"                            # single-colour contexts
TILE_RADIUS = 220


def stadium(x, h, w=BAR_W, cy=500.0):
    """One bar: a vertical capsule centred on cy."""
    r = w / 2
    y0, y1 = cy - h / 2, cy + h / 2
    return (
        f"M{x - r:g} {y0 + r:g}"
        f"A{r:g} {r:g} 0 0 1 {x + r:g} {y0 + r:g}"
        f"L{x + r:g} {y1 - r:g}"
        f"A{r:g} {r:g} 0 0 1 {x - r:g} {y1 - r:g}"
        f"Z"
    )


def chord_d(scale=1.0, dx=0.0, dy=0.0, cy=500.0):
    """The mark's path data, optionally scaled/translated in place."""
    parts = []
    for x, h in zip(BARS_X, BARS_H):
        d = stadium(x * scale + dx, h * scale, BAR_W * scale, cy * scale + dy)
        parts.append(d)
    return "".join(parts)


def mark_grad(gid):
    return (
        f'<linearGradient id="{gid}" x1="0" y1="0" x2="1" y2="1">'
        f'<stop offset="0" stop-color="{MARK_FROM}"/>'
        f'<stop offset="1" stop-color="{MARK_TO}"/></linearGradient>'
    )


def word_grad(gid):
    return (
        f'<linearGradient id="{gid}" x1="0" y1="0" x2="0" y2="1">'
        f'<stop offset="0" stop-color="{WORD_FROM}"/>'
        f'<stop offset="1" stop-color="{WORD_TO}"/></linearGradient>'
    )


HEAD = '<svg xmlns="http://www.w3.org/2000/svg"'


# ------------------------------------------------------------------- assets


def svg_mark():
    """The mark alone, transparent background. viewBox 0 0 1000 1000."""
    return (
        f'{HEAD} viewBox="0 0 1000 1000">{mark_grad("a")}'
        f'<path d="{chord_d()}" fill="url(#a)"/></svg>\n'
    )


def svg_tile(radius=TILE_RADIUS):
    """White mark on a gradient rounded square -- app icon, PWA maskable."""
    s = 0.62
    off = 500 * (1 - s)
    return (
        f'{HEAD} viewBox="0 0 1000 1000">{mark_grad("a")}'
        f'<rect width="1000" height="1000" rx="{radius}" fill="url(#a)"/>'
        f'<path d="{chord_d(s, off, off)}" fill="#fff"/></svg>\n'
    )


def svg_circle():
    """White mark on a gradient disc -- avatars, favicon-with-count backdrop."""
    s = 0.58
    off = 500 * (1 - s)
    return (
        f'{HEAD} viewBox="0 0 1000 1000">{mark_grad("a")}'
        f'<circle cx="500" cy="500" r="500" fill="url(#a)"/>'
        f'<path d="{chord_d(s, off, off)}" fill="#fff"/></svg>\n'
    )


def lockup_parts():
    """Shared maths for the horizontal logo."""
    d, w, _f = text_path(str(FONT), "Consort", TRACKING)
    top = -CAP / 2 - MARK_H / 2
    x_word = MARK_H + GAP
    vb = (-PAD, top - PAD, x_word + w + PAD * 2, MARK_H + PAD * 2)
    scale = MARK_H / 1000.0
    return d, x_word, top, scale, vb


def svg_logo(mono=None):
    """Mark + wordmark. This is the top-left of the app."""
    d, x_word, top, scale, vb = lockup_parts()
    vbs = " ".join(f"{v:.1f}" for v in vb)
    if mono:
        defs, mfill, wfill = "", mono, mono
    else:
        defs = mark_grad("a") + word_grad("b")
        mfill, wfill = "url(#a)", "url(#b)"
    return (
        f'{HEAD} viewBox="{vbs}">{defs}'
        f'<g transform="translate(0 {top:.1f}) scale({scale:.5f})">'
        f'<path d="{chord_d()}" fill="{mfill}"/></g>'
        f'<path transform="translate({x_word:.1f} 0)" d="{d}" fill="{wfill}"/>'
        f"</svg>\n"
    )


def svg_favicon():
    """Browser tab. Slightly tighter than the bare mark so it fills 16px."""
    s = 1.06
    off = 500 * (1 - s)
    return (
        f'{HEAD} viewBox="0 0 1000 1000">{mark_grad("a")}'
        f'<path d="{chord_d(s, off, off)}" fill="url(#a)"/></svg>\n'
    )


def favicon_hbs_path():
    """Path data for web/templates/favicon.svg.hbs, pre-fitted to its 16-unit
    box the way Zulip pre-fits its own glyph there."""
    s = 16.0 / 1000.0 * 1.06
    off = (16.0 - 1000.0 * s) / 2
    return chord_d(s, off, off)


def messages_logo_svg():
    """The feed watermark. Keeps Zulip's circle+path element structure so the
    existing CSS (circle -> --color-zulip-logo, path -> --color-zulip-logo-z)
    keeps colouring it with no stylesheet change."""
    s = 774.0 / 1000.0 * 0.58
    off = (774.0 - 1000.0 * s) / 2
    return chord_d(s, off, off)


def loading_logo_svg():
    """The pre-app splash, in a 774 box like Zulip's."""
    s = 774.0 / 1000.0 * 0.58
    off = (774.0 - 1000.0 * s) / 2
    return (
        f'<svg class="app-loading-logo" xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 774 774">{mark_grad("a")}'
        f'<circle cx="387" cy="387" r="370" fill="url(#a)"/>'
        f'<path d="{chord_d(s, off, off)}" fill="#fff"/></svg>'
    )


def portico_logo(fill="hsl(0, 0%, 27%)"):
    """Login page top-left: single colour, inline, height-25 like Zulip's."""
    d, x_word, top, scale, vb = lockup_parts()
    vbs = " ".join(f"{v:.1f}" for v in vb)
    return (
        vbs,
        f'<g transform="translate(0 {top:.1f}) scale({scale:.5f})">'
        f'<path d="{chord_d()}"/></g>'
        f'<path transform="translate({x_word:.1f} 0)" d="{d}"/>',
    )


# --------------------------------------------------------------- rasterising

RASTER = 1024  # headless Chromium is unhappy with tiny windows: render big, resample

# Any Chromium will do. Set BRAND_BROWSER to override.
BROWSER_CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    "/usr/bin/google-chrome",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
]


def find_browser():
    override = os.environ.get("BRAND_BROWSER")
    if override:
        return override
    for path in BROWSER_CANDIDATES:
        if pathlib.Path(path).exists():
            return path
    found = shutil.which("chromium") or shutil.which("google-chrome")
    if found:
        return found
    raise SystemExit(
        "No Chromium found for rasterising. Set BRAND_BROWSER to one, or skip "
        "the PNGs -- the SVGs above are the masters and were already written."
    )


def rasterise(svg_text, out_png, size):
    """SVG -> PNG at an exact pixel size, transparent background.

    Headless Chromium screenshots at exactly --window-size and ignores relative
    --screenshot paths, so: absolute path, one large render, then Lanczos down."""
    from PIL import Image

    tmp_html = HERE / "_raster.html"
    tmp_png = (HERE / "_raster.png").resolve()
    tmp_html.write_text(
        f"<style>html,body{{margin:0;padding:0;background:transparent}}"
        f"svg{{display:block;width:{RASTER}px;height:{RASTER}px}}</style>{svg_text}",
        encoding="utf-8",
    )
    out_png = pathlib.Path(out_png).resolve()
    out_png.parent.mkdir(parents=True, exist_ok=True)
    tmp_png.unlink(missing_ok=True)
    # A unique profile per call: without it a second headless Edge attaches to
    # the first one's singleton and never returns.
    profile = (HERE.parent / "_edge-profile").resolve()
    shutil.rmtree(profile, ignore_errors=True)
    subprocess.run(
        [
            find_browser(), "--headless=new", "--disable-gpu", "--hide-scrollbars",
            "--no-first-run", "--no-default-browser-check",
            f"--user-data-dir={profile}",
            "--default-background-color=00000000",
            f"--window-size={RASTER},{RASTER}",
            f"--screenshot={tmp_png}",
            tmp_html.as_uri(),
        ],
        check=True, capture_output=True, timeout=120,
    )
    if not tmp_png.exists():
        raise SystemExit(f"Edge produced no screenshot for {out_png.name}")
    img = Image.open(tmp_png).convert("RGBA")
    img.resize((size, size), Image.LANCZOS).save(out_png, optimize=True)
    tmp_png.unlink(missing_ok=True)
    tmp_html.unlink(missing_ok=True)
    shutil.rmtree(profile, ignore_errors=True)
    return out_png


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("outdir")
    args = ap.parse_args()
    out = pathlib.Path(args.outdir)
    out.mkdir(parents=True, exist_ok=True)

    files = {
        "consort-logo.svg": svg_logo(),
        "consort-logo-mono.svg": svg_logo(mono="currentColor"),
        "consort-mark.svg": svg_mark(),
        "consort-icon-square.svg": svg_tile(),
        "consort-icon-circle.svg": svg_circle(),
        "favicon.svg": svg_favicon(),
    }
    for name, text in files.items():
        (out / name).write_text(text, encoding="utf-8", newline="\n")
        print("svg  ", name)

    for name, src, size in [
        ("consort-icon-128x128.png", svg_tile(), 128),
        ("consort-icon-512x512.png", svg_tile(), 512),
        ("apple-touch-icon-precomposed.png", svg_tile(radius=0), 180),
        ("favicon.png", svg_favicon(), 64),
    ]:
        rasterise(src, out / name, size)
        print("png  ", name, size)

    # Fragments that get pasted into templates rather than shipped as files.
    frag = out / "_fragments"
    frag.mkdir(exist_ok=True)
    (frag / "favicon-hbs-path.txt").write_text(favicon_hbs_path(), encoding="utf-8", newline="\n")
    (frag / "messages-logo-path.txt").write_text(messages_logo_svg(), encoding="utf-8", newline="\n")
    (frag / "loading-logo.svg").write_text(loading_logo_svg(), encoding="utf-8", newline="\n")
    vbs, body = portico_logo()
    (frag / "portico-logo.txt").write_text(f"{vbs}\n---\n{body}", encoding="utf-8", newline="\n")
    print("frag ", "4 fragments")


if __name__ == "__main__":
    sys.exit(main())
