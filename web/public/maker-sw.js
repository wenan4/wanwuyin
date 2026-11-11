/* App shell and chosen models only. API responses and credentials are never cached. */
const CACHE = "wanwuyin-maker-v3";
// Development pages import Vite's HMR client. Keep its local module API available
// offline without opening a websocket; production bundles never request this.
const OFFLINE_DEV_CLIENT = `
const noop = () => {};
const styles = new Map();
export const createHotContext = () => ({data:{},accept:noop,acceptExports:noop,dispose:noop,prune:noop,invalidate:noop,on:noop,off:noop,send:noop,decline:noop});
export function updateStyle(id, css) { let node=styles.get(id); if(!node){node=document.createElement('style');document.head.append(node);styles.set(id,node);} node.textContent=css; }
export function removeStyle(id) { styles.get(id)?.remove(); styles.delete(id); }
export function injectQuery(url, query) { const parts=url.split('#');return parts[0]+(parts[0].includes('?')?'&':'?')+query+(parts[1]?'#'+parts[1]:''); }
export class ErrorOverlay extends HTMLElement { close(){this.remove();} }
`;
const MARKER = new URL("maker-offline-ready", self.registration.scope).href;
const essential = ["maker.html", "seals.json", "seal-cache.json"];
const allowed = (url) => {
  const u = new URL(url, self.registration.scope);
  return u.origin === self.location.origin && !u.pathname.startsWith("/api/")
    && !u.pathname.split("/").some((part) => part.startsWith(".") && part !== ".vite")
    && (/\.(html|m?js|ts|css|json|glb|svg|woff2?)$/.test(u.pathname) || u.pathname === "/@vite/client" || u.pathname.startsWith("/@id/"));
};
self.addEventListener("install", (event) => event.waitUntil((async () => {
  const cache = await caches.open(CACHE);
  for (const path of essential) {
    try { const response = await fetch(new URL(path, self.registration.scope)); if (response.ok) await cache.put(new URL(path, self.registration.scope), response); } catch { /* first visit may be offline */ }
  }
  await self.skipWaiting();
})()));
self.addEventListener("activate", (event) => event.waitUntil((async () => {
  for (const key of await caches.keys()) if (key.startsWith("wanwuyin-maker-") && key !== CACHE) await caches.delete(key);
  await self.clients.claim();
})()));
self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET" || !allowed(request.url)) return;
  event.respondWith((async () => {
    const cache = await caches.open(CACHE);
    if (new URL(request.url).pathname.endsWith(".glb")) {
      const saved = await cache.match(request, { ignoreVary: true });
      if (saved) return saved;
    }
    try {
      const response = await fetch(request);
      if (response.ok) { try { await cache.put(request, response.clone()); } catch { /* return a successful network response even when storage is full */ } }
      return response;
    } catch {
      if (new URL(request.url).pathname === "/@vite/client") {
        return new Response(OFFLINE_DEV_CLIENT, { headers: { "Content-Type": "application/javascript" } });
      }
      // Vite adds Vary: Origin to static modules. The same-origin offline copy is
      // identical even when a module request supplies an Origin header.
      const saved = await cache.match(request, { ignoreSearch: true, ignoreVary: true });
      if (saved) return saved;
      if (request.mode === "navigate" && new URL(request.url).pathname.endsWith("maker.html")) {
        const shell = await cache.match(new URL("maker.html", self.registration.scope)); if (shell) return shell;
      }
      return Response.error();
    }
  })());
});

function dependencies(text, url, type) {
  const urls = [];
  text = text.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
  for (const match of text.matchAll(/(?:\bfrom\s*|\bimport\s*\(?\s*|\b(?:src|href)\s*=\s*)["']([^"']+)["']/g)) {
    if (type.includes("javascript") && !/^[./]/.test(match[1])) continue;
    try { const next = new URL(match[1], url); next.hash = ""; if (allowed(next.href)) urls.push(next.href); } catch { /* not a URL */ }
  }
  return urls;
}

self.addEventListener("message", (event) => {
  const port = event.ports[0];
  if (!port || !event.data || !["PREPARE_OFFLINE", "CHECK_OFFLINE"].includes(event.data.type)) return;
  event.waitUntil((async () => {
    const cache = await caches.open(CACHE);
    if (event.data.type === "CHECK_OFFLINE") {
      const saved = await cache.match(MARKER);
      const urls = saved ? await saved.json() : [];
      const present = await Promise.all(urls.map((url) => cache.match(url)));
      port.postMessage({ type: "CHECKED", ready: urls.length > 0 && present.every(Boolean) }); return;
    }
    let failedPath = "";
    try {
      const queue = [...new Set((event.data.urls || []).filter((url) => typeof url === "string" && allowed(url)).map((url) => new URL(url, self.registration.scope).href))];
      if (!queue.length || queue.length > 100) throw new Error("invalid asset list");
      const seen = new Set(queue);
      for (let i = 0; i < queue.length; i++) {
        if (queue.length > 100) throw new Error("too many assets");
        const url = queue[i]; failedPath = new URL(url).pathname;
        // Vite distinguishes a stylesheet from a CSS-as-JS module using Accept.
        const response = await fetch(url, { cache: "reload", headers: url.split("?")[0].endsWith(".css") ? { Accept: "text/css" } : {} });
        if (!response.ok) throw new Error("asset unavailable");
        const type = response.headers.get("Content-Type") || "";
        if (/javascript|text\/html|text\/css/.test(type)) {
          for (const next of dependencies(await response.clone().text(), url, type)) if (!seen.has(next)) { seen.add(next); queue.push(next); }
        }
        await cache.put(url, response);
        port.postMessage({ type: "PROGRESS", done: i + 1, total: queue.length });
      }
      await cache.put(MARKER, new Response(JSON.stringify(queue), { headers: { "Content-Type": "application/json" } }));
      port.postMessage({ type: "COMPLETE", ready: true });
    } catch { port.postMessage({ type: "ERROR", message: "有些内容还没下载好，请联网后再试一次。", asset: failedPath }); }
  })());
});
