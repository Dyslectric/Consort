# Consort brand assets

The mark is a **chord**: four voices of different range sounding together. It is
the shortest thing that says what the project is — a room where several people
are heard at once — and it is the only element small enough to survive a 16px
favicon with an unread count drawn on top of it.

The wordmark is Source Sans 3 Semibold converted to outlines, which is the app's
own UI face, so the lockup needs no webfont and renders identically everywhere.

| | |
|---|---|
| Mark gradient | `#2DD4BF` → `#0E7490`, diagonal |
| Wordmark gradient | `#25C4B4` → `#0D8A96`, vertical |
| Single colour | `#0D9488` |

One asset has to work on both themes: Zulip serves the *same* default logo file
for its light and dark navbars. That is why the wordmark's gradient is narrow —
its light end is still dark enough for `hsl(0deg 0% 97%)` and its dark end still
light enough for `hsl(0deg 0% 13%)`.

## Files

| File | Where it is used |
|---|---|
| `consort-logo.svg` | the top-left of the app — Zulip's default realm logo, rendered at 20px tall |
| `consort-logo-mono.svg` | the same lockup in `currentColor`, for single-colour contexts |
| `consort-mark.svg` | the mark alone, transparent |
| `consort-icon-square.svg` | rounded tile — PWA maskable icon |
| `consort-icon-circle.svg` | disc |
| `consort-icon-128x128.png` | `og:image` |
| `consort-icon-512x512.png` | PWA manifest icon, Web Push notification icon |
| `apple-touch-icon-precomposed.png` | iOS home screen (full bleed; iOS applies its own mask) |
| `favicon.svg`, `favicon.png` | browser tab |

## Regenerating

Everything above — plus the fragments that are pasted inline into templates
rather than shipped as files — comes out of one generator. Edit the geometry
constants at the top of `generate.py`, never the SVGs by hand.

```bash
pip install fonttools pillow
python brand/generate.py /tmp/consort-brand
```

The PNGs are rasterised through headless Chromium; it is found automatically, or
set `BRAND_BROWSER` to point at one. Without a browser the SVG masters are still
written and only the PNG step fails.

`_fragments/` in the output holds the inline copies that live in the fork's
templates: the unread-count favicon path (`web/templates/favicon.svg.hbs`), the
message-feed watermark and loading splash (`templates/zerver/app/index.html`),
and the login-page lockup (`templates/zerver/portico-header.html`). Those are
pasted in, so after changing the geometry they have to be pasted again.

`fonts/` vendors the one weight the wordmark needs, under the SIL Open Font
License — see `fonts/LICENSE.md`. Source is a trademark of Adobe.
