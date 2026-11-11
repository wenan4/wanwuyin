import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { RoomEnvironment } from "three/addons/environments/RoomEnvironment.js";
import {
  MELODIES, type Material, type Meta, Vibration, decayRates, frequencies, midiLabel, midiToFreq,
  noteName, pitchMode, renderStrike, strikeAmplitudes,
} from "./modal";

const $ = <T extends HTMLElement>(id: string) => document.getElementById(id) as T;
const VIS_MAX = 0.05; // peak visual displacement, as a fraction of the object's size
const HEAT = new THREE.Color("#ff6a1a");

/** Everything shared by all copies of one shape (geometry is normalised to max extent 1). */
interface Shape {
  meta: Meta; geom: THREE.BufferGeometry; material: THREE.MeshStandardMaterial; original: THREE.MeshStandardMaterial;
  modes: Float32Array;
  rest: Float32Array; restNormals: Float32Array; maxAbs: Float64Array; textured: boolean; height: number;
}

/** One physical copy: its own size, tuning, vibration state and deforming geometry. */
interface Inst {
  mesh: THREE.Mesh; geom: THREE.BufferGeometry; vib: Vibration; size: number;
  freqs: Float64Array; decays: Float64Array; pitchK: number; refPeak: number; deformed: boolean;
  baseColors: Float32Array;
}

// ---------- scene ----------
const stage = $("stage");
const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
renderer.toneMapping = THREE.ACESFilmicToneMapping;
stage.appendChild(renderer.domElement);
const scene = new THREE.Scene();
scene.background = new THREE.Color("#15171c");
scene.environment = new THREE.PMREMGenerator(renderer).fromScene(new RoomEnvironment(), 0.04).texture;
const sun = new THREE.DirectionalLight(0xffffff, 1.1);
sun.position.set(1, 2, 1.5);
scene.add(sun);
scene.add(new THREE.GridHelper(4, 40, 0x4a4f5a, 0x2c3038)); // 10 cm cells
const camera = new THREE.PerspectiveCamera(40, 1, 0.005, 50);
const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;
new ResizeObserver(() => {
  const { clientWidth: w, clientHeight: h } = stage;
  renderer.setSize(w, h);
  camera.aspect = w / h;
  camera.updateProjectionMatrix();
}).observe(stage);

// ---------- state ----------
let materials: Record<string, Material> = {};
let shape: Shape | null = null;
let main: Inst | null = null;
let rack: { inst: Inst; midi: number }[] = [];
let matKey = "porcelain";
let tau = 4e-4;
let audio: AudioContext | null = null;
let bus: GainNode, wet: GainNode;
const ripples: { mesh: THREE.Mesh; t: number }[] = [];
let playTimers: number[] = [];

const active = (): Inst[] => (rack.length ? rack.map((r) => r.inst) : main ? [main] : []);

function ensureAudio(): AudioContext {
  if (audio) return audio;
  audio = new AudioContext();
  const comp = audio.createDynamicsCompressor();
  comp.connect(audio.destination);
  bus = audio.createGain();
  bus.connect(comp);
  const conv = audio.createConvolver();
  conv.buffer = impulseResponse(audio, 1.8);
  wet = audio.createGain();
  wet.gain.value = ($("reverb") as HTMLInputElement).checked ? 0.22 : 0;
  bus.connect(conv).connect(wet).connect(comp);
  return audio;
}

function impulseResponse(ctx: AudioContext, seconds: number): AudioBuffer {
  const n = Math.floor(seconds * ctx.sampleRate), buf = ctx.createBuffer(2, n, ctx.sampleRate);
  for (let ch = 0; ch < 2; ch++) {
    const d = buf.getChannelData(ch);
    for (let k = 0; k < n; k++) d[k] = (Math.random() * 2 - 1) * Math.exp((-6.9 * k) / n);
  }
  return buf;
}

