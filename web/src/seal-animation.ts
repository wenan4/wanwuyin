import * as THREE from "three";
import { seal, page, RELIEF, burnished, disposeBuilt, type Loaded } from "./seal-geo";

const ease = (x: number) => { x = Math.min(1, Math.max(0, x)); return x * x * (3 - 2 * x); };
const seg = (t: number, a: number, b: number) => ease((t - a) / (b - a));
const mix = (a: number, b: number, k: number) => a + (b - a) * k;

export function createSealAnimation(s: Loaded, scene: THREE.Scene, camera: THREE.PerspectiveCamera,
  target: THREE.Vector3, onCaption?: (text: string) => void, plain = false) {
const T = { spin: 2.6, flip: [2.4, 3.8], foilIn: [5.45, 6.05], rub: [6.2, 8.2], part: [8.5, 9.9], end: 12 };
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
const root = new THREE.Group();
root.add(flipG, pg, pad);
scene.add(root);
const lines: [number, string][] = [
  [0, s.def.name === "elephant" ? "摸到长鼻子了吗？" : "先摸摸形状，猜猜它是什么"],
  [T.flip[0], "翻到印底，摸摸凸点"],
  [T.flip[1], `这几个凸点写着：${s.def.label}`],
  [T.foilIn[0], "铺一张铜箔，拓一遍"],
  [T.part[0], "这一页，是孩子自己做的"],
];
const yFace = Ht + 1.2, yFoil = yFace + RELIEF + 0.03;
const A = [new THREE.Vector3(0, 7.5, 27), new THREE.Vector3(0, 3.2, 0)];
const B = [new THREE.Vector3(0, yFace + 18.5, 16.5), new THREE.Vector3(0, yFace - 1.5, 0)];
const C = [new THREE.Vector3(1.6, 10.5, 30), new THREE.Vector3(1.6, 2.2, 0)];
const pose = (t: number) => {
  const back = seg(t, T.part[0], T.part[1]);
  const kFlip = seg(t, T.flip[0], T.flip[1]) - back;
  flipG.rotation.x = -Math.PI * kFlip; // the knob tips away, so the face turns towards the camera
  flipG.position.set(mix(0, -3.6, back), Ht / 2 + 1.2 * kFlip, 0);
  spinG.rotation.y = mix(plain ? 0.26 : 0.5, 2 * Math.PI, seg(t, 0, T.spin)) + 0.5 * back;

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
  // Leave room for both the lifting sheet and the inverted knob during separation.
  camera.position.sub(target).multiplyScalar(1 + 0.18 * Math.sin(Math.PI * back)).add(target);
  // &plain: one fixed caption (name and why it cannot be touched), for montage cuts
  onCaption?.(plain ? `${s.def.label} ｜ ${s.def.why}，${s.def.real}` : [...lines].reverse().find(([at]) => t >= at)![1]);
};
  return { duration: T.end, pose, root, objects: { seal: flipG, page: pg },
    dispose() { scene.remove(root); disposeBuilt(root, s.model); } };
}
