// Browser integration checks using the repository's playwright-core. Never records video.
import { chromium } from "playwright-core";
import assert from "node:assert/strict";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const base = process.env.MAKER_BASE_URL ?? "http://127.0.0.1:5199";
const root = fileURLToPath(new URL("../../", import.meta.url));
const output = path.join(root, "output/playwright"); mkdirSync(output, { recursive: true });
const defs = JSON.parse(readFileSync(new URL("../public/seals.json", import.meta.url), "utf8"));
const executablePath = process.env.CHROME_PATH ?? (process.platform === "win32"
  ? "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe" : "/usr/bin/google-chrome");
const browser = await chromium.launch({ executablePath, headless: true, args: ["--enable-gpu", "--ignore-gpu-blocklist"] });
const checks = [];
const diagnostics = [];
const watch = (page) => {
  page.on("pageerror", (error) => diagnostics.push({ page: page.url(), error: error.message }));
  page.on("console", (message) => { if (["warning", "error"].includes(message.type())) diagnostics.push({ page: page.url(), console: message.text() }); });
  page.on("requestfailed", (request) => diagnostics.push({ failed: request.url(), reason: request.failure()?.errorText }));
};
const pass = (name, details = {}) => { checks.push({ name, ...details }); console.log("PASS", name); };
const noOverflow = async (page) => assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true, "horizontal overflow");
const ready = async (page) => {
  await page.waitForFunction(() => document.documentElement.dataset.makerReady === "true");
  await page.locator("#flip").waitFor({ state: "visible" });
  await page.waitForFunction(() => !document.getElementById("flip").disabled);
  assert.equal(await page.locator("#seal-stage canvas").count(), 1, "real 3D canvas must be present");
};
const imprint = async (page, label) => {
  await page.locator("#quick-picks").getByRole("button", { name: label, exact: true }).click();
  await page.waitForFunction((label) => document.getElementById("result-label").textContent === label && !document.getElementById("flip").disabled, label);
  await page.locator("#flip").click();
  await page.waitForFunction(() => !document.getElementById("rub").disabled);
  await page.locator("#rub").click();
  await page.waitForFunction((label) => document.getElementById("live-status").textContent.includes(label + "的一页书拓好了"), label);
  await page.locator("#collect").click();
};
const view = { viewport: { width: 1440, height: 1050 }, reducedMotion: "reduce" };