// ---------- loading ----------
async function loadShape(name: string): Promise<Shape> {
  const base = `objects/${name}/`;
  const [meta, gltf, modesBuf] = await Promise.all([
    fetch(base + "meta.json").then((r) => r.json() as Promise<Meta>),
    new GLTFLoader().loadAsync(base + "model.glb"),
    fetch(base + "modes.bin").then((r) => r.arrayBuffer()),
  ]);
  gltf.scene.updateMatrixWorld(true);
  let src: THREE.Mesh | undefined;
  gltf.scene.traverse((o) => { if (!src && (o as THREE.Mesh).isMesh) src = o as THREE.Mesh; });
  if (!src) throw new Error("GLB has no mesh");
  // The pipeline flattened node transforms into world space; do the same here.
  const geom = src.geometry.clone().applyMatrix4(src.matrixWorld);
  const nV = geom.attributes.position.count;
  const modes = new Float32Array(modesBuf);
  if (nV !== meta.vertexCount || modes.length !== meta.modeCount * nV * 3) {
    throw new Error(`vertex mismatch: glb ${nV}, meta ${meta.vertexCount}, modes ${modes.length}`);
  }
  geom.computeBoundingBox();
  const bb = geom.boundingBox!, ext = bb.getSize(new THREE.Vector3());
  const s = 1 / Math.max(ext.x, ext.y, ext.z);
  geom.translate(-(bb.min.x + bb.max.x) / 2, -bb.min.y, -(bb.min.z + bb.max.z) / 2).scale(s, s, s);
  if (!geom.attributes.normal) geom.computeVertexNormals();
  geom.computeBoundingBox();
  geom.computeBoundingSphere();

  const maxAbs = new Float64Array(meta.modeCount);
  for (let i = 0; i < meta.modeCount; i++) {
    let m = 0;
    for (let v = 0, o = i * nV * 3; v < nV; v++, o += 3) m = Math.max(m, Math.hypot(modes[o], modes[o + 1], modes[o + 2]));
    maxAbs[i] = m;
  }
  const textured = meta.source === "lux3d";
  const material = textured ? (src.material as THREE.MeshStandardMaterial).clone() : new THREE.MeshStandardMaterial();
  const original = material.clone();
  material.vertexColors = true;
  return {
    meta, geom, material, original, modes, maxAbs, textured, height: geom.boundingBox!.max.y,
    rest: (geom.attributes.position.array as Float32Array).slice(),
    restNormals: (geom.attributes.normal.array as Float32Array).slice(),
  };
}

function makeInst(size: number): Inst {
  const s = shape!;
  const geom = s.geom.clone();
  const nV = s.meta.vertexCount;
  geom.setAttribute("color", new THREE.BufferAttribute(new Float32Array(nV * 3).fill(1), 3));
  const mesh = new THREE.Mesh(geom, s.material);
  scene.add(mesh);
  const inst: Inst = {
    mesh, geom, size, vib: new Vibration(s.meta.modeCount), freqs: new Float64Array(), decays: new Float64Array(),
    pitchK: 0, refPeak: 1, deformed: false, baseColors: new Float32Array(nV * 3).fill(1),
  };
  paint(inst);
  retune(inst);
  return inst;
}

function disposeInst(inst: Inst) {
  scene.remove(inst.mesh);
  inst.geom.dispose();
}

async function loadObject(name: string) {
  stopPlaying();
  stopSand();
  neighbours = [];
  clearRack();
  if (main) disposeInst(main);
  shape = await loadShape(name);
  matKey = shape.meta.material;
  ($("material") as HTMLSelectElement).value = matKey;
  applyLook();
  main = makeInst(shape.meta.sizeM);
  buildStrikeButtons();
  buildVariantSelect();
  fillSandModes();
  frame([main]);
  updateInfo();
}

// ---------- Lux3D material repaint variants: same mesh and UVs, new textures + acoustics ----------
const MAP_KEYS = ["map", "metalnessMap", "roughnessMap", "normalMap", "aoMap"] as const;

async function applyVariant(index: number) {
  if (!shape) return;
  const v = shape.meta.variants?.[index];
  let src = shape.original;
  matKey = shape.meta.material;
  if (v) {
    const gltf = await new GLTFLoader().loadAsync(`objects/${shape.meta.name}/${v.glb}`);
    gltf.scene.traverse((o) => { if ((o as THREE.Mesh).isMesh) src = (o as THREE.Mesh).material as THREE.MeshStandardMaterial; });
    matKey = v.material;
  }
  for (const k of MAP_KEYS) shape.material[k] = src[k];
  shape.material.metalness = src.metalness;
  shape.material.roughness = src.roughness;
  shape.material.needsUpdate = true;
  ($("material") as HTMLSelectElement).value = matKey;
  retuneAll();
}

