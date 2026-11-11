// Run with Node 24: node --test tools/test-maker.mjs (no browser or network).
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { cachedSeal, mergeSeals, normalizeLabel, validSeal } from "../src/maker-cache.ts";

const defs = JSON.parse(readFileSync(new URL("../public/seals.json", import.meta.url), "utf8"));
const entries = JSON.parse(readFileSync(new URL("../public/seal-cache.json", import.meta.url), "utf8"));

test("all eight cached objects resolve with natural-language names and aliases", () => {
  for (const entry of entries) for (const alias of entry.aliases) {
    assert.equal(cachedSeal(`我想摸摸一只${alias}的印章！`, defs, entries)?.name, entry.name);
  }
  assert.equal(cachedSeal("请帮我生成一个蓝鲸的印章。", defs, entries)?.name, "blue_whale");
  assert.equal(cachedSeal("BLUE WHALE", defs, entries)?.name, "blue_whale");
});
test("unknown objects and multiple-object prompts do not silently return a wrong seal", () => {
  for (const text of ["", "月亮", "蚂蚁上树", "蓝鲸和土星", "大象坐在土星上", "我想摸摸长颈鹿"]) {
    assert.equal(cachedSeal(text, defs, entries), undefined, text);
  }
});
test("normalization preserves the object while removing polite prefixes and punctuation", () => {
  assert.equal(normalizeLabel("请给我摸一摸一头蓝鲸，好吗？"), "蓝鲸");
  assert.equal(normalizeLabel("我想摸摸土星的印章！"), "土星");
});
test("catalog rejects cross-origin model URLs and traversal before any model request", () => {
  assert.ok(defs.every(validSeal));
  for (const patch of [{ glb: "https://example/model.glb" }, { glb: "objects/../model.glb" },
    { name: "../escape" }, { cells: "not-braille" }, { yaw: Infinity }]) assert.equal(validSeal({ ...defs[0], ...patch }), false);
});
test("generated and collected seals merge by id and preserve refreshed metadata", () => {
  const newer = { ...defs[0], real: "测试更新" };
  const merged = mergeSeals(defs, [newer], [null, {}]);
  assert.equal(merged.length, defs.length); assert.equal(merged[0].real, "测试更新");
});
test("offline pack contains at least three distinct existing GLBs and reuses paid assets", () => {
  const offline = entries.filter((entry) => entry.offline).map((entry) => defs.find((d) => d.name === entry.name));
  assert.equal(offline.length, 3); assert.equal(new Set(offline.map((d) => d.glb)).size, 3);
  for (const def of offline) assert.ok(readFileSync(new URL(`../public/${def.glb}`, import.meta.url)).length > 12);
});
