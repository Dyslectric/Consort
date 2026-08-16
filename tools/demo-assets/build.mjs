// Generates the four demo avatars and the sidebar scene they appear in, from one
// description each, so the standalone files and the frames in the GIF cannot
// drift apart.
//
// The scene reproduces the real sidebar rather than dressing it up: the sizes,
// gaps, paddings and the speaking ring are the values in
// web/styles/jitsi_sidebar.css, multiplied by SCALE so a GIF of an 18px avatar
// is legible. The ring is flat and 2px with no glow and no pulse, because that
// is what the app does and the comment in that file is emphatic about it.

import fs from "node:fs";
import path from "node:path";

const OUT_AVATARS = path.join(import.meta.dirname, "../../docs/demo-avatars");
const OUT_PAGE = path.join(import.meta.dirname, "scene.html");

const SCALE = 3;
const px = (n) => `${n * SCALE}px`;

// Three colours each, background included. The constraint is the point: at 18px
// a fourth colour is mud, and every one of these has to survive being 18px.
const people = [
  {
    id: "penguin",
    name: "Ada",
    animal: "penguin",
    colors: ["#dff0f7", "#1e2a33", "#f5a623"],
    art: (bg, ink, accent) => `
    <ellipse cx="32" cy="36" rx="18" ry="22" fill="${ink}"/>
    <circle cx="32" cy="22" r="14" fill="${ink}"/>
    <ellipse cx="32" cy="40" rx="11.5" ry="16" fill="${bg}"/>
    <ellipse cx="32" cy="23" rx="9.5" ry="8.5" fill="${bg}"/>
    <ellipse cx="25" cy="57" rx="5" ry="2.6" fill="${accent}"/>
    <ellipse cx="39" cy="57" rx="5" ry="2.6" fill="${accent}"/>
    <path d="M32 26l4.2 5h-8.4z" fill="${accent}"/>
    <circle cx="27.2" cy="21.5" r="2" fill="${ink}"/>
    <circle cx="36.8" cy="21.5" r="2" fill="${ink}"/>`,
  },
  {
    id: "elephant",
    name: "Bo",
    animal: "elephant",
    colors: ["#ecdfc9", "#7f8a99", "#e79f9f"],
    art: (bg, ink, accent) => `
    <circle cx="12.5" cy="27" r="11" fill="${ink}"/>
    <circle cx="51.5" cy="27" r="11" fill="${ink}"/>
    <circle cx="12.5" cy="27" r="6" fill="${accent}"/>
    <circle cx="51.5" cy="27" r="6" fill="${accent}"/>
    <circle cx="32" cy="28" r="15.5" fill="${ink}"/>
    <path d="M32 38v9" fill="none" stroke="${ink}" stroke-width="10" stroke-linecap="round"/>
    <path d="M32 46v6c0 4.5 5.5 5.5 7.5 2" fill="none" stroke="${ink}" stroke-width="6.5" stroke-linecap="round"/>
    <circle cx="25.5" cy="25.5" r="2.3" fill="${bg}"/>
    <circle cx="38.5" cy="25.5" r="2.3" fill="${bg}"/>`,
  },
  {
    id: "crab",
    name: "Ines",
    animal: "crab",
    colors: ["#ffe6c0", "#e0553a", "#2a2320"],
    art: (bg, ink, accent) => `
    <g stroke="${ink}" stroke-width="3.4" stroke-linecap="round" fill="none">
      <path d="M17 39 8 44"/><path d="M17.5 43.5 9 51"/><path d="M19 47 14 55"/>
      <path d="M47 39 56 44"/><path d="M46.5 43.5 55 51"/><path d="M45 47 50 55"/>
    </g>
    <circle cx="11" cy="30" r="8" fill="${ink}"/>
    <circle cx="53" cy="30" r="8" fill="${ink}"/>
    <path d="M3.5 26.5 12 30l-8.5 3.5z" fill="${bg}"/>
    <path d="M60.5 26.5 52 30l8.5 3.5z" fill="${bg}"/>
    <path d="M26 30v-8" stroke="${ink}" stroke-width="3.2" stroke-linecap="round"/>
    <path d="M38 30v-8" stroke="${ink}" stroke-width="3.2" stroke-linecap="round"/>
    <ellipse cx="32" cy="39" rx="17" ry="12" fill="${ink}"/>
    <circle cx="26" cy="20" r="3.2" fill="${accent}"/>
    <circle cx="38" cy="20" r="3.2" fill="${accent}"/>`,
  },
  {
    id: "camel",
    name: "Ravi",
    animal: "camel",
    colors: ["#6d8fa4", "#d8a566", "#4a3421"],
    // Two humps rather than one: a dromedary's single hump reads as a back, and
    // at 18px "camel" has to arrive before the detail does.
    art: (bg, ink, accent) => `
    <g stroke="${ink}" stroke-width="4.5" stroke-linecap="round">
      <path d="M16 44v13"/><path d="M24 45v12"/><path d="M32 45v12"/><path d="M40 44v11"/>
    </g>
    <ellipse cx="27" cy="40" rx="17" ry="9" fill="${ink}"/>
    <circle cx="20" cy="32" r="8.5" fill="${ink}"/>
    <circle cx="33" cy="32.5" r="8" fill="${ink}"/>
    <path d="M42 40C43 28 45 21 48 16" fill="none" stroke="${ink}" stroke-width="7.5" stroke-linecap="round"/>
    <ellipse cx="45.5" cy="9.5" rx="2.4" ry="3.6" fill="${ink}"/>
    <ellipse cx="51" cy="14.5" rx="7.5" ry="5.5" fill="${ink}" transform="rotate(-18 51 14.5)"/>
    <ellipse cx="57" cy="18.5" rx="4.2" ry="3.4" fill="${ink}"/>
    <path d="M54.5 21.5h5" stroke="${accent}" stroke-width="1.7" stroke-linecap="round"/>
    <circle cx="58.4" cy="16.4" r="1.2" fill="${accent}"/>
    <circle cx="50" cy="12" r="1.9" fill="${accent}"/>`,
  },
];