function buildVariantSelect() {
  const sel = $("variant") as HTMLSelectElement, vs = shape!.meta.variants ?? [];
  $("variantRow").hidden = !vs.length;
  sel.innerHTML = [`<option value="-1">原始（${materials[shape!.meta.material].name}）</option>`,
    ...vs.map((v, i) => `<option value="${i}">${v.label}</option>`)].join("");
}

// ---------- physics + appearance ----------
function applyLook() {
  if (!shape || shape.textured) return;
  const mat = materials[matKey];
  shape.material.metalness = mat.metalness;
  shape.material.roughness = mat.roughness;
  shape.material.transparent = mat.opacity !== undefined;
  shape.material.opacity = mat.opacity ?? 1;
}

function paint(inst: Inst) {
  if (shape!.textured) return; // Lux3D textures carry the look; vertex colours stay white
  const c = new THREE.Color(materials[matKey].color);
  for (let v = 0; v < inst.baseColors.length; v += 3) inst.baseColors.set([c.r, c.g, c.b], v);
  inst.geom.attributes.color.array.set(inst.baseColors);
  inst.geom.attributes.color.needsUpdate = true;
}

function normalAt(v: number): [number, number, number] {
  const n = shape!.restNormals;
  return [n[v * 3], n[v * 3 + 1], n[v * 3 + 2]];
}

function retune(inst: Inst) {
  const s = shape!, mat = materials[matKey];
  inst.freqs = frequencies(s.meta, mat, inst.size);
  inst.decays = decayRates(inst.freqs, mat);
  const amps = strikeAmplitudes(s.modes, s.meta.vertexCount, s.meta.strikeVertex, normalAt(s.meta.strikeVertex), inst.freqs, tau);
  inst.pitchK = Math.max(pitchMode(amps), 0);
  inst.vib.retune(inst.freqs, inst.decays, inst.pitchK);
  if (audio) inst.refPeak = peakOf(renderStrike(audio, inst.freqs, inst.decays, amps));
  inst.mesh.scale.setScalar(inst.size);
}

function retuneAll() {
  for (const i of active()) retune(i);
  if (main) fillSandModes();
  if (sand && main) sand.osc.frequency.value = main.freqs[sand.mode];
  updateInfo();
}

function peakOf(buf: AudioBuffer): number {
  let m = 0;
  for (const v of buf.getChannelData(0)) m = Math.max(m, Math.abs(v));
  return m || 1;
}

function strike(inst: Inst, vertex: number, point?: THREE.Vector3) {
  const s = shape!, ctx = ensureAudio();
  if (ctx.state === "suspended") ctx.resume();
  if (inst.refPeak === 1) retune(inst);
  const amps = strikeAmplitudes(s.modes, s.meta.vertexCount, vertex, normalAt(vertex), inst.freqs, tau);
  const src = ctx.createBufferSource();
  const g = ctx.createGain();
  src.buffer = renderStrike(ctx, inst.freqs, inst.decays, amps);
  g.gain.value = 0.55 / inst.refPeak; // hits near a node line come out quieter, as they should
  src.connect(g).connect(bus);
  src.start();

  let p = 0;
  for (let i = 0; i < amps.length; i++) p += Math.abs(amps[i]) * s.maxAbs[i];
  if (p > 0) inst.vib.strike(amps, VIS_MAX / p);
  addRipple(inst, point ?? vertexWorld(inst, vertex), normalAt(vertex));
  let k = 0;
  for (let i = 1; i < amps.length; i++) if (Math.abs(amps[i]) > Math.abs(amps[k])) k = i;
  const heard = document.getElementById("heard");
  if (heard) heard.textContent = `${noteName(inst.freqs[k])} · ${Math.round(inst.freqs[k])} Hz`;
}

function vertexWorld(inst: Inst, v: number): THREE.Vector3 {
  const r = shape!.rest;
  return inst.mesh.localToWorld(new THREE.Vector3(r[v * 3], r[v * 3 + 1], r[v * 3 + 2]));
}

