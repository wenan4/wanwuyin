// 万物印: each object is a seal. The knob is the Lux3D object, the face underneath carries
// the braille name plus a flattened relief of the object, and a foil rubbing of the face is
// one page of the child's book. Units are centimetres.
//   seal.html?seal=elephant              triptych: knob / face / rubbing
//   seal.html?view=set                   the whole collection with its rubbings
//   seal.html?view=anim&seal=elephant    touch → flip → rub → page (add &capture to drive it with window.seek(t))
import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { RoomEnvironment } from "three/addons/environments/RoomEnvironment.js";

type SealDef = { name: string; label: string; cells: string; why: string; real: string; glb: string; yaw?: number };
type Loaded = { def: SealDef; model: THREE.Object3D; W: number; yaw: number };

const q = new URLSearchParams(location.search);
const view = q.get("view") ?? "concept";
const capture = q.has("capture");
const ui = document.getElementById("ui")!;

const H = 1.5; // plinth height
const RELIEF = 0.15; // how far the relief rises from the face
const DOT = 0.25, CELL = 0.6, DOT_R = 0.085, DOT_H = 0.07;
const DOT_POS: [number, number][] = [[0, 0], [0, 1], [0, 2], [1, 0], [1, 1], [1, 2]];

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

const stone = new THREE.MeshPhysicalMaterial({ color: 0x2c4a41, roughness: 0.45, clearcoat: 0.4, clearcoatRoughness: 0.45 }); // dark green seal stone
const ink = new THREE.MeshStandardMaterial({ color: 0xc0200f, roughness: 0.85 }); // cinnabar paste on the raised parts
const foil = new THREE.MeshStandardMaterial({ color: 0xa55c33, metalness: 1, roughness: 0.5 }); // copper foil
const burnished = new THREE.MeshStandardMaterial({ color: 0xf0b07a, metalness: 1, roughness: 0.22 }); // the rubbed high points shine

const brailleWidth = (cells: string) => ([...cells].length - 1) * CELL + DOT;

/** Wrap the model so its bounding box is centred at the origin and fits (w, h, d). */
function fitted(src: THREE.Object3D, w: number, h: number, d: number, mat?: THREE.Material, yaw = 0) {
  const obj = src.clone(true);
  obj.rotation.y = yaw;
  obj.traverse((o) => {
    const m = o as THREE.Mesh;
    if (!m.isMesh) return;
    m.castShadow = m.receiveShadow = true;
    if (mat) m.material = mat;
  });
  const box = new THREE.Box3().setFromObject(obj);
  const size = box.getSize(new THREE.Vector3());
  const s = Math.min(w / size.x, h / size.y, d / size.z);
  obj.position.copy(box.getCenter(new THREE.Vector3())).multiplyScalar(-s);
  obj.scale.setScalar(s);
  const g = new THREE.Group();
  g.add(obj);
  g.userData.size = size.multiplyScalar(s);
  return g;
}

/** Braille domes in the local xy plane, raised along +z, centred on the origin. */
function braille(cells: string, mat: THREE.Material) {
  const g = new THREE.Group();
  const geo = new THREE.SphereGeometry(DOT_R, 16, 10);
  const width = brailleWidth(cells);
  [...cells].forEach((ch, ci) => {
    const bits = ch.codePointAt(0)! - 0x2800;
    DOT_POS.forEach(([col, row], b) => {
      if (!((bits >> b) & 1)) return;
      const dot = new THREE.Mesh(geo, mat);
      dot.scale.z = DOT_H / DOT_R;
      dot.position.set(ci * CELL + col * DOT - width / 2, (1 - row) * DOT, 0);
      dot.castShadow = true;
      g.add(dot);
    });
  });
  return g;
}

/** Relief + braille laid out in the xy plane (z out of the surface): what the seal face carries. */
function face(s: Loaded, mat: THREE.Material) {
  const g = new THREE.Group();
  const relief = fitted(s.model, s.W * 0.78, s.W * 0.48, 1e9, mat, s.yaw);
  relief.scale.z = (2 * RELIEF) / relief.userData.size.z; // the front half of the object rises RELIEF
  relief.position.set(0, s.W * 0.15, 0);
  const text = braille(s.def.cells, mat);
  text.position.set(0, -s.W * 0.32, 0);
  g.add(relief, text);
  return g;
}