const avatar = (p) => `<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" viewBox="0 0 64 64" role="img" aria-label="${p.animal}">
  <defs><clipPath id="c-${p.id}"><circle cx="32" cy="32" r="32"/></clipPath></defs>
  <g clip-path="url(#c-${p.id})">
    <circle cx="32" cy="32" r="32" fill="${p.colors[0]}"/>${p.art(...p.colors)}
  </g>
</svg>
`;

fs.mkdirSync(OUT_AVATARS, {recursive: true});
for (const p of people) {
  fs.writeFileSync(path.join(OUT_AVATARS, `${p.id}.svg`), avatar(p));
}

// Who is talking, and when, in seconds. Overlapping on purpose: two rings lit at
// once is the thing a screenshot cannot show.
const script = [
  {id: "penguin", from: 0.3, to: 1.9},
  {id: "elephant", from: 1.6, to: 2.65},
  {id: "camel", from: 2.95, to: 4.35},
  {id: "crab", from: 4.1, to: 4.8},
  {id: "penguin", from: 5.1, to: 6.15},
  {id: "crab", from: 5.4, to: 6.45},
  {id: "elephant", from: 6.75, to: 7.45},
];

const DURATION = 7.8;
const FPS = 60;

// The sidebar's own numbers, from web/styles/jitsi_sidebar.css.
const AVATAR = 18;
const RING = 2;
const ROW_GAP = 6;
const COL_GAP = 6; // 3 in the app; opened up so the rings breathe at demo size
const OCC_TOP = 9; // 1 in the app, where the row above is taller and does the separating
const NAME = 12;
const COUNT = 11;
const CHANNEL = 15;
const WIDTH = 250;
const height =
  10 + CHANNEL * 1.6 + OCC_TOP + people.length * AVATAR + (people.length - 1) * COL_GAP + 5 + 10;

const rows = people
  .map(
    (p) => `      <div class="jitsi-sidebar-occupant">
        <span class="jitsi-sidebar-avatar" id="av-${p.id}">${avatar(p).replace(/\n\s*/g, "")}</span>
        <span class="jitsi-sidebar-name">${p.name}</span>
      </div>`,
  )
  .join("\n");