function addRipple(inst: Inst, at: THREE.Vector3, n: [number, number, number]) {
  const ring = new THREE.Mesh(new THREE.RingGeometry(0.8, 1, 40),
    new THREE.MeshBasicMaterial({ color: 0xffd28a, transparent: true, side: THREE.DoubleSide, depthWrite: false }));
  const normal = new THREE.Vector3(...n).normalize();
  ring.position.copy(at).addScaledVector(normal, inst.size * 0.004);
  ring.quaternion.setFromUnitVectors(new THREE.Vector3(0, 0, 1), normal);
  ring.userData.size = inst.size;
  scene.add(ring);
  ripples.push({ mesh: ring, t: 0 });
}

function deform(inst: Inst) {
  const s = shape!, { geom, vib, baseColors } = inst;
  const nV = s.meta.vertexCount, pos = geom.attributes.position.array as Float32Array, rest = s.rest;
  pos.set(rest);
  for (let i = 0; i < vib.im.length; i++) {
    const d = vib.im[i];
    if (Math.abs(vib.re[i]) + Math.abs(d) < 1e-5) continue;
    for (let k = 0, o = i * nV * 3; k < nV * 3; k++) pos[k] += d * s.modes[o + k];
  }
  const col = geom.attributes.color.array as Float32Array;
  const heat = ($("heat") as HTMLInputElement).checked && !sand; // sand needs a clean plate
  for (let v = 0; v < nV * 3; v += 3) {
    const t = heat ? Math.min(1, Math.hypot(pos[v] - rest[v], pos[v + 1] - rest[v + 1], pos[v + 2] - rest[v + 2]) / (VIS_MAX * 0.6)) : 0;
    col[v] = baseColors[v] * (1 - t) + HEAT.r * t;
    col[v + 1] = baseColors[v + 1] * (1 - t) + HEAT.g * t;
    col[v + 2] = baseColors[v + 2] * (1 - t) + HEAT.b * t;
  }
  geom.attributes.position.needsUpdate = true;
  geom.attributes.color.needsUpdate = true;
  geom.computeVertexNormals();
  inst.deformed = true;
}

function restore(inst: Inst) {
  (inst.geom.attributes.position.array as Float32Array).set(shape!.rest);
  (inst.geom.attributes.normal.array as Float32Array).set(shape!.restNormals);
  inst.geom.attributes.color.array.set(inst.baseColors);
  for (const k of ["position", "normal", "color"]) inst.geom.attributes[k].needsUpdate = true;
  inst.deformed = false;
}

// ---------- Chladni sand: drive one mode, grains hop off antinodes and settle on node lines ----------
interface Sand {
  points: THREE.Points; at: Int32Array; offset: Float32Array; mode: number; amp: number;
  osc: OscillatorNode; gain: GainNode; acc: number; bounce: Float32Array;
}
let sand: Sand | null = null;
let neighbours: Int32Array[] = [];
const SAND_COUNT = 3000, SAND_HOP_HZ = 60, SAND_MAX_STEPS = 5, SAND_REST = 0.03, SAND_DOWNHILL = 0.5;

function buildNeighbours() {
  const s = shape!, nV = s.meta.vertexCount, sets: Set<number>[] = Array.from({ length: nV }, () => new Set());
  const idx = s.geom.index ? (s.geom.index.array as ArrayLike<number>) : Array.from({ length: nV }, (_, i) => i);
  for (let t = 0; t < idx.length; t += 3) {
    const [a, b, c] = [idx[t], idx[t + 1], idx[t + 2]];
    sets[a].add(b).add(c); sets[b].add(a).add(c); sets[c].add(a).add(b);
  }
  neighbours = sets.map((x) => Int32Array.from(x));
}

const facesUp = (v: number) => shape!.restNormals[v * 3 + 1] > 0.6;