/** The seal, standing on the ground with its origin at the centre of the face. */
function seal(s: Loaded) {
  const g = new THREE.Group();
  const plinth = new THREE.Mesh(new THREE.BoxGeometry(s.W, H, s.W), stone);
  plinth.position.y = H / 2;
  plinth.castShadow = plinth.receiveShadow = true;
  const knob = fitted(s.model, s.W, 5.4, s.W, undefined, s.yaw);
  knob.position.y = H + knob.userData.size.y / 2 - 0.05;
  const f = face(s, ink);
  f.rotation.x = Math.PI / 2; // face z -> -y (out of the bottom), face y -> +z
  g.add(plinth, knob, f);
  g.userData.height = H + knob.userData.size.y - 0.05;
  return g;
}

/** A rubbing: foil that has been pressed over the face. Lies on the ground, reader at +z. */
function page(s: Loaded, mat: THREE.Material = burnished) {
  const g = new THREE.Group();
  const side = s.W + 1.6;
  const sheet = new THREE.Mesh(new THREE.BoxGeometry(side, 0.05, side), foil);
  sheet.position.y = 0.025;
  sheet.castShadow = sheet.receiveShadow = true;
  const print = face(s, mat);
  print.rotation.x = -Math.PI / 2;
  print.position.y = 0.05;
  g.add(sheet, print);
  g.userData.side = side;
  return g;
}

function rest(obj: THREE.Object3D) {
  obj.position.y -= new THREE.Box3().setFromObject(obj).min.y;
}

