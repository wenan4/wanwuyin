import type { SealDef } from "./seal-geo";

export type CacheEntry = { name: string; aliases: string[]; offline?: boolean };

/** Exact normalized names only: a sentence containing two objects must not pick one. */
export function normalizeLabel(value: string): string {
  let text = value.normalize("NFKC").toLowerCase().replace(/[\p{P}\p{Z}\s]/gu, "");
  for (let i = 0; i < 3; i++) {
    text = text.replace(/^(?:请帮我|请给我|帮我|给我|我想要|我想|想要|想|请)/u, "")
      .replace(/^(?:摸一摸|摸摸|摸到|摸|看看|看|认识一下|认识|做一枚|做一个|做|生成一枚|生成一个|生成)/u, "")
      .replace(/^(?:一只|一头|一条|一座|一颗|一个|一枚|一下)/u, "");
  }
  return text.replace(/(?:的印章|的印|印章|吧|好吗)$/u, "");
}

export function cachedSeal(text: string, defs: SealDef[], entries: CacheEntry[]): SealDef | undefined {
  const name = normalizeLabel(text);
  if (!name) return undefined;
  return defs.find((def) => normalizeLabel(def.label) === name || entries.some((entry) =>
    entry.name === def.name && entry.aliases.some((alias) => normalizeLabel(alias) === name)));
}

export function validSeal(value: unknown): value is SealDef {
  if (!value || typeof value !== "object") return false;
  const s = value as Record<string, unknown>;
  return typeof s.name === "string" && /^[A-Za-z0-9_-]{1,80}$/.test(s.name)
    && typeof s.label === "string" && s.label.length > 0 && s.label.length <= 40
    && typeof s.cells === "string" && /^[\u2800-\u283f]{1,100}$/u.test(s.cells)
    && typeof s.why === "string" && typeof s.real === "string"
    && typeof s.glb === "string" && /^objects\/[A-Za-z0-9_-]+\/[A-Za-z0-9_.-]+\.glb$/.test(s.glb)
    && !s.glb.includes("..") && (s.yaw === undefined || typeof s.yaw === "number" && Number.isFinite(s.yaw));
}

export function mergeSeals(...groups: unknown[]): SealDef[] {
  const all = new Map<string, SealDef>();
  for (const group of groups) if (Array.isArray(group)) {
    for (const def of group) if (validSeal(def)) all.set(def.name, def);
  }
  return [...all.values()];
}

export async function loadCatalog(): Promise<SealDef[]> {
  const read = async (url: string) => {
    try { const r = await fetch(url); return r.ok ? await r.json() : []; } catch { return []; }
  };
  const [curated, generated] = await Promise.all([read("seals.json"), read("generated.json")]);
  return mergeSeals(curated, generated);
}