function startSand(mode: number) {
  stopSand();
  if (!shape || !main) return;
  if (!neighbours.length) buildNeighbours();
  const up: number[] = [];
  for (let v = 0; v < shape.meta.vertexCount; v++) if (facesUp(v)) up.push(v);
  if (!up.length) { $("sandStatus").textContent = "这件物体没有朝上的面可以撒沙"; return; }
  const at = new Int32Array(SAND_COUNT), offset = new Float32Array(SAND_COUNT * 2);
  for (let i = 0; i < SAND_COUNT; i++) {
    at[i] = up[Math.floor(Math.random() * up.length)];
    offset[i * 2] = (Math.random() - 0.5) * 0.012;
    offset[i * 2 + 1] = (Math.random() - 0.5) * 0.012;
  }
  const geom = new THREE.BufferGeometry();
  geom.setAttribute("position", new THREE.BufferAttribute(new Float32Array(SAND_COUNT * 3), 3));
  const points = new THREE.Points(geom, new THREE.PointsMaterial({ color: 0x3a2410, size: 0.009, sizeAttenuation: true }));
  main.mesh.add(points); // inherits the object's scale
  const ctx = ensureAudio();
  if (ctx.state === "suspended") ctx.resume();
  const osc = ctx.createOscillator(), gain = ctx.createGain();
  osc.frequency.value = main.freqs[mode];
  gain.gain.value = 0.08; // the "speaker" driving the plate
  osc.connect(gain).connect(bus);
  osc.start();
  // Out-of-plane motion is what throws grains: on a bending plate the top surface's
  // in-plane motion peaks exactly on the node lines, so |phi| alone never reaches zero there.
  const nV = shape.meta.vertexCount, bounce = new Float32Array(nV);
  let top = 0;
  for (let v = 0, o = mode * nV * 3; v < nV; v++, o += 3) {
    const n = shape.restNormals;
    bounce[v] = Math.abs(shape.modes[o] * n[v * 3] + shape.modes[o + 1] * n[v * 3 + 1] + shape.modes[o + 2] * n[v * 3 + 2]);
    if (facesUp(v)) top = Math.max(top, bounce[v]);
  }
  for (let v = 0; v < nV; v++) bounce[v] /= top || 1;
  sand = { points, at, offset, mode, amp: (VIS_MAX * 0.35) / shape.maxAbs[mode], osc, gain, acc: 0, bounce };
  // Chladni figures read best from above.
  const t = controls.target, d = camera.position.distanceTo(t);
  camera.position.set(t.x, t.y + d * 0.92, t.z + d * 0.4);
  controls.update();
  $("sandToggle").textContent = "停止";
  $("sandStatus").textContent = `正在驱动第 ${mode + 1} 阶：${noteName(main.freqs[mode])} · ${Math.round(main.freqs[mode])} Hz`;
}

function stopSand() {
  if (!sand) return;
  sand.osc.stop();
  sand.points.parent?.remove(sand.points);
  sand.points.geometry.dispose();
  sand = null;
  $("sandToggle").textContent = "撒沙";
  $("sandStatus").textContent = "";
}

function stepSand(dt: number) {
  if (!sand || !shape || !main) return;
  const m = sand.mode, bounce = sand.bounce;
  // Keep the driven mode at a steady amplitude (a speaker, not a single hit).
  const r = Math.hypot(main.vib.re[m], main.vib.im[m]) || 1;
  main.vib.re[m] *= sand.amp / r;
  main.vib.im[m] = main.vib.im[m] ? main.vib.im[m] * (sand.amp / r) : sand.amp;
  sand.acc += dt;
  while (sand.acc > 1 / SAND_HOP_HZ) {
    sand.acc -= 1 / SAND_HOP_HZ;
    for (let i = 0; i < SAND_COUNT; i++) {
      // Grains bounce further where the surface moves more, and tend to land in the
      // quieter neighbour (downhill in |w|); offline this put 95% on node lines in 60 hops.
      let cur = sand.at[i];
      for (let k = bounce[cur] < SAND_REST ? 0 : Math.ceil(bounce[cur] * SAND_MAX_STEPS); k > 0; k--) {
        const nb = neighbours[cur];
        let w = -1;
        if (Math.random() < SAND_DOWNHILL) {
          for (const c of nb) if (facesUp(c) && (w < 0 || bounce[c] < bounce[w])) w = c;
        } else {
          const c = nb[Math.floor(Math.random() * nb.length)];
          if (c !== undefined && facesUp(c)) w = c;
        }
        if (w >= 0) cur = w;
      }
      sand.at[i] = cur;
    }
  }
  const pos = main.geom.attributes.position.array as Float32Array;
  const out = sand.points.geometry.attributes.position.array as Float32Array;
  for (let i = 0; i < SAND_COUNT; i++) {
    const v = sand.at[i];
    out[i * 3] = pos[v * 3] + sand.offset[i * 2];
    out[i * 3 + 1] = pos[v * 3 + 1] + 0.002;
    out[i * 3 + 2] = pos[v * 3 + 2] + sand.offset[i * 2 + 1];
  }
  sand.points.geometry.attributes.position.needsUpdate = true;
}

