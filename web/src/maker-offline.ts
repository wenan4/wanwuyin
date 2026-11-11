import type { CacheEntry } from "./maker-cache";
import type { SealDef } from "./seal-geo";

async function worker(): Promise<ServiceWorker> {
  if (!("serviceWorker" in navigator)) throw new Error("这个浏览器暂不支持保存离线体验，请使用较新的浏览器。");
  await navigator.serviceWorker.register("maker-sw.js", { scope: "./" });
  const registration = await navigator.serviceWorker.ready;
  if (!registration.active) throw new Error("离线工具还没有准备好，请稍后重试。");
  return registration.active;
}

function message(target: ServiceWorker, data: unknown, progress?: (done: number, total: number) => void): Promise<boolean> {
  return new Promise((resolve, reject) => {
    const channel = new MessageChannel();
    const timeout = setTimeout(() => { channel.port1.close(); reject(new Error("准备时间较长，请确认网络后重试。")); }, 180000);
    channel.port1.onmessage = (event) => {
      if (event.data.type === "PROGRESS") { progress?.(event.data.done, event.data.total); return; }
      clearTimeout(timeout); channel.port1.close();
      if (event.data.type === "ERROR") { console.warn("Offline preparation failed for asset:", event.data.asset); reject(new Error(event.data.message)); }
      else resolve(Boolean(event.data.ready));
    };
    target.postMessage(data, [channel.port2]);
  });
}

export async function offlineReady(): Promise<boolean> {
  return message(await worker(), { type: "CHECK_OFFLINE" });
}

export async function prepareOffline(defs: SealDef[], entries: CacheEntry[], progress: (done: number, total: number) => void) {
  const target = await worker();
  const names = entries.filter((e) => e.offline).map((e) => e.name);
  const models = defs.filter((d) => names.includes(d.name)).map((d) => d.glb);
  if (models.length < 3) throw new Error("三枚离线印还没加载齐，请联网刷新后重试。");
  const resources = (performance.getEntriesByType("resource") as PerformanceResourceTiming[])
    .filter((entry) => !entry.responseStatus || entry.responseStatus >= 200 && entry.responseStatus < 300)
    .map((e) => e.name).filter((u) => {
    const url = new URL(u, location.href); return url.origin === location.origin && !url.pathname.startsWith("/api/");
  });
  const urls = [...new Set(["maker.html", "seal.html", "seals.json", "seal-cache.json", ...models, ...resources])]
    .map((url) => new URL(url, location.href).href);
  return message(target, { type: "PREPARE_OFFLINE", urls }, progress);
}