async function load(def: SealDef): Promise<Loaded> {
  const model = (await new GLTFLoader().loadAsync(def.glb)).scene;
  // Long things (animals) show their side, on the knob and in the relief alike: def.yaw turns
  // the long axis of the plan view onto x (computed once per model, see seals.json).
  const yaw = q.has("yaw") ? Number(q.get("yaw")) : def.yaw ?? 0;
  return { def, model, W: Math.max(4.4, brailleWidth(def.cells) + 0.9), yaw };
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

const ease = (x: number) => { x = Math.min(1, Math.max(0, x)); return x * x * (3 - 2 * x); };
const seg = (t: number, a: number, b: number) => ease((t - a) / (b - a));
const mix = (a: number, b: number, k: number) => a + (b - a) * k;

let pose: (t: number) => void = () => {};
let duration = 0;

const defs: SealDef[] = await fetch("seals.json").then((r) => r.json());

if (view === "set") {
  // ---- the collection: every seal with its rubbing in front of it
  const got = await Promise.allSettled(defs.map(load));
  const all = got.flatMap((r) => (r.status === "fulfilled" ? [r.value] : []));
  // Tall knobs hide what is behind them, so the taller half stands in the back row.
  const built = all.map((s) => ({ s, sl: seal(s) })).sort((a, b) => b.sl.userData.height - a.sl.userData.height);
  const cols = Math.ceil(built.length / 2), dx = 12, dz = 23;
  shadowArea(44, 4096);
  target.set(0, 1.5, 2.5);
  camera.position.set(0, 1.5 + 64 * Math.sin(0.56), 2.5 + 64 * Math.cos(0.56));
  camera.lookAt(target);
  camera.updateMatrixWorld();
  ui.innerHTML = `<div class="head"><b>万物印</b><span>摸不到的世界，一枚一枚握在手里</span></div>`;
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
    tag.innerHTML = `<b>${s.def.label}</b><span>${s.def.why} · ${s.def.real}</span>`;
    ui.appendChild(tag);
  });
} else {
  const def = defs.find((d) => d.name === (q.get("seal") ?? "elephant")) ?? defs[0];
  const s = await load(def);
  if (view === "anim") {
    // ---- touch → flip → rub → page
    const T = { spin: 2.6, flip: [2.4, 3.8], foilIn: [5.2, 6.0], rub: [6.2, 8.2], part: [8.5, 9.9], end: 12 };
    duration = T.end;
    const sealG = seal(s), Ht = sealG.userData.height as number;
    const flipG = new THREE.Group(), spinG = new THREE.Group();
    sealG.position.y = -Ht / 2;
    spinG.add(sealG);
    flipG.add(spinG);
    const clip = new THREE.Plane(new THREE.Vector3(-1, 0, 0), 0);
    const fresh = burnished.clone();
    fresh.clippingPlanes = [clip];
    const pg = page(s, fresh), side = pg.userData.side as number;
    const pad = new THREE.Mesh(new THREE.SphereGeometry(0.55, 24, 16), new THREE.MeshStandardMaterial({ color: 0x3a2a20, roughness: 0.95 }));
    pad.castShadow = true;
    scene.add(flipG, pg, pad);
    tracked.push(["seal", flipG], ["page", pg]);
    const cap = document.createElement("div");
    cap.id = "cap";
    ui.appendChild(cap);
    const lines: [number, string][] = [
      [0, "先用手认一认，猜猜它是什么"],
      [T.flip[0], "翻过来，答案在底下"],
      [T.flip[1] + 0.1, `盲文写着它的名字：${def.label}`],
      [T.foilIn[0], "铺一张铜箔，拓一遍"],
      [T.part[0], "这一页，是孩子自己做的"],
    ];
    const yFace = Ht + 1.2, yFoil = yFace + RELIEF + 0.03;
    const A = [new THREE.Vector3(0, 7.5, 27), new THREE.Vector3(0, 3.2, 0)];
    const B = [new THREE.Vector3(0, yFace + 17, 15), new THREE.Vector3(0, yFace, 0)];
    const C = [new THREE.Vector3(1.6, 10.5, 30), new THREE.Vector3(1.6, 2.2, 0)];
    pose = (t) => {
      const back = seg(t, T.part[0], T.part[1]);
      const kFlip = seg(t, T.flip[0], T.flip[1]) - back;
      flipG.rotation.x = -Math.PI * kFlip; // the knob tips away, so the face turns towards the camera
      flipG.position.set(mix(0, -3.6, back), Ht / 2 + 1.2 * kFlip, 0);
      spinG.rotation.y = mix(0.5, 2 * Math.PI, seg(t, 0, T.spin)) + 0.5 * back;

      const down = seg(t, T.foilIn[0], T.foilIn[1]);
      pg.visible = t >= T.foilIn[0];
      pg.position.set(mix(0, 4.6, back), mix(mix(yFoil + 14, yFoil, down), Math.sin(1.05) * side / 2, back) + Math.sin(Math.PI * back) * 1.5, mix(0, -0.5, back));
      pg.rotation.set(1.05 * back, -0.3 * back, 0, "YXZ"); // the finished page stands up to face the viewer
      const k = Math.min(1, Math.max(0, (t - T.rub[0]) / (T.rub[1] - T.rub[0])));
      const edge = mix(-side / 2 - 0.2, side / 2 + 0.2, k);
      clip.constant = t < T.rub[0] ? -1e3 : t > T.rub[1] ? 1e6 : edge;
      const show = seg(t, T.rub[0] - 0.3, T.rub[0]) - seg(t, T.rub[1], T.rub[1] + 0.3);
      pad.visible = show > 0.01;
      pad.scale.set(show, show * 0.6, show);
      pad.position.set(edge, yFoil + 0.45, Math.sin(t * 9) * side * 0.36);

      const kb = seg(t, T.flip[0] + 0.2, T.flip[1] + 0.2), kc = seg(t, T.part[0], T.part[1] + 0.3);
      const push = 1 - 0.1 * seg(t, T.flip[1], T.foilIn[0]); // slow push-in while the face is read
      camera.position.lerpVectors(A[0], B[0], kb).sub(B[1]).multiplyScalar(mix(1, push, kb)).add(B[1]).lerp(C[0], kc);
      camera.position.x += Math.sin(t * 0.35) * 0.5;
      target.lerpVectors(A[1], B[1], kb).lerp(C[1], kc);
      // &plain: one fixed caption (name and why it cannot be touched), for montage cuts
      cap.textContent = q.has("plain") ? `${def.label} ｜ ${def.why}，${def.real}` : [...lines].reverse().find(([at]) => t >= at)![1];
    };
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
    pg.rotation.y = -0.3;
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
