import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { RoomEnvironment } from "three/addons/environments/RoomEnvironment.js";
import { createSealAnimation } from "./seal-animation";
import { loadSeal, stone, type SealDef } from "./seal-geo";

export class SealStage {
  private renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
  private scene = new THREE.Scene();
  private camera = new THREE.PerspectiveCamera(30, 1, 0.1, 300);
  private target = new THREE.Vector3(0, 3, 0);
  private controls: OrbitControls;
  private animation?: ReturnType<typeof createSealAnimation>;
  private sourceModel?: THREE.Object3D;
  private loading = 0;
  private playing?: { from: number; to: number; start: number; done: () => void };
  private time = 0;
  private placeholder = new THREE.Mesh(new THREE.BoxGeometry(4.4, 1.5, 4.4), stone);
  private observer: ResizeObserver;
  private reducedMotion = matchMedia("(prefers-reduced-motion: reduce)");

  constructor(private container: HTMLElement, private caption: (text: string) => void) {
    const renderer = this.renderer;
    renderer.setPixelRatio(Math.min(devicePixelRatio, 1.75));
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = THREE.PCFShadowMap;
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 0.9;
    renderer.localClippingEnabled = true;
    renderer.domElement.setAttribute("aria-hidden", "true");
    container.append(renderer.domElement);
    const environment = new THREE.PMREMGenerator(renderer);
    this.scene.environment = environment.fromScene(new RoomEnvironment(), 0.04).texture;
    environment.dispose();
    const light = new THREE.DirectionalLight(0xfff2e0, 2.2);
    light.position.set(-9, 17, 10); light.castShadow = true;
    light.shadow.mapSize.set(2048, 2048);
    Object.assign(light.shadow.camera, { left: -18, right: 18, top: 18, bottom: -18, far: 80 });
    light.shadow.bias = -0.0001;
    this.scene.add(light, new THREE.HemisphereLight(0xfff8ef, 0x829181, 0.7));
    const floor = new THREE.Mesh(new THREE.PlaneGeometry(100, 100), new THREE.ShadowMaterial({ opacity: 0.16 }));
    floor.rotation.x = -Math.PI / 2; floor.receiveShadow = true;
    this.scene.add(floor);
    this.placeholder.position.y = 0.75;
    this.placeholder.castShadow = true;
    this.placeholder.visible = false;
    this.scene.add(this.placeholder);
    this.camera.position.set(0, 8, 29); this.camera.lookAt(this.target);
    this.controls = new OrbitControls(this.camera, renderer.domElement);
    this.controls.enablePan = false;
    this.controls.enableDamping = true;
    this.controls.minDistance = 12; this.controls.maxDistance = 75;
    this.controls.maxPolarAngle = Math.PI * 0.49;
    this.controls.target.copy(this.target);
    this.observer = new ResizeObserver(() => this.resize());
    this.observer.observe(container);
    this.resize();
    renderer.setAnimationLoop(() => {
      if (this.playing) {
        const p = this.playing;
        const seconds = (performance.now() - p.start) / 1000;
        const next = this.reducedMotion.matches ? p.to : Math.min(p.to, p.from + seconds);
        this.pose(next);
        if (next >= p.to) { this.playing = undefined; this.controls.enabled = true; p.done(); }
      }
      if (this.placeholder.visible && !this.reducedMotion.matches) {
        const t = performance.now() / 1000;
        this.placeholder.rotation.y = t * 0.15;
        this.placeholder.position.y = 1.25 + Math.sin(t * 1.4) * 0.12;
      }
      this.controls.update();
      renderer.render(this.scene, this.camera);
    });
  }

  private resize() {
    const width = this.container.clientWidth, height = this.container.clientHeight;
    if (!width || !height) return;
    this.camera.aspect = width / height;
    this.camera.updateProjectionMatrix();
    this.renderer.setSize(width, height, false);
    if (this.animation) this.pose(this.time);
  }

  private pose(time: number) {
    this.time = time;
    this.animation?.pose(time);
    this.camera.position.sub(this.target).multiplyScalar(0.72 * Math.max(1, 1.55 / this.camera.aspect)).add(this.target);
    this.controls.target.copy(this.target);
    this.camera.lookAt(this.target);
  }

  private stopPlayback() {
    const previous = this.playing;
    this.playing = undefined;
    this.controls.enabled = true;
    previous?.done();
  }

  private releaseModel(model: THREE.Object3D) {
    const geometries = new Set<THREE.BufferGeometry>(), materials = new Set<THREE.Material>(), textures = new Set<THREE.Texture>();
    model.traverse((object) => { const mesh = object as THREE.Mesh; if (mesh.isMesh) {
      geometries.add(mesh.geometry);
      for (const material of Array.isArray(mesh.material) ? mesh.material : [mesh.material]) {
        materials.add(material);
        for (const value of Object.values(material)) if (value instanceof THREE.Texture) textures.add(value);
      }
    } });
    geometries.forEach((g) => g.dispose()); materials.forEach((m) => m.dispose()); textures.forEach((t) => t.dispose());
  }

  private clearModel() {
    this.animation?.dispose(); this.animation = undefined;
    if (this.sourceModel) this.releaseModel(this.sourceModel);
    this.sourceModel = undefined;
  }

  busy() {
    this.loading++;
    this.stopPlayback();
    this.clearModel();
    this.placeholder.visible = true;
    this.camera.position.set(0, 9, 27);
    this.target.set(0, 1.5, 0); this.controls.target.copy(this.target);
  }

  async show(def: SealDef, land = false) {
    const ticket = ++this.loading;
    this.stopPlayback();
    const loaded = await loadSeal(def);
    if (ticket !== this.loading) { this.releaseModel(loaded.model); return false; }
    this.clearModel(); this.sourceModel = loaded.model;
    this.placeholder.visible = false;
    this.animation = createSealAnimation(loaded, this.scene, this.camera, this.target, this.caption);
    this.pose(0.5);
    if (land) await this.play(0, 2.3);
    return ticket === this.loading;
  }

  play(from: number, to: number): Promise<void> {
    if (!this.animation) return Promise.resolve();
    this.stopPlayback();
    this.controls.enabled = false;
    return new Promise((resolve) => { this.playing = { from, to, start: performance.now(), done: resolve }; });
  }

  turn(direction: number) {
    const offset = this.camera.position.clone().sub(this.controls.target);
    offset.applyAxisAngle(new THREE.Vector3(0, 1, 0), direction * Math.PI / 8);
    this.camera.position.copy(this.controls.target).add(offset); this.controls.update();
  }

  zoom(factor: number) {
    this.camera.position.sub(this.controls.target).multiplyScalar(factor).add(this.controls.target);
    this.controls.update();
  }
}
