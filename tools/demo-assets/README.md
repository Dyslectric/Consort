# Demo assets

Regenerates the four demo avatars and the sidebar animation in `docs/`:

| Output | |
| --- | --- |
| `docs/demo-avatars/*.svg` | Penguin, elephant, crab and camel, three colours each including the background — the constraint is what keeps them readable at the sidebar's real 18px. |
| `docs/voice-channel.gif` | Four people in a voice channel, speaking rings lighting as each talks. 50fps. |
| `docs/voice-channel.mp4` | The same at a true 60fps. |

The avatars are also meant to be uploaded as the profile pictures of the demo
deployment's test accounts, so recorded footage matches the animation.

## Why it is built rather than drawn

`build.mjs` writes both the standalone avatars **and** the page they appear in,
from one description of each person, so the two cannot drift apart.

The scene is not a mockup of the sidebar; it is the sidebar's own numbers out of
`web/styles/jitsi_sidebar.css` in the Zulip fork — 18px avatars, the 2px ring at
`hsl(147deg 52% 45% / 90%)` fading over 120ms, `6px` between avatar and name,
the `1px 6px 5px 32px` occupant padding — multiplied by `SCALE` so a GIF of an
18px avatar is legible. Two values are deliberately not the app's, and are
commented where they are set: the gap between occupants, and the space under the
channel label. Both exist because the app is separated by the rows around it and
an isolated crop is not.

**If the sidebar styling changes, change it here too**, or the demo starts
advertising an interface that no longer exists.

## Running it

Frames are stepped by script and captured one at a time, so the timing is the
script's rather than a wall clock's — nothing drops, and the result is identical
every run.

```sh
node tools/demo-assets/build.mjs
npx electron tools/demo-assets/capture.js   # needs an Electron; the desktop app's will do
```

Then encode, which needs `ffmpeg`:

```sh
cd tools/demo-assets
ffmpeg -y -framerate 60 -i frames/f%04d.png \
  -filter_complex "fps=50,split [a][b];[a] palettegen=stats_mode=diff [p];[b][p] paletteuse=dither=none" \
  -loop 0 ../../docs/voice-channel.gif
ffmpeg -y -framerate 60 -i frames/f%04d.png \
  -c:v libx264 -pix_fmt yuv420p -crf 18 -movflags +faststart ../../docs/voice-channel.mp4
```

**The GIF is 50fps and cannot be 60.** GIF stores each frame's delay in
hundredths of a second, so the achievable rates are 100/n — 50 or 33, nothing
between. The `fps=50` filter resamples the 60fps capture and keeps the duration
right; the MP4 carries the real 60.

`dither=none` is deliberate. The art is flat colour, so a diff palette
reproduces it exactly and dithering would only add noise for the encoder to
spend bytes on — it is the difference between 80KB and several hundred.

`scene.html`, `meta.json` and `frames/` are generated and ignored.