try {
  const context = await browser.newContext(view);
  const page = await context.newPage();
  watch(page);
  const errors = []; let apiRequests = 0;
  page.on("pageerror", (error) => errors.push(error.message));
  context.on("request", (request) => { if (new URL(request.url()).pathname.startsWith("/api/")) apiRequests++; });
  await page.goto(base + "/maker.html?offline=1"); await ready(page);
  await page.locator("#prompt").fill("我想摸摸月亮");
  await page.locator("#submit-seal").click();
  await page.locator("#message").filter({ hasText: "还没有备好" }).waitFor();
  assert.equal(apiRequests, 0);
  await page.locator("#prepare-offline").click();
  await page.waitForFunction(() => /已备好大象|有些内容|准备时间/.test(document.getElementById("offline-status").textContent), null, { timeout: 120000 });
  assert.match(await page.locator("#offline-status").textContent(), /已备好大象/);
  pass("offline pack prepared with page modules and three real GLBs");
  await context.setOffline(true);
  await page.reload({ waitUntil: "domcontentloaded" }); await ready(page);
  for (const label of ["大象", "蓝鲸", "甬钟"]) await imprint(page, label);
  assert.equal(apiRequests, 0);
  assert.equal(await page.locator("#collection-grid button").count(), 3);
  await page.locator("#collection-link").click();
  await page.waitForFunction(() => document.title === "ready");
  assert.equal(await page.locator(".tag").count(), 3);
  await page.goto(base + "/maker.html?offline=1"); await ready(page);
  assert.equal(await page.locator("#collection-grid button").count(), 3);
  assert.equal(apiRequests, 0);
  pass("offline reload, three complete flip/rubbing flows, collection and group view", { apiRequests });
  await page.screenshot({ path: path.join(output, "maker-desktop-verified.png"), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 }); await noOverflow(page);
  await page.locator("#theme-dark").click();
  assert.equal(await page.locator("html").getAttribute("data-theme"), "dark");
  await page.screenshot({ path: path.join(output, "maker-mobile-dark.png"), fullPage: true });
  await page.locator("#theme-light").click();
  await page.screenshot({ path: path.join(output, "maker-mobile-light.png"), fullPage: true });
  pass("390px mobile layout and both themes without horizontal overflow");
  assert.deepEqual(errors, [], "offline page errors"); await context.close();

  const online = await browser.newContext(view);
  const work = await online.newPage(); const jsErrors = [];
  watch(work);
  work.on("pageerror", (error) => jsErrors.push(error.message));
  let postCount = 0, getCount = 0, release = false, dropFirst = true;
  let active = "text"; const keys = { text: [], photo: [] }; const photoBodies = [];
  const ids = { text: "a".repeat(32), photo: "b".repeat(32), failure: "c".repeat(32) };
  const job = (completed = false) => ({ id: ids[active], name: "seal_" + ids[active], status: active === "failure" ? "failed" : completed ? "completed" : "generating",
    createdAt: new Date().toISOString(), updatedAt: new Date().toISOString(), elapsedSeconds: 7, estimatedSeconds: active === "photo" ? 60 : 240, queuePosition: 0, quoteCredits: 1,
    seal: completed ? { ...defs[active === "photo" ? 7 : 0], name: "seal_" + ids[active], label: active === "photo" ? "测试甬钟" : "测试海豚" } : null,
    error: active === "failure" ? { code: "QUOTE_LIMIT", message: "报价超过单次积分上限，未创建生成任务。" } : null });
  await online.route("**/api/seals**", async (route) => {
    const request = route.request();
    if (request.method() === "POST") {
      postCount++;
      if (active !== "failure") keys[active].push(request.headers()["idempotency-key"]);
      if (active === "photo") {
        assert.ok(request.headers()["content-type"].startsWith("multipart/form-data; boundary="));
        photoBodies.push(request.postDataBuffer());
      }
      if (dropFirst) { dropFirst = false; await route.abort("failed"); return; }
      await route.fulfill({ status: 202, contentType: "application/json", body: JSON.stringify(job()) });
    } else {
      getCount++; await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(job(release)) });
    }
  });
  await work.goto(base + "/maker.html"); await ready(work);
  await work.locator("#text-tab").focus(); await work.keyboard.press("ArrowRight");
  assert.equal(await work.locator("#photo-tab").getAttribute("aria-selected"), "true");
  assert.equal(await work.evaluate(() => document.activeElement.id), "photo-tab");
  await work.keyboard.press("ArrowLeft");
  pass("keyboard tab navigation and semantic status region");
  await work.locator("#prompt").fill("我想摸摸测试海豚"); await work.locator("#submit-seal").click();
  await work.locator("#retry-request").filter({ hasText: "继续这次请求" }).waitFor();
  await work.reload();
  await work.waitForFunction(() => document.getElementById("status-title").textContent.includes("成形"));
  assert.equal(postCount, 2); assert.equal(new Set(keys.text).size, 1);
  await work.reload();
  await work.waitForFunction(() => document.getElementById("status-title").textContent.includes("成形"));
  assert.equal(postCount, 2, "known task reload must query instead of creating again");
  const beforeOffline = getCount;
  await work.locator("#mode-offline").click();
  await work.reload(); await work.waitForFunction(() => document.documentElement.dataset.makerReady === "true");
  assert.equal(getCount, beforeOffline, "explicit offline mode must not query a pending task");
  await work.locator("#offline-toggle").uncheck();
  release = true;
  await work.waitForFunction(() => document.getElementById("source-badge").textContent === "刚铸好的印" && !document.getElementById("flip").disabled);
  assert.equal(postCount, 2); assert.equal(await work.locator("#collection-grid button").count(), 1);
  pass("lost POST response, stable idempotency key, reload/resume and offline interruption");

  active = "photo"; release = false; dropFirst = true;
  await work.locator("#mode-photo").click();
  const image = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl6oYQAAAAASUVORK5CYII=", "base64");
  await work.locator("#photo").setInputFiles({ name: "sample.png", mimeType: "image/png", buffer: image });
  await work.locator("#seal-name").fill("测试甬钟");
  await work.locator("#submit-seal").click();
  await work.locator("#retry-request").filter({ hasText: "继续这次请求" }).waitFor();
  await work.reload();
  await work.waitForFunction(() => document.getElementById("status-title").textContent.includes("成形"));
  assert.equal(new Set(keys.photo).size, 1); assert.equal(photoBodies.length, 2);
  assert.equal(await work.locator("#photo").evaluate((input) => input.files.length), 1, "restored photo stays visible in the native input");
  for (const body of photoBodies) assert.ok(body.includes(image), "stored photo must survive reload unchanged");
  release = true;
  await work.waitForFunction(() => document.getElementById("result-label").textContent === "测试甬钟" && !document.getElementById("flip").disabled);
  assert.equal(await work.locator("#collection-grid button").count(), 2);
  pass("photo FormData and Blob survive a lost response and a page reload");

  active = "failure"; release = false; dropFirst = false;
  await work.locator("#mode-text").click(); await work.locator("#prompt").fill("一座冰川"); await work.locator("#seal-name").fill("冰川");
  await work.locator("#submit-seal").click();
  await work.locator("#message").filter({ hasText: "报价超过单次积分上限" }).waitFor();
  assert.equal(await work.locator("#input-fields").isDisabled(), false);
  pass("terminal provider rejection restores editable form with a readable error");
  assert.deepEqual(jsErrors, [], "online page errors"); await online.close();
} catch (error) {
  for (const [i, context] of browser.contexts().entries()) for (const [j, page] of context.pages().entries()) {
    await page.screenshot({ path: path.join(output, `maker-failure-${i}-${j}.png`), fullPage: true }).catch(() => {});
    console.error("Failure page:", page.url(), await page.locator("#offline-status").textContent().catch(() => ""));
    console.error("Cached paths:", await page.evaluate(async () => { const all = []; for (const key of await caches.keys()) for (const request of await (await caches.open(key)).keys()) all.push(new URL(request.url).pathname); return all; }).catch(() => []));
  }
  console.error("Diagnostics:", JSON.stringify(diagnostics.slice(-15), null, 2));
  throw error;
} finally {
  writeFileSync(path.join(output, "maker-checks.json"), JSON.stringify({ base, checks, count: checks.length, diagnostics }, null, 2));
  await browser.close();
}
console.log(`Completed ${checks.length} browser checks; no video was recorded.`);
