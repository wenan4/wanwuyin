// 万物印: each object is a seal. The knob is the Lux3D object, the face underneath carries
// the braille name plus a flattened relief of the object, and a foil rubbing of the face is
// one page of the child's book. Units are centimetres.
//   seal.html?seal=elephant              triptych: knob / face / rubbing
//   seal.html?view=set                   the whole collection with its rubbings
//   seal.html?view=anim&seal=elephant    touch → flip → rub → page (add &capture to drive it with window.seek(t))
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { RoomEnvironment } from "three/addons/environments/RoomEnvironment.js";
import { seal, page, rest, loadSeal, type SealDef, type Loaded } from "./seal-geo";
import { createSealAnimation } from "./seal-animation";
import { loadCatalog, mergeSeals } from "./maker-cache";
import { collection } from "./maker-store";

const q = new URLSearchParams(location.search);
const view = q.get("view") ?? "concept";
const capture = q.has("capture");
const ui = document.getElementById("ui")!;

const renderer = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true });
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
renderer.setSize(innerWidth, innerHeight);
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.PCFShadowMap;
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure = 0.8;
renderer.localClippingEnabled = true;
document.body.appendChild(renderer.domElement);

const scene = new THREE.Scene();
scene.background = new THREE.Color(0xefe9dd);
scene.environment = new THREE.PMREMGenerator(renderer).fromScene(new RoomEnvironment(), 0.04).texture;
const camera = new THREE.PerspectiveCamera(30, innerWidth / innerHeight, 0.1, 400);
const target = new THREE.Vector3(0, 2.6, 0);
camera.position.set(0, 11, 27);

const sun = new THREE.DirectionalLight(0xfff2e0, 1.7);
sun.castShadow = true;
sun.shadow.radius = 6;
scene.add(sun, sun.target);
const sideLight = new THREE.DirectionalLight(0xffead5, 0.78);
sideLight.position.set(-9, 3, 2);
scene.add(sideLight);
function shadowArea(r: number, map: number) {
  sun.shadow.mapSize.set(map, map);
  Object.assign(sun.shadow.camera, { left: -r, right: r, top: r, bottom: -r, far: 4 * r + 40 });
  sun.position.set(-0.5 * r, r, 0.62 * r);
  sun.shadow.camera.updateProjectionMatrix();
}
shadowArea(16, 2048);
const ground = new THREE.Mesh(new THREE.PlaneGeometry(400, 400), new THREE.ShadowMaterial({ opacity: 0.22 }));
ground.rotation.x = -Math.PI / 2;
ground.receiveShadow = true;
scene.add(ground);

function load(def: SealDef): Promise<Loaded> {
  return loadSeal(def, q.has("yaw") ? Number(q.get("yaw")) : undefined);
}

// ---- checks without eyes: where things land on screen (fractions of the viewport)
const tracked: [string, THREE.Object3D][] = [];
function screenBox(obj: THREE.Object3D) {
  const b = new THREE.Box3().setFromObject(obj);
  const out = { x0: 1e9, y0: 1e9, x1: -1e9, y1: -1e9 };
  for (let i = 0; i < 8; i++) {
    const p = new THREE.Vector3(i & 1 ? b.max.x : b.min.x, i & 2 ? b.max.y : b.min.y, i & 4 ? b.max.z : b.min.z).project(camera);
    out.x0 = Math.min(out.x0, (p.x + 1) / 2); out.x1 = Math.max(out.x1, (p.x + 1) / 2);
    out.y0 = Math.min(out.y0, (1 - p.y) / 2); out.y1 = Math.max(out.y1, (1 - p.y) / 2);
  }
  return out;
}
const r3 = (v: number) => Math.round(v * 1000) / 1000;
(window as any).boxes = () => {
  scene.updateMatrixWorld();
  camera.updateMatrixWorld();
  const out: Record<string, number[]> = {};
  for (const [n, o] of tracked) { const b = screenBox(o); out[n] = [b.x0, b.y0, b.x1, b.y1].map(r3); }
  document.querySelectorAll<HTMLElement>(".tag").forEach((e, i) => {
    const r = e.getBoundingClientRect();
    out[`tag${i}`] = [r.left / innerWidth, r.top / innerHeight, r.right / innerWidth, r.bottom / innerHeight].map(r3);
  });
  return out;
};

let pose: (t: number) => void = () => {};
let duration = 0;

const allDefs = mergeSeals(await loadCatalog(), collection());
const saved = collection();
const defs: SealDef[] = q.has("collection") && saved.length ? saved : allDefs;

