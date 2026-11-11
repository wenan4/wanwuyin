// node boxes.mjs <seal...> — screen boxes of seal and page at a few times of the animation
// Chrome: set CHROME_PATH on Windows / Linux (default is the macOS app path).
import { chromium } from "playwright-core";
const browser = await chromium.launch({ executablePath: process.env.CHROME_PATH ?? "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", args: [...(process.platform === "darwin" ? ["--use-angle=metal"] : []), "--enable-gpu", "--ignore-gpu-blocklist"] });
const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
for (const n of process.argv.slice(2)) {
  const url = new URL("seal.html", process.env.SEAL_BASE_URL ?? "http://localhost:5199/");
  url.search = new URLSearchParams({ view: "anim", seal: n, capture: "" }).toString();
  await page.goto(url.href);
  await page.waitForFunction(() => document.title === "ready");
  for (const t of [1, 4.5, 7, 9.2, 11]) {
    const b = await page.evaluate(async (t) => { await window.seek(t); return window.boxes(); }, t);
    const bad = Object.values(b).some((v) => v[0] < 0.02 || v[2] > 0.98 || v[1] < 0.02 || v[3] > 0.88);
    console.log(n.padEnd(10), `t=${t}`.padEnd(6), "seal", b.seal.join(","), " page", t < 5.2 ? "(not in yet)" : b.page.join(","), t >= 5.2 && bad ? "  <-- near edge / caption" : "");
  }
}
await browser.close();
