// Steps the scene one frame at a time and saves a PNG of each, so the timing is
// the script's rather than the renderer's — no dropped frames, no wall clock.

const {app, BrowserWindow} = require("electron");
const fs = require("node:fs");
const path = require("node:path");

const dir = __dirname;
const {fps, frames, width, height} = JSON.parse(
  fs.readFileSync(path.join(dir, "meta.json"), "utf8"),
);
const out = path.join(dir, "frames");
fs.rmSync(out, {recursive: true, force: true});
fs.mkdirSync(out, {recursive: true});

app.disableHardwareAcceleration();

app.whenReady().then(async () => {
  const win = new BrowserWindow({
    width,
    height,
    show: false,
    useContentSize: true,
    webPreferences: {backgroundThrottling: false},
  });
  await win.loadFile(path.join(dir, "scene.html"));
  // The web font, if it resolves, and the first paint.
  await new Promise((resolve) => setTimeout(resolve, 600));

  for (let frame = 0; frame < frames; frame += 1) {
    await win.webContents.executeJavaScript(`window.setFrame(${frame})`);
    const image = await win.webContents.capturePage();
    fs.writeFileSync(
      path.join(out, `f${String(frame).padStart(4, "0")}.png`),
      image.toPNG(),
    );
  }

  console.log(`captured ${frames} frames at ${fps}fps into ${out}`);
  app.exit(0);
});