const page = `<!doctype html>
<html>
<head>
<meta charset="utf-8">
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  html, body {
    width: ${px(WIDTH)}; height: ${px(height)};
    background: hsl(0deg 0% 11%);
    font-family: "Source Sans 3", "Segoe UI", system-ui, sans-serif;
  }
  .narrow-filter { padding: ${px(5)} ${px(8)} 0; }
  .bottom_left_row { display: flex; align-items: center; gap: ${px(6)}; }
  .stream-privacy { display: inline-flex; align-items: center; color: #6bd6b8; }
  .stream-privacy svg { display: block; width: ${px(CHANNEL * 1.05)}; height: ${px(CHANNEL * 1.05)}; }
  .stream-name { font-size: ${px(CHANNEL)}; line-height: 1; color: hsl(0deg 0% 87%); }
  .jitsi-sidebar-count { line-height: 1; }
  .jitsi-sidebar-count { margin-left: auto; font-size: ${px(COUNT)}; color: hsl(0deg 0% 87%); opacity: 0.8; }
  .jitsi-sidebar-occupants {
    display: flex; flex-direction: column; gap: ${px(COL_GAP)};
    padding: ${px(OCC_TOP)} ${px(6)} ${px(5)} ${px(32)};
  }
  .jitsi-sidebar-occupant { display: flex; align-items: center; gap: ${px(ROW_GAP)}; }
  .jitsi-sidebar-avatar {
    display: inline-flex; flex: none;
    width: ${px(AVATAR)}; height: ${px(AVATAR)};
    border-radius: 50%; overflow: hidden;
    box-shadow: 0 0 0 ${px(RING)} hsl(147deg 52% 45% / 0%);
  }
  .jitsi-sidebar-avatar svg { display: block; width: 100%; height: 100%; }
  .jitsi-sidebar-name { font-size: ${px(NAME)}; color: hsl(0deg 0% 87%); opacity: 0.9; }
</style>
</head>
<body>
  <div class="narrow-filter">
    <div class="bottom_left_row">
      <span class="stream-privacy">
        <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-linecap="round">
          <path d="M8.5 2.4 4.7 5.4H2.2v5.2h2.5l3.8 3z" fill="currentColor" stroke="none"/>
          <path d="M11 5.6a3.2 3.2 0 0 1 0 4.8" stroke-width="1.2"/>
          <path d="M12.7 3.8a5.6 5.6 0 0 1 0 8.4" stroke-width="1.2"/>
        </svg>
      </span>
      <span class="stream-name">Voice</span>
      <span class="jitsi-sidebar-count">4</span>
    </div>
    <div class="jitsi-sidebar-occupants">
${rows}
    </div>
  </div>
<script>
  const SCRIPT = ${JSON.stringify(script)};
  const IDS = ${JSON.stringify(people.map((p) => p.id))};
  const FPS = ${FPS};
  const FADE = 0.12;

  function levelFor(id, t) {
    let best = 0;
    for (const turn of SCRIPT) {
      if (turn.id !== id) continue;
      const up = Math.min(1, Math.max(0, (t - turn.from) / FADE));
      const down = Math.min(1, Math.max(0, (turn.to - t) / FADE));
      best = Math.max(best, Math.min(up, down));
    }
    return best;
  }

  window.setFrame = (frame) => {
    const t = frame / FPS;
    for (const id of IDS) {
      const level = levelFor(id, t);
      document.getElementById("av-" + id).style.boxShadow =
        "0 0 0 ${px(RING)} hsl(147deg 52% 45% / " + (level * 0.9).toFixed(3) + ")";
    }
  };
  window.setFrame(0);
</script>
</body>
</html>
`;

fs.writeFileSync(OUT_PAGE, page);
fs.writeFileSync(
  path.join(import.meta.dirname, "meta.json"),
  JSON.stringify({
    fps: FPS,
    frames: Math.round(DURATION * FPS),
    width: WIDTH * SCALE,
    height: Math.round(height * SCALE),
  }),
);
console.log(
  `wrote ${people.length} avatars, scene.html at ${WIDTH * SCALE}x${Math.round(height * SCALE)}, ${Math.round(DURATION * FPS)} frames`,
);
