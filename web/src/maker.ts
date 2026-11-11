import { cachedSeal, loadCatalog, mergeSeals, normalizeLabel, validSeal, type CacheEntry } from "./maker-cache";
import { clearPending, collect, collection, loadPending, savePending, type Pending } from "./maker-store";
import { offlineReady, prepareOffline } from "./maker-offline";
import { SealStage } from "./seal-stage";
import type { SealDef } from "./seal-geo";

type Job = { id: string; status: "queued" | "quoting" | "generating" | "downloading" | "completed" | "failed";
  elapsedSeconds: number; estimatedSeconds: number; queuePosition: number; quoteCredits: number | null;
  seal: SealDef | null; error: { code: string; message: string } | null };
const $ = <T extends HTMLElement>(id: string) => document.getElementById(id) as T;
const form = $<HTMLFormElement>("maker-form"), fields = $<HTMLFieldSetElement>("input-fields");
const prompt = $<HTMLTextAreaElement>("prompt"), name = $<HTMLInputElement>("seal-name");
const photo = $<HTMLInputElement>("photo"), photoPreview = $<HTMLImageElement>("photo-preview");
const why = $<HTMLSelectElement>("why"), real = $<HTMLInputElement>("real-size");
const textTab = $<HTMLButtonElement>("text-tab"), photoTab = $<HTMLButtonElement>("photo-tab");
const toggle = $<HTMLInputElement>("offline-toggle"), submit = $<HTMLButtonElement>("submit-seal");
const retry = $<HTMLButtonElement>("retry-request"), collectButton = $<HTMLButtonElement>("collect");
const touch = $<HTMLButtonElement>("touch"), flip = $<HTMLButtonElement>("flip"), rub = $<HTMLButtonElement>("rub");
const status = $("task-status"), statusTitle = $("status-title"), detail = $("status-detail");
const live = $("live-status"), badge = $("source-badge"), caption = $("stage-caption");
let defs: SealDef[] = [], entries: CacheEntry[] = [];
let mode: "text" | "photo" = "text", nameEdited = false, storyEdited = false;
let pending: Pending | undefined, current: SealDef | undefined, ready = false, moving = false;
let operation = 0, selection = 0, retryAction: (() => void) | undefined;
let requestController: AbortController | undefined, photoURL = "", announced = "";
let selectedPhoto: Blob | undefined;
let elapsedStart = 0, elapsedBase = 0, isWaiting = false;
toggle.checked = new URLSearchParams(location.search).get("offline") === "1";
const offline = () => toggle.checked || !navigator.onLine;
const say = (text: string) => { if (announced !== text) { live.textContent = text; announced = text; } };
const error = (text: string, action?: () => void, label = "继续查询这枚印") => {
  $("message").textContent = text; $("message").hidden = false;
  retry.hidden = !action; retry.textContent = label; retryAction = action;
};
const clearError = () => { $("message").hidden = true; retry.hidden = true; retryAction = undefined; };
retry.addEventListener("click", () => retryAction?.());

function modeCards() {
  $("mode-text").setAttribute("aria-pressed", String(!offline() && mode === "text"));
  $("mode-photo").setAttribute("aria-pressed", String(!offline() && mode === "photo"));
  $("mode-offline").setAttribute("aria-pressed", String(offline()));
}
function flow(index: number) {
  for (const item of document.querySelectorAll<HTMLElement>("[data-flow]")) {
    if (item.dataset.flow === String(index)) item.setAttribute("aria-current", "step"); else item.removeAttribute("aria-current");
  }
}
function theme(value: "light" | "dark") {
  document.documentElement.dataset.theme = value;
  $("theme-light").setAttribute("aria-pressed", String(value === "light"));
  $("theme-dark").setAttribute("aria-pressed", String(value === "dark"));
  try { localStorage.setItem("wanwuyin.theme.v1", value); } catch { /* theme can still change for this visit */ }
}
try { theme(localStorage.getItem("wanwuyin.theme.v1") === "dark" ? "dark" : "light"); } catch { theme("light"); }
$("theme-light").addEventListener("click", () => theme("light")); $("theme-dark").addEventListener("click", () => theme("dark"));

