import * as THREE from "three";
import { RoundedBoxGeometry } from "three/addons/geometries/RoundedBoxGeometry.js";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";

export type SealDef = { name: string; label: string; cells: string; why: string; real: string; glb: string; yaw?: number };
export type Loaded = { def: SealDef; model: THREE.Object3D; W: number; yaw: number };

export const H = 1.5; // plinth height
export const RELIEF = 0.20; // how far the relief rises from the face
const DOT = 0.25, CELL = 0.6, DOT_R = 0.085, DOT_H = 0.095;
const DOT_POS: [number, number][] = [[0, 0], [0, 1], [0, 2], [1, 0], [1, 1], [1, 2]];

export const stone = new THREE.MeshPhysicalMaterial({ color: 0x2c4a41, roughness: 0.45, clearcoat: 0.4, clearcoatRoughness: 0.45 }); // dark green seal stone
export const ink = new THREE.MeshStandardMaterial({ color: 0x9b2718, roughness: 0.85 }); // cinnabar paste on the raised parts
export const foil = new THREE.MeshStandardMaterial({ color: 0x794024, metalness: 0.68, roughness: 0.69 }); // copper foil
export const burnished = new THREE.MeshStandardMaterial({ color: 0xe2ab72, metalness: 0.48, roughness: 0.58 }); // the rubbed high points shine

export const brailleWidth = (cells: string) => ([...cells].length - 1) * CELL + DOT;

/** Wrap the model so its bounding box is centred at the origin and fits (w, h, d). */
export function fitted(src: THREE.Object3D, w: number, h: number, d: number, mat?: THREE.Material, yaw = 0, pitch = 0) {
  const obj = src.clone(true);
  obj.rotation.set(pitch, yaw, 0, "XYZ");
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
export function braille(cells: string, mat: THREE.Material) {
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
export function face(s: Loaded, mat: THREE.Material) {
  const g = new THREE.Group();
  // Project these shapes from above so fins, ring openings and the crater survive flattening.
  const pitch: Record<string, number> = { ant: 1.42, blue_whale: 1.27, saturn: 0.18, volcano: 0.65, yongzhong: 0.14 };
  const relief = fitted(s.model, s.W * 0.83, s.W * 0.52, Infinity, mat, s.yaw, pitch[s.def.name] ?? 0);
  // Recesses keep a darker patina. Measure depth before flattening the mesh.
  const reliefMat = mat.clone() as THREE.MeshStandardMaterial;
  reliefMat.vertexColors = true;
  reliefMat.clippingPlanes = mat.clippingPlanes;
  relief.updateMatrixWorld(true);
  const point = new THREE.Vector3();
  const normal = new THREE.Vector3();
  const grazing = new THREE.Vector3(-0.8, 0.55, 0.24).normalize();
  relief.traverse((object) => {
    if (!(object instanceof THREE.Mesh)) return;
    const geometry = object.geometry.clone();
    const vertices = geometry.getAttribute("position");
    if (!geometry.hasAttribute("normal")) geometry.computeVertexNormals();
    const normals = geometry.getAttribute("normal");
    const normalMatrix = new THREE.Matrix3().getNormalMatrix(object.matrixWorld);
    const colors = new Float32Array(vertices.count * 3);
    for (let index = 0; index < vertices.count; index++) {
      point.fromBufferAttribute(vertices, index).applyMatrix4(object.matrixWorld);
      const depth = THREE.MathUtils.clamp(point.z / relief.userData.size.z + 0.5, 0, 1);
      normal.fromBufferAttribute(normals, index).applyNormalMatrix(normalMatrix);
      const patina = (0.2 + 0.8 * depth ** 2) * (0.56 + 0.44 * Math.max(0, normal.dot(grazing)));
      colors.fill(patina, index * 3, index * 3 + 3);
    }
    geometry.setAttribute("color", new THREE.BufferAttribute(colors, 3));
    object.geometry = geometry;
    object.material = reliefMat;
  });
  relief.scale.z = RELIEF / relief.userData.size.z;
  relief.position.set(0, s.W * 0.15, RELIEF / 2);
  const text = braille(s.def.cells, mat);
  text.position.set(0, -s.W * 0.32, 0);
  g.add(relief, text);
  return g;
}

/** The seal, standing on the ground with its origin at the centre of the face. */
export function seal(s: Loaded) {
  const g = new THREE.Group();
  const plinth = new THREE.Mesh(new RoundedBoxGeometry(s.W, H, s.W, 2, 0.075), stone);
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
export function page(s: Loaded, mat: THREE.Material = burnished) {
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

export function rest(obj: THREE.Object3D) {
  obj.position.y -= new THREE.Box3().setFromObject(obj).min.y;
}

export async function loadSeal(def: SealDef, yawOverride?: number): Promise<Loaded> {
  const model = (await new GLTFLoader().loadAsync(def.glb)).scene;
  // Long things (animals) show their side, on the knob and in the relief alike: def.yaw turns
  // the long axis of the plan view onto x (computed once per model, see seals.json).
  const yaw = yawOverride ?? def.yaw ?? 0;
  return { def, model, W: Math.max(4.4, brailleWidth(def.cells) + 0.9), yaw };
}


/** Dispose a built view while keeping the shared GLB and common materials alive. */
export function disposeBuilt(root: THREE.Object3D, model: THREE.Object3D) {
  const keptGeo = new Set<THREE.BufferGeometry>();
  const keptMat = new Set<THREE.Material>([stone, ink, foil, burnished]);
  model.traverse((o) => { const m = o as THREE.Mesh; if (m.isMesh) {
    keptGeo.add(m.geometry); (Array.isArray(m.material) ? m.material : [m.material]).forEach((mat) => keptMat.add(mat));
  } });
  const geo = new Set<THREE.BufferGeometry>(), mats = new Set<THREE.Material>();
  root.traverse((o) => { const m = o as THREE.Mesh; if (m.isMesh) {
    if (!keptGeo.has(m.geometry)) geo.add(m.geometry);
    (Array.isArray(m.material) ? m.material : [m.material]).forEach((mat) => { if (!keptMat.has(mat)) mats.add(mat); });
  } });
  geo.forEach((g) => g.dispose()); mats.forEach((m) => m.dispose());
}
