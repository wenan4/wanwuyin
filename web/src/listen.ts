// 听触: the page a tactile print's NFC tag opens. No graphics needed: every control
// is a large button with a spoken label, and sound is synthesised from the
// precomputed strike shapes in meta.json.
import {
  type Material, type Meta, amplitudesFromShape, decayRates, dominantMode, frequencies, noteName, renderStrike,
} from "./modal";

const $ = <T extends HTMLElement>(id: string) => document.getElementById(id) as T;
const PRINT_SIZE_M = 0.10; // tactile prints are ~10 cm
const TAU = 4e-4;

let materials: Record<string, Material> = {};
let meta: Meta | null = null;
let audio: AudioContext | null = null;
let bus: GainNode;

function ensureAudio(): AudioContext {
  if (!audio) {
    audio = new AudioContext();
    const comp = audio.createDynamicsCompressor();
    comp.connect(audio.destination);
    bus = audio.createGain();
    bus.connect(comp);
  }
  if (audio.state === "suspended") audio.resume();
  return audio;
}

function speak(text: string) {
  if (!("speechSynthesis" in window)) return;
  speechSynthesis.cancel();
  const u = new SpeechSynthesisUtterance(text);
  u.lang = "zh-CN";
  u.rate = 0.95;
  speechSynthesis.speak(u);
}

function say(text: string, voice = true) {
  $("status").textContent = text;
  if (voice) speak(text);
}

const cm = (m: number) => `${Math.round(m * 100)} 厘米`;

/** Hit one strike point at a given physical size; returns the note heard. */
function hit(pointIndex: number, sizeM: number): string {
  const ctx = ensureAudio(), mat = materials[meta!.material];
  const freqs = frequencies(meta!, mat, sizeM);
  const amps = amplitudesFromShape(meta!.strikePoints[pointIndex].shape, freqs, TAU);
  const buf = renderStrike(ctx, freqs, decayRates(freqs, mat), amps);
  let peak = 0;
  for (const v of buf.getChannelData(0)) peak = Math.max(peak, Math.abs(v));
  const src = ctx.createBufferSource(), g = ctx.createGain();
  src.buffer = buf;
  g.gain.value = 0.6 / (peak || 1);
  src.connect(g).connect(bus);
  src.start();
  return noteName(freqs[dominantMode(amps)]).split(" ")[0];
}

function describe(): string {
  const m = meta!, mat = materials[m.material];
  const pts = m.strikePoints.length > 1 ? `可以敲${m.strikePoints.map((p) => p.name).join("和")}，声音不一样。` : "";
  return `${m.label}。${mat.name}做的，实物大约 ${cm(m.sizeM)}${m.wallMm ? `，壁厚大约 ${m.wallMm} 毫米` : ""}。` +
    `手里的模型大约 ${cm(PRINT_SIZE_M)}。${pts}`;
}

function buildButtons() {
  const m = meta!, box = $("buttons");
  box.innerHTML = "";
  const actions: { label: string; run: () => void }[] = [];
  m.strikePoints.forEach((p, i) => {
    const where = m.strikePoints.length > 1 ? `敲${p.name}` : "敲一下";
    actions.push({ label: `${where}，真实大小`, run: () => say(`${where}，真实大小 ${cm(m.sizeM)}，${hit(i, m.sizeM)}`, false) });
  });
  actions.push({ label: "敲一下，手里大小", run: () => say(`手里大小 ${cm(PRINT_SIZE_M)}，${hit(0, PRINT_SIZE_M)}`, false) });
  actions.push({ label: "大小对比：小、中、真实", run: () => compareSizes() });
  actions.push({ label: "介绍", run: () => say(describe()) });
  actions.forEach((a, i) => {
    const b = document.createElement("button");
    b.textContent = `${i + 1}. ${a.label}`;
    b.onclick = a.run;
    box.appendChild(b);
  });
  window.onkeydown = (e) => {
    if (document.activeElement?.tagName === "SELECT") return;
    const n = Number(e.key);
    if (n >= 1 && n <= actions.length) { e.preventDefault(); actions[n - 1].run(); }
    if (e.code === "Space") { e.preventDefault(); actions[0].run(); }
    if (e.key.toLowerCase() === "i") actions[actions.length - 1].run();
  };
}

/** Same object at three sizes, one second apart: scale heard as pitch. */
function compareSizes() {
  const m = meta!, sizes = [PRINT_SIZE_M, Math.sqrt(PRINT_SIZE_M * m.sizeM), m.sizeM];
  say(`同一件东西，三个大小：${sizes.map(cm).join("，")}。越大越低。`);
  sizes.forEach((s, i) => setTimeout(() => hit(0, s), 3200 + i * 1400));
}

async function load(name: string) {
  meta = await fetch(`objects/${name}/meta.json`).then((r) => r.json());
  if (!meta!.strikePoints?.length) throw new Error("meta.json has no strikePoints; rebuild with build_demo.py");
  $("title").textContent = meta!.label;
  document.title = `听触 · ${meta!.label}`;
  buildButtons();
  say(`已打开：${meta!.label}。按 1 敲击，按 I 听介绍。`, false);
}

async function init() {
  materials = await fetch("materials.json").then((r) => r.json());
  const manifest: { name: string; label: string }[] = await fetch("objects/manifest.json").then((r) => r.json());
  const sel = $("object") as HTMLSelectElement;
  sel.innerHTML = manifest.map((o) => `<option value="${o.name}">${o.label}</option>`).join("");
  sel.onchange = () => load(sel.value).catch((e) => say(String(e), false));
  const first = new URLSearchParams(location.search).get("object") ?? manifest[0].name;
  sel.value = first;
  await load(first);
}

init().catch((e) => say(String(e), false));
