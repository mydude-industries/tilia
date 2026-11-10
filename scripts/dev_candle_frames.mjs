// Dev-only: frames of the candle page at chosen moments, so a change to the flame, the
// smoke or the follow-up can be looked at without a phone. Never run by Tilia.
//
//   node scripts/dev_candle_frames.mjs <url> <out-dir> [--lit 0,800,1600] [--out 300,1200,2200,3400,4600]
//
// Serves nothing itself: start `python3 -m http.server PORT --bind 127.0.0.1` in webapp/
// first and pass http://127.0.0.1:PORT/candle.html. "lit" times are ms after lighting,
// "out" times are ms after blowing out (by tap). Writes lit-<ms>.png and out-<ms>.png.
import { createRequire } from "node:module";
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

const require = createRequire(join(homedir(), "node_modules", "x.js"));
const { chromium } = require("playwright-core");

const [url, outDir, ...rest] = process.argv.slice(2);
if (!url || !outDir) {
  console.error("usage: node scripts/dev_candle_frames.mjs <url> <out-dir> [--lit ms,ms] [--out ms,ms]");
  process.exit(2);
}
function arg(name, dflt) {
  const i = rest.indexOf(name);
  return (i >= 0 ? rest[i + 1] : dflt).split(",").map(Number).sort((a, b) => a - b);
}
const litTimes = arg("--lit", "0,800,1600");
const outTimes = arg("--out", "300,1200,2200,3400,4600");
mkdirSync(outDir, { recursive: true });

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 390, height: 800 }, deviceScaleFactor: 2 });
const errors = [];
page.on("pageerror", (e) => errors.push(String(e)));
page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
await page.goto(url + (url.includes("?") ? "&" : "?") + "nocache=" + Date.now());
await page.waitForTimeout(300);

async function series(prefix, times, start) {
  const t0 = Date.now();
  await start();
  for (const ms of times) {
    const wait = ms - (Date.now() - t0);
    if (wait > 0) await page.waitForTimeout(wait);
    await page.screenshot({ path: join(outDir, `${prefix}-${ms}.png`) });
  }
}
await series("lit", litTimes, () => page.click("#candle"));
await series("out", outTimes, () => page.click("#candle"));
await browser.close();
console.log(`wrote ${litTimes.length + outTimes.length} frames to ${outDir}`);
if (errors.length) { console.error("page errors:\n" + errors.join("\n")); process.exit(1); }