function fillSandModes() {
  const sel = $("sandMode") as HTMLSelectElement;
  sel.innerHTML = Array.from(main!.freqs.slice(0, 12), (f, i) =>
    `<option value="${i}">第 ${i + 1} 阶 · ${noteName(f).split(" ")[0]} · ${Math.round(f)} Hz</option>`).join("");
}

// ---------- chime rack: one shape, resized per note (f ∝ 1/size) ----------
function buildRack(key: string) {
  if (!shape || !main) return;
  stopPlaying();
  stopSand();
  clearRack();
  const notes = [...new Set(MELODIES[key].notes.map(([m]) => m))].sort((a, b) => a - b);
  const f0 = main.freqs[main.pitchK];
  let sizes = notes.map((m) => (main!.size * f0) / midiToFreq(m));
  // Whole-octave shifts keep the set physically reasonable (6–60 cm).
  while (Math.max(...sizes) > 0.6) sizes = sizes.map((s) => s / 2);
  while (Math.min(...sizes) < 0.06) sizes = sizes.map((s) => s * 2);
  main.mesh.visible = false;
  let x = 0;
  rack = notes.map((midi, i) => {
    const inst = makeInst(sizes[i]);
    x += sizes[i] * 0.5;
    inst.mesh.position.x = x;
    x += sizes[i] * 0.5 + 0.03 + sizes[i] * 0.15;
    return { inst, midi };
  });
  const mid = x / 2;
  for (const r of rack) r.inst.mesh.position.x -= mid;
  frame(rack.map((r) => r.inst));
  ($("rackToggle") as HTMLButtonElement).textContent = "回到单件";
  $("play").removeAttribute("disabled");
  updateInfo();
}

function clearRack() {
  for (const r of rack) disposeInst(r.inst);
  rack = [];
  if (main) main.mesh.visible = true;
  ($("rackToggle") as HTMLButtonElement).textContent = "排成编钟架";
  $("play").setAttribute("disabled", "");
}

function playMelody(key: string) {
  if (!rack.length) buildRack(key);
  stopPlaying();
  let t = 0;
  for (const [midi, beats] of MELODIES[key].notes) {
    const r = rack.find((x) => x.midi === midi)!;
    playTimers.push(window.setTimeout(() => strike(r.inst, shape!.meta.strikeVertex), t * 1000));
    t += beats * 0.84;
  }
}

function stopPlaying() {
  playTimers.forEach(clearTimeout);
  playTimers = [];
}

function frame(insts: Inst[]) {
  const box = new THREE.Box3();
  for (const i of insts) box.expandByObject(i.mesh);
  const c = box.getCenter(new THREE.Vector3()), size = box.getSize(new THREE.Vector3());
  const r = Math.max(size.x, size.y * 1.4, 0.05);
  controls.target.set(c.x, c.y, c.z);
  camera.position.set(c.x + r * 0.7, c.y + r * 0.55, c.z + r * 1.6);
  controls.update();
}

// ---------- UI ----------
const SIZE_MIN = 0.03, SIZE_MAX = 0.8;
const sizeToSlider = (s: number) => (1000 * Math.log(s / SIZE_MIN)) / Math.log(SIZE_MAX / SIZE_MIN);
const sliderToSize = (v: number) => SIZE_MIN * (SIZE_MAX / SIZE_MIN) ** (v / 1000);
const TAU_SOFT = 2e-3, TAU_HARD = 8e-5;

function buildStrikeButtons() {
  const box = $("strikePoints");
  box.innerHTML = "";
  const pts = shape!.meta.strikePoints ?? [{ name: "默认", vertex: shape!.meta.strikeVertex, shape: [] }];
  pts.forEach((p, i) => {
    const b = document.createElement("button");
    b.className = "secondary";
    b.textContent = pts.length > 1 ? `敲${p.name}（${i + 1}）` : "敲一下（空格）";
    b.onclick = () => strikeAll(p.vertex);
    box.appendChild(b);
  });
}

function strikeAll(vertex: number) {
  if (rack.length) return playMelody(($("melody") as HTMLSelectElement).value);
  if (main) strike(main, vertex);
}