if (view === "set") {
  // ---- the collection: every seal with its rubbing in front of it
  const got = await Promise.allSettled(defs.map(load));
  const all = got.flatMap((r) => (r.status === "fulfilled" ? [r.value] : []));
  // Tall knobs hide what is behind them, so the taller half stands in the back row.
  const built = all.map((s) => ({ s, sl: seal(s) })).sort((a, b) => b.sl.userData.height - a.sl.userData.height);
  const cols = Math.ceil(built.length / 2), dx = 12, dz = 23;
  const span = Math.max(1, cols / 4);
  shadowArea(44 * span, 4096);
  target.set(0, 1.5, 2.5);
  camera.position.set(0, 1.5 + 64 * span * Math.sin(0.56), 2.5 + 64 * span * Math.cos(0.56));
  camera.lookAt(target);
  camera.updateMatrixWorld();
  ui.innerHTML = `<div class="head"><b>万物印</b><span>摸不到的世界，一枚一枚握在手里</span></div>`;
  const back = document.createElement("a"); back.href = "maker.html"; back.textContent = "← 回到造印机";
  Object.assign(back.style, { position: "fixed", top: "16px", left: "20px", color: "#315247", pointerEvents: "auto", fontSize: "13px" });
  if (!capture) ui.append(back);
  built.forEach(({ s, sl }, i) => {
    const row = Math.floor(i / cols), n = row === 0 ? cols : built.length - cols;
    const x = ((i % cols) - (n - 1) / 2) * dx, z = (row - 0.5) * dz;
    sl.position.set(x, 0, z - 3.4);
    sl.rotation.y = 0.4;
    const pg = page(s);
    pg.position.set(x, 0, z + 2.6);
    scene.add(sl, pg);
    tracked.push([`${s.def.name}.seal`, sl], [`${s.def.name}.page`, pg]);
    const at = new THREE.Vector3(x, 0, z + 2.6 + pg.userData.side / 2 + 0.5).project(camera);
    const tag = document.createElement("div");
    tag.className = "tag";
    tag.style.left = `${((at.x + 1) / 2) * 100}%`;
    tag.style.top = `${((1 - at.y) / 2) * 100}%`;
    const name = document.createElement("b"), story = document.createElement("span");
    name.textContent = s.def.label; story.textContent = `${s.def.why} · ${s.def.real}`;
    tag.append(name, story);
    ui.appendChild(tag);
  });
} else {
  const def = defs.find((d) => d.name === (q.get("seal") ?? "elephant")) ?? defs[0];
  const s = await load(def);
  if (view === "anim") {
    // ---- touch → flip → rub → page
    const cap = document.createElement("div");
    cap.id = "cap";
    ui.appendChild(cap);
    const animation = createSealAnimation(s, scene, camera, target, (text) => { cap.textContent = text; }, q.has("plain"));
    pose = animation.pose;
    duration = animation.duration;
    tracked.push(["seal", animation.objects.seal], ["page", animation.objects.page]);
  } else {
    // ---- triptych: knob / face / rubbing
    const upright = seal(s);
    upright.position.x = -8.2;
    upright.rotation.y = 0.45;
    const flipped = seal(s);
    flipped.rotation.x = -1.9; // tipped onto its back so the face looks at the camera
    flipped.position.set(0, 0, -1);
    rest(flipped);
    const pg = page(s);
    pg.position.set(8.4, 0, 1.5);
    pg.rotation.set(0.46, -0.24, 0, "YXZ");
    rest(pg);
    scene.add(upright, flipped, pg);
    tracked.push(["upright", upright], ["flipped", flipped], ["page", pg]);
    ui.innerHTML = `<div class="caps">
      <div><b>印钮</b><span>Lux3D 生成的物件，用手认形状</span></div>
      <div><b>印面</b><span>翻过来才有答案：盲文名字 + 轮廓浮雕</span></div>
      <div><b>拓出来的一页</b><span>铺一张铜箔抹一遍，孩子自己拓出一本书</span></div></div>`;
  }
}

function frame(t: number) {
  pose(t);
  camera.lookAt(target);
  renderer.render(scene, camera);
}
addEventListener("resize", () => {
  camera.aspect = innerWidth / innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(innerWidth, innerHeight);
});
if (capture) {
  // Frame-accurate capture: seek, then wait until the frame has been presented.
  (window as any).seek = (t: number) => new Promise<void>((done) => {
    frame(t);
    requestAnimationFrame(() => requestAnimationFrame(() => done()));
  });
  frame(0);
} else {
  if (view !== "anim") new OrbitControls(camera, renderer.domElement).target.copy(target);
  const t0 = performance.now();
  renderer.setAnimationLoop(() => frame(duration ? ((performance.now() - t0) / 1000) % (duration + 1) : 0));
}
(window as any).duration = duration;
document.title = "ready";
