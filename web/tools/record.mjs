// node record.mjs <url with &capture> <out.mp4> [fps] [width] [height] [ranges, e.g. 0.4-2,10-11.6]
// Steps the page with window.seek(t), screenshots every frame, encodes with ffmpeg.
// Also prints window.boxes() at a few times so the motion can be checked without watching it.
// Chrome: set CHROME_PATH on Windows / Linux (default is the macOS app path).
import { chromium } from "playwright-core";
import { execFileSync } from "node:child_process";
import { mkdirSync, rmSync } from "node:fs";
const [url, out, fps = "30", w = "1920", h = "1080", ranges = ""] = process.argv.slice(2);
const dir = out.replace(/\.mp4$/, "_frames");
rmSync(dir, { recursive: true, force: true });
mkdirSync(dir, { recursive: true });
const browser = await chromium.launch({
  executablePath: process.env.CHROME_PATH ?? "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  args: [...(process.platform === "darwin" ? ["--use-angle=metal"] : []), "--enable-gpu", "--ignore-gpu-blocklist"],
});
const page = await browser.newPage({ viewport: { width: +w, height: +h }, deviceScaleFactor: 1 });
page.on("pageerror", (e) => console.log("pageerror:", e.message));
await page.goto(url);
await page.waitForFunction(() => document.title === "ready", null, { timeout: 90000 });
const dur = await page.evaluate(() => window.duration);
const spans = ranges ? ranges.split(",").map((r) => r.split("-").map(Number)) : [[0, dur]];
const times = spans.flatMap(([a, b]) => Array.from({ length: Math.round((b - a) * +fps) }, (_, k) => a + k / +fps));
const n = times.length;
for (let i = 0; i < n; i++) {
  const t = times[i];
  await page.evaluate((t) => window.seek(t), t);
  if (i % (+fps) === 0) {
    const b = await page.evaluate(() => ({ ...window.boxes(), cap: document.getElementById("cap")?.textContent }));
    console.log(`t=${t.toFixed(1)}`, Object.entries(b).map(([k, v]) => `${k}=${Array.isArray(v) ? v.join(",") : v}`).join("  "));
  }
  await page.screenshot({ path: `${dir}/f_${String(i).padStart(4, "0")}.jpg`, type: "jpeg", quality: 93 });
}
await browser.close();
execFileSync("ffmpeg", ["-y", "-loglevel", "error", "-framerate", fps, "-i", `${dir}/f_%04d.jpg`, "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "17", "-movflags", "+faststart", out]);
console.log("wrote", out, n, "frames");