function updateInfo() {
  if (!shape || !main) return;
  const mat = materials[matKey];
  ($("size") as HTMLInputElement).value = String(sizeToSlider(main.size));
  $("sizeLabel").textContent = `${(main.size * 100).toFixed(1)} cm`;
  const f0 = main.freqs[main.pitchK];
  const list = Array.from(main.freqs.slice(0, 8), (f) => `${Math.round(f)}`).join(" · ");
  const rackInfo = rack.length
    ? `<div class="muted">编钟架：${rack.map((r) => `${midiLabel(r.midi)} ${(r.inst.size * 100).toFixed(1)}cm`).join("，")}</div>`
    : "";
  $("info").innerHTML = `
    <div class="pitch">${noteName(f0)} <small>${Math.round(f0)} Hz</small></div>
    <div>${shape.meta.label} · ${mat.name} · ${(main.size * 100).toFixed(1)} cm${shape.meta.wallMm ? ` · 壁厚 ${shape.meta.wallMm} mm` : ""}</div>
    <div class="muted">最近一次听到：<span id="heard">—</span></div>
    <div class="muted">前 8 阶模态 (Hz)：${list}</div>
    ${rackInfo}
    <div class="muted">画面慢放约 ${Math.round(f0 / 1.4).toLocaleString()}× ，振幅放大显示</div>
    ${shape.textured ? '<div class="muted">Lux3D 贴图保留；材质切换只改声学参数</div>' : ""}`;
}