let stage: SealStage | undefined;
try { stage = new SealStage($("seal-stage"), (text) => { caption.textContent = text; }); }
catch { error("这个浏览器暂时无法显示三维画面。你仍可读名字、收藏印章，或换个浏览器再看形状。"); }

function inputMode(next: "text" | "photo", focus = false) {
  mode = next;
  textTab.setAttribute("aria-selected", String(next === "text"));
  photoTab.setAttribute("aria-selected", String(next === "photo"));
  textTab.tabIndex = next === "text" ? 0 : -1; photoTab.tabIndex = next === "photo" ? 0 : -1;
  $("text-panel").hidden = next !== "text"; $("photo-panel").hidden = next !== "photo";
  prompt.required = next === "text"; photo.required = next === "photo" && !selectedPhoto;
  modeCards();
  if (focus) (next === "text" ? textTab : photoTab).focus();
}
textTab.addEventListener("click", () => inputMode("text"));
photoTab.addEventListener("click", () => inputMode("photo"));
$("mode-text").addEventListener("click", () => { toggle.checked = false; inputMode("text"); availability(); prompt.focus(); });
$("mode-photo").addEventListener("click", () => { toggle.checked = false; inputMode("photo"); availability(); photo.focus(); });
$("mode-offline").addEventListener("click", () => { toggle.checked = true; if (!pending) inputMode("text"); availability(); });
for (const tab of [textTab, photoTab]) tab.addEventListener("keydown", (event) => {
  if (["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) {
    event.preventDefault(); inputMode(event.key === "Home" ? "text" : event.key === "End" ? "photo" : mode === "text" ? "photo" : "text", true);
  }
});
inputMode("text");
name.addEventListener("input", () => { nameEdited = true; });
why.addEventListener("change", () => { storyEdited = true; }); real.addEventListener("input", () => { storyEdited = true; });
prompt.addEventListener("input", () => {
  const found = cachedSeal(prompt.value, defs, entries);
  if (!nameEdited) name.value = found?.label ?? normalizeLabel(prompt.value).slice(0, 40);
  if (!storyEdited) { why.value = found?.why ?? "暂时摸不到"; real.value = found?.real ?? ""; }
});
photo.addEventListener("change", () => {
  if (photoURL) URL.revokeObjectURL(photoURL);
  selectedPhoto = undefined; photo.required = mode === "photo";
  const file = photo.files?.[0]; photoPreview.hidden = !file;
  if (file && file.size > 8 * 1024 * 1024) {
    photo.value = ""; photoPreview.hidden = true; error("照片有点大，请换一张 8 MB 以内的照片。"); return;
  }
  if (file && file.type && !["image/png", "image/jpeg", "image/webp"].includes(file.type)) {
    photo.value = ""; photoPreview.hidden = true; error("请使用 PNG、JPEG 或 WebP 照片。"); return;
  }
  if (file) { selectedPhoto = file; photo.required = false; photoURL = URL.createObjectURL(file); photoPreview.src = photoURL; clearError(); }
});

function refreshForm() {
  fields.disabled = Boolean(pending);
  $<HTMLButtonElement>("mode-text").disabled = Boolean(pending);
  $<HTMLButtonElement>("mode-photo").disabled = Boolean(pending);
  modeCards();
  submit.querySelector("span")!.textContent = offline() ? "摸摸已备好的印" : "铸成我的印";
  $("submit-hint").textContent = offline()
    ? "现在是离线体验。试试大象、蓝鲸、甬钟，或选择下面已备好的印。"
    : "新的印，通常需要 1–4 分钟。已备好的，可以立刻摸一摸。";
}

function refreshActions() {
  touch.disabled = !ready || moving; flip.disabled = !ready || moving; rub.disabled = !ready || moving || !flip.classList.contains("is-current");
  for (const id of ["turn-left", "turn-right", "zoom-in", "zoom-out"]) $<HTMLButtonElement>(id).disabled = !ready || moving;
  const saved = current && collection().some((s) => s.name === current!.name);
  collectButton.disabled = !ready || Boolean(saved);
  collectButton.textContent = saved ? "已放进印集 ✓" : "＋ 放进我的印集";
}

function step(button: HTMLButtonElement) {
  for (const b of [touch, flip, rub]) b.classList.toggle("is-current", b === button);
}

async function showSeal(def: SealDef, source: "cache" | "online" = "cache", land = false) {
  const ticket = ++selection;
  current = def; ready = false; moving = false; refreshActions(); step(touch);
  badge.textContent = source === "cache" ? "已备好的印" : "刚铸好的印";
  badge.dataset.source = source;
  $("result-kind").textContent = source === "cache" ? "用已备好的印，先摸摸它的世界" : "新的世界，已经来到你的桌上";
  $("result-label").textContent = def.label;
  $("result-story").textContent = `${def.why} · ${def.real}`;
  $("result-braille").textContent = def.cells;
  $("result-braille").setAttribute("aria-label", `${def.label}的盲文`);
  caption.textContent = "正在把这枚印放到桌上……";
  try {
    const shown = await stage?.show(def, land);
    if (shown === false) return;
    if (ticket !== selection) return;
    ready = true; flow(0); caption.textContent = "先认一认形状，再翻过来读它的名字。";
    refreshActions(); say(`${def.label}的印已经放好。${source === "cache" ? "这是已准备好的印章。" : ""}可以翻面读名字，再拓出一页书。`);
  } catch {
    if (ticket !== selection) return;
    error("这枚印的三维模型还没加载好。请联网重试，或选一枚已保存的离线印。", () => { void showSeal(def, source); }, "重新加载这枚印");
    say("模型加载失败，可以重新加载或选择已备好的印。");
  }
}

async function animate(button: HTMLButtonElement, from: number, to: number, text: string) {
  if (!ready || moving) return;
  const ticket = selection;
  moving = true; refreshActions();
  await stage?.play(from, to);
  if (ticket !== selection) return;
  moving = false; step(button); flow(button === touch ? 0 : button === flip ? 2 : 3); refreshActions(); say(text);
}
touch.addEventListener("click", () => { void animate(touch, 0, 2.3, "印章已转回正面，可以认形状。"); });
flip.addEventListener("click", () => { void animate(flip, 2.4, 4.2, `印面上写着${current?.label}，还有它的轮廓浮雕。现在可以拓一页书。`); });
rub.addEventListener("click", () => { void animate(rub, 4.2, 12, `${current?.label}的一页书拓好了，可以把这枚印放进印集。`); });
$("turn-left").addEventListener("click", () => stage?.turn(-1)); $("turn-right").addEventListener("click", () => stage?.turn(1));
$("zoom-in").addEventListener("click", () => stage?.zoom(0.9)); $("zoom-out").addEventListener("click", () => stage?.zoom(1.1));

function renderCollection() {
  const all = collection();
  $("collection-count").textContent = String(all.length);
  $("collection-empty").hidden = all.length > 0;
  const grid = $("collection-grid"); grid.replaceChildren();
  for (const seal of all) {
    const tile = document.createElement("button"); tile.type = "button"; tile.className = "collection-tile";
    tile.setAttribute("aria-label", `查看印集中的${seal.label}`);
    const cells = document.createElement("span"); cells.className = "tile-dots"; cells.textContent = seal.cells; cells.setAttribute("aria-hidden", "true");
    const title = document.createElement("strong"); title.textContent = seal.label;
    const story = document.createElement("small"); story.textContent = seal.why + " · 翻面有答案";
    tile.append(cells, title, story);
    tile.addEventListener("click", () => { clearError(); void showSeal(seal); $("seal-stage").scrollIntoView({ block: "center", behavior: "smooth" }); });
    grid.append(tile);
  }
}
collectButton.addEventListener("click", () => {
  if (!current) return;
  try { collect(current); renderCollection(); refreshActions(); say(`${current.label}已放进我的印集，下次回来还在。`); }
  catch { error("这次没有保存成功。请确认浏览器允许保存数据，再试一次。"); }
});

function taskUI(job: Pick<Job, "status" | "elapsedSeconds" | "estimatedSeconds" | "queuePosition">) {
  status.hidden = false;
  elapsedStart = Date.now(); elapsedBase = job.elapsedSeconds;
  isWaiting = !["completed", "failed"].includes(job.status);
  flow(isWaiting ? 1 : 0);
  $("busy-track").hidden = !isWaiting;
  const titles = { queued: "你的想象，已经排好队", quoting: "正在准备这枚新印", generating: "印石正在慢慢成形", downloading: "正把它带到你的桌上", completed: "你的印，做好了", failed: "这次没能铸好" };
  statusTitle.textContent = titles[job.status];
  detail.textContent = job.status === "queued" && job.queuePosition > 0 ? `前面还有 ${Math.max(0, job.queuePosition - 1)} 个等待中的任务。`
    : job.status === "generating" ? `通常约需 ${Math.ceil(job.estimatedSeconds / 60)} 分钟。可以先认一认下面已备好的印，离开后回来也能继续。`
    : job.status === "completed" ? "翻过来读名字，再亲手拓出一页书。新印也已经放进你的印集。"
    : job.status === "downloading" ? "模型、盲文名字和轮廓浮雕正在就位。" : job.status === "failed" ? "可以修改描述或照片后再试一次。" : "还没有开始成形，请稍等一会儿。";
  say(`${titles[job.status]}。${detail.textContent}`);
}
setInterval(() => {
  const seconds = Math.max(0, Math.floor(elapsedBase + (isWaiting ? (Date.now() - elapsedStart) / 1000 : 0)));
  $("elapsed").textContent = `已等 ${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
}, 1000);

class APIError extends Error { constructor(message: string, readonly status: number) { super(message); } }
async function api(path: string, init: RequestInit = {}): Promise<Job> {
  const controller = new AbortController(); requestController = controller;
  const timeout = setTimeout(() => controller.abort(), 25000);
  try {
    const response = await fetch(path, { ...init, signal: controller.signal, cache: "no-store" });
    const data = await response.json();
    if (!response.ok) throw new APIError(typeof data?.error?.message === "string" ? data.error.message : "暂时没能连接造印机。", response.status);
    if (!data || typeof data.id !== "string" || !/^[a-f0-9]{32}$/.test(data.id)
      || !["queued", "quoting", "generating", "downloading", "completed", "failed"].includes(data.status)
      || data.status === "completed" && !validSeal(data.seal)) throw new Error("造印机的回复暂时无法识别。");
    return data as Job;
  } finally { clearTimeout(timeout); if (requestController === controller) requestController = undefined; }
}

async function persist() {
  if (!pending) return;
  try { await savePending(pending); }
  catch { error("浏览器暂时无法保存这次进度。铸造完成前请保持页面打开。"); }
}

async function runPending() {
  if (!pending || offline()) return;
  const token = ++operation, draft = pending;
  requestController?.abort(); clearError(); refreshForm();
  if (!current || !isWaiting) {
    selection++; stage?.busy(); current = undefined; ready = false; moving = false; refreshActions();
    badge.textContent = "铸造中"; badge.dataset.source = "online";
    $("result-label").textContent = "正在铸造"; $("result-story").textContent = "一枚属于你的新印，正在路上。";
    $("result-braille").textContent = ""; caption.textContent = "你的想象，正在慢慢成形。";
  }
  const accept = async (job: Job) => {
    if (token !== operation || offline()) return true;
    taskUI(job);
    if (job.status === "completed" && job.seal) {
      pending = undefined; await clearPending().catch(() => {}); refreshForm();
      defs = mergeSeals(defs, [job.seal]);
      try { collect(job.seal); renderCollection(); } catch { error("印做好了，但浏览器没有保存印集。请稍后再点一次收藏。"); }
      await showSeal(job.seal, "online", true); return true;
    }
    if (job.status === "failed") {
      pending = undefined; await clearPending().catch(() => {}); refreshForm();
      error(job.error?.message ?? "这次没有铸好。请修改描述或照片后重新提交。", () => { (mode === "text" ? prompt : name).focus(); }, "修改后再试");
      return true;
    }
    return false;
  };
  try {
    if (!draft.id) {
      taskUI({ status: "queued", elapsedSeconds: Math.floor((Date.now() - draft.started) / 1000), estimatedSeconds: draft.photo ? 60 : 240, queuePosition: 0 });
      const headers: Record<string, string> = { "Idempotency-Key": draft.key };
      let body: BodyInit;
      if (draft.photo) {
        const data = new FormData(); data.set("label", draft.label); data.set("why", draft.why); data.set("real", draft.real); data.set("image", draft.photo, "photo.png"); body = data;
      } else { headers["Content-Type"] = "application/json"; body = JSON.stringify({ label: draft.label, why: draft.why, real: draft.real, prompt: draft.prompt }); }
      const job = await api("/api/seals", { method: "POST", headers, body });
      if (token !== operation) return;
      draft.id = job.id; await persist();
      if (await accept(job)) return;
    }
    while (token === operation && !offline()) {
      if (await accept(await api(`/api/seals/${draft.id}`))) return;
      await new Promise((resolve) => setTimeout(resolve, 2500));
    }
  } catch (exception) {
    if (token !== operation || offline()) return;
    const rejected = exception instanceof APIError && exception.status >= 400 && exception.status < 500 && !draft.id;
    if (rejected) { pending = undefined; await clearPending().catch(() => {}); refreshForm(); }
    isWaiting = false; $("busy-track").hidden = true;
    const text = exception instanceof APIError ? exception.message : "连接暂时断开了。你的请求已记住，继续查询会接着原来的任务，不会重复铸造。";
    error(text, rejected ? () => prompt.focus() : () => { void runPending(); }, rejected ? "修改输入" : draft.id ? "继续查询这枚印" : "继续这次请求");
    say(text);
  }
}

form.addEventListener("submit", (event) => {
  event.preventDefault(); if (pending) return;
  const cached = mode === "text" ? cachedSeal(prompt.value, defs, entries) : undefined;
  if (cached) { clearError(); void showSeal(cached, "cache", true); return; }
  if (offline()) {
    error(mode === "photo" ? "照片铸造需要联网。现在可以先选大象、蓝鲸或甬钟，完整体验翻面和拓印。" : "这件还没有备好。离线时可以试试大象、蓝鲸或甬钟。", () => $("quick-picks").querySelector("button")?.focus(), "先摸摸已备好的印"); return;
  }
  const file = photo.files?.[0] ?? selectedPhoto;
  if (mode === "photo" && !file) { error("请先选择一张照片。"); photo.focus(); return; }
  pending = { key: crypto.randomUUID(), label: name.value.trim(), why: why.value,
    real: real.value.trim() || "真实尺寸待补充", started: Date.now(),
    ...(mode === "photo" ? { photo: file } : { prompt: `${name.value.trim()}。${prompt.value.trim()}。写实风格，完整物件，白色背景，不带底座。` }) };
  refreshForm();
  void persist().then(() => runPending());
});

function availability() {
  operation++; requestController?.abort();
  const url = new URL(location.href);
  if (toggle.checked) url.searchParams.set("offline", "1"); else url.searchParams.delete("offline");
  history.replaceState(null, "", url);
  refreshForm();
  if (offline()) {
    if (pending) { isWaiting = false; detail.textContent = "任务进度已留好，联网后可以继续查询。现在可以先摸摸已备好的印。"; $("busy-track").hidden = true; }
    say("已进入离线体验。选择大象、蓝鲸或甬钟，不会启动在线生成。");
  } else if (pending) void runPending();
}
toggle.addEventListener("change", availability);
window.addEventListener("offline", availability); window.addEventListener("online", availability);

$("prepare-offline").addEventListener("click", () => {
  const button = $<HTMLButtonElement>("prepare-offline");
  if (!navigator.onLine) { $("offline-status").textContent = "准备时需要联网。已经备好的印仍然可以使用。"; return; }
  button.disabled = true; $("offline-status").textContent = "正在备好三枚印，请保持页面打开……";
  void prepareOffline(defs, entries, (done, total) => { $("offline-status").textContent = `正在保存离线内容 ${done} / ${total}，请稍等。`; })
    .then(() => { $("offline-status").textContent = "已备好大象、蓝鲸和甬钟。断网或刷新页面，都可以继续体验。"; button.textContent = "重新准备离线体验 ↻"; say("三枚离线印已备好，断网后也能继续。"); })
    .catch((exception: Error) => { $("offline-status").textContent = exception.message; })
    .finally(() => { button.disabled = false; });
});

async function boot() {
  const [catalog, cache] = await Promise.all([loadCatalog(), fetch("seal-cache.json").then((r) => r.ok ? r.json() : []).catch(() => [])]);
  defs = mergeSeals(catalog, collection());
  entries = Array.isArray(cache) ? cache.filter((e): e is CacheEntry => e && typeof e.name === "string" && Array.isArray(e.aliases) && e.aliases.every((s: unknown) => typeof s === "string")) : [];
  for (const entry of entries.filter((e) => e.offline)) {
    const def = defs.find((s) => s.name === entry.name); if (!def) continue;
    const button = document.createElement("button"); button.type = "button"; button.textContent = def.label;
    button.addEventListener("click", () => { clearError(); void showSeal(def, "cache", true); });
    $("quick-picks").append(button);
  }
  renderCollection(); refreshForm();
  if (defs.length) void showSeal(defs.find((d) => d.name === "elephant") ?? defs[0]);
  else error("印章清单暂时没有加载好，请联网刷新页面。");
  void offlineReady().then((saved) => { if (saved) $("offline-status").textContent = "三枚离线印已备好。断网后仍可以刷新页面，继续翻面和拓印。"; }).catch(() => {});
  try { pending = await loadPending(); } catch { /* current-session use still works without IndexedDB */ }
  if (pending) {
    const p = pending;
    inputMode(p.photo ? "photo" : "text"); name.value = p.label; why.value = p.why; real.value = p.real;
    if (p.prompt) prompt.value = p.prompt;
    if (p.photo) {
      selectedPhoto = p.photo; photo.required = false;
      photoURL = URL.createObjectURL(p.photo); photoPreview.src = photoURL; photoPreview.hidden = false;
      try { const transfer = new DataTransfer(); transfer.items.add(p.photo instanceof File ? p.photo : new File([p.photo], "photo.png", { type: p.photo.type })); photo.files = transfer.files; } catch { /* the saved Blob still submits when FileList assignment is unavailable */ }
    }
    refreshForm();
    if (!offline()) void runPending();
    else { status.hidden = false; statusTitle.textContent = "还有一枚印等你回来"; detail.textContent = "已保留上次任务，联网后继续查询。现在可以先体验已备好的印。"; $("busy-track").hidden = true; }
  }
  document.documentElement.dataset.makerReady = "true";
}
void boot();
