// node shot.mjs <url> <out.png> [width] [height]  — waits for document.title === "ready",
// prints window.boxes() (screen-space boxes of the tracked objects) when the page has it.
// Chrome: set CHROME_PATH on Windows / Linux (default is the macOS app path).
import { chromium } from "playwright-core";
const [url, out, w = "1600", h = "900"] = process.argv.slice(2);
const browser = await chromium.launch({
  executablePath: process.env.CHROME_PATH ?? "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  args: [...(process.platform === "darwin" ? ["--use-angle=metal"] : []), "--enable-gpu", "--ignore-gpu-blocklist"],
});
const page = await browser.newPage({ viewport: { width: +w, height: +h }, deviceScaleFactor: 2 });
page.on("console", (m) => m.type() === "error" && console.log("console:", m.text(), m.location().url));
page.on("pageerror", (e) => console.log("pageerror:", e.message));
await page.goto(url);
await page.waitForFunction(() => document.title === "ready", null, { timeout: 90000 });
await page.waitForTimeout(1500);
const boxes = await page.evaluate(() => window.boxes?.());
if (boxes) for (const [k, v] of Object.entries(boxes)) console.log(" ", k.padEnd(18), v.join(" "));
await page.screenshot({ path: out });
await browser.close();
console.log("wrote", out);