function animateSize(target: number) {
  if (!main) return;
  const from = main.size, t0 = performance.now();
  const tick = (now: number) => {
    const k = Math.min(1, (now - t0) / 600), e = k * k * (3 - 2 * k);
    main!.size = from * (target / from) ** e;
    retune(main!);
    updateInfo();
    if (k < 1) requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
}

function hammerLabel(v: number) {
  return v < 30 ? "软槌" : v < 70 ? "木槌" : "金属锤";
}

async function init() {
  materials = await fetch("materials.json").then((r) => r.json());
  const manifest: { name: string; label: string }[] = await fetch("objects/manifest.json").then((r) => r.json());
  const objSel = $("object") as HTMLSelectElement, matSel = $("material") as HTMLSelectElement;
  objSel.innerHTML = manifest.map((o) => `<option value="${o.name}">${o.label}</option>`).join("");
  matSel.innerHTML = Object.entries(materials).map(([k, m]) => `<option value="${k}">${m.name}</option>`).join("");
  const noteSel = $("note") as HTMLSelectElement;
  for (let m = 48; m <= 96; m++) noteSel.add(new Option(midiLabel(m), String(m)));
  const melSel = $("melody") as HTMLSelectElement;
  melSel.innerHTML = Object.entries(MELODIES).map(([k, m]) => `<option value="${k}">${m.label}</option>`).join("");

  objSel.onchange = () => loadObject(objSel.value).catch(showError);
  matSel.onchange = () => {
    matKey = matSel.value;
    applyLook();
    for (const i of [main!, ...rack.map((r) => r.inst)]) paint(i);
    retuneAll();
  };
  ($("size") as HTMLInputElement).oninput = (e) => {
    if (!main) return;
    if (rack.length) clearRack();
    main.size = sliderToSize(+(e.target as HTMLInputElement).value);
    retuneAll();
  };
  noteSel.onchange = () => {
    if (!noteSel.value || !main) return;
    if (rack.length) clearRack();
    const target = main.size * (main.freqs[main.pitchK] / midiToFreq(+noteSel.value));
    animateSize(Math.min(Math.max(target, SIZE_MIN), SIZE_MAX));
  };
  const hammer = $("hammer") as HTMLInputElement;
  const setHammer = () => {
    tau = TAU_SOFT * (TAU_HARD / TAU_SOFT) ** (+hammer.value / 100);
    $("hammerLabel").textContent = hammerLabel(+hammer.value);
    if (shape) retuneAll();
  };
  hammer.oninput = setHammer;
  ($("reverb") as HTMLInputElement).onchange = (e) => { if (wet) wet.gain.value = (e.target as HTMLInputElement).checked ? 0.22 : 0; };
  $("rackToggle").onclick = () => (rack.length ? (clearRack(), frame([main!]), updateInfo()) : buildRack(melSel.value));
  melSel.onchange = () => rack.length && buildRack(melSel.value);
  $("play").onclick = () => playMelody(melSel.value);
  ($("variant") as HTMLSelectElement).onchange = (e) => applyVariant(+(e.target as HTMLSelectElement).value).catch(showError);
  $("sandToggle").onclick = () => (sand ? stopSand() : startSand(+($("sandMode") as HTMLSelectElement).value));
  ($("sandMode") as HTMLSelectElement).onchange = (e) => sand && startSand(+(e.target as HTMLSelectElement).value);
  window.addEventListener("keydown", (e) => {
    if (!shape || document.activeElement?.tagName === "SELECT") return;
    const pts = shape.meta.strikePoints ?? [];
    if (e.code === "Space") { e.preventDefault(); strikeAll(shape.meta.strikeVertex); }
    const n = Number(e.key);
    if (n >= 1 && n <= pts.length) strikeAll(pts[n - 1].vertex);
  });

  const ray = new THREE.Raycaster(), down = new THREE.Vector2();
  renderer.domElement.addEventListener("pointerdown", (e) => down.set(e.clientX, e.clientY));
  renderer.domElement.addEventListener("pointerup", (e) => {
    if (!shape || Math.hypot(e.clientX - down.x, e.clientY - down.y) > 4) return;
    const r = renderer.domElement.getBoundingClientRect();
    ray.setFromCamera(new THREE.Vector2(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1), camera);
    const insts = active();
    const hit = ray.intersectObjects(insts.map((i) => i.mesh))[0];
    if (!hit?.face) return;
    const inst = insts.find((i) => i.mesh === hit.object)!;
    const local = inst.mesh.worldToLocal(hit.point.clone()), r0 = shape.rest;
    const best = [hit.face.a, hit.face.b, hit.face.c].reduce((a, b) =>
      local.distanceToSquared(new THREE.Vector3(r0[a * 3], r0[a * 3 + 1], r0[a * 3 + 2])) <=
      local.distanceToSquared(new THREE.Vector3(r0[b * 3], r0[b * 3 + 1], r0[b * 3 + 2])) ? a : b);
    strike(inst, best, hit.point);
  });

  setHammer();
  const params = new URLSearchParams(location.search);
  const first = params.get("object") ?? manifest[0].name;
  objSel.value = first;
  await loadObject(first);
  // Debug/demo hooks: ?strike=1 hits after load, ?rack=jasmine builds and plays a chime rack.
  const rackKey = params.get("rack");
  if (rackKey && MELODIES[rackKey]) { melSel.value = rackKey; buildRack(rackKey); setTimeout(() => playMelody(rackKey), 300); }
  if (params.has("strike")) setTimeout(() => strikeAll(shape!.meta.strikeVertex), 300);
  const variant = params.get("variant"); // ?variant=0 shows the first Lux3D repaint
  if (variant !== null) { ($("variant") as HTMLSelectElement).value = variant; await applyVariant(+variant); }
  const sandMode = params.get("sand"); // ?sand=3 drives mode 3; &settle=120 fast-forwards hops
  if (sandMode !== null) {
    startSand(+sandMode);
    if (sand) { sand.acc = +(params.get("settle") ?? 0) / SAND_HOP_HZ; stepSand(0); }
  }
}

function showError(e: unknown) {
  $("info").innerHTML = `<div class="error">${String(e)}</div>`;
  console.error(e);
}

const clock = new THREE.Clock();
renderer.setAnimationLoop(() => {
  const dt = Math.min(clock.getDelta(), 0.05);
  for (const inst of active()) {
    if (inst.vib.energy() > 1e-12 || (sand && inst === main)) {
      inst.vib.step(dt);
      if (sand && inst === main) stepSand(dt);
      deform(inst);
    } else if (inst.deformed) restore(inst);
  }
  for (let i = ripples.length - 1; i >= 0; i--) {
    const r = ripples[i];
    r.t += dt;
    r.mesh.scale.setScalar(r.mesh.userData.size * (0.01 + r.t * 0.25));
    (r.mesh.material as THREE.MeshBasicMaterial).opacity = Math.max(0, 1 - r.t / 0.5);
    if (r.t > 0.5) { scene.remove(r.mesh); r.mesh.geometry.dispose(); ripples.splice(i, 1); }
  }
  controls.update();
  renderer.render(scene, camera);
});

init().catch(showError);
