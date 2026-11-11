// Live modal model: same physics as pipeline/wwys (frequencies scale with
// sqrt(E/rho)/size, Rayleigh damping, half-sine hammer contact).

export interface Material {
  name: string; rho: number; E: number; alpha: number; beta: number;
  color: string; metalness: number; roughness: number; opacity?: number;
}

export interface StrikePoint { name: string; vertex: number; shape: number[] }

export interface Meta {
  name: string; label: string; material: string; sizeM: number; pitch: number;
  lambdaUnit: number[]; vertexCount: number; modeCount: number;
  strikeVertex: number; source: "procedural" | "lux3d";
  strikePoints: StrikePoint[]; wallMm: number | null; voxels: number;
  variants?: { label: string; glb: string; material: string }[];
}

export function frequencies(meta: Meta, mat: Material, sizeM: number): Float64Array {
  const h = sizeM * meta.pitch;
  return Float64Array.from(meta.lambdaUnit, (l) => Math.sqrt(Math.max(l, 0) * mat.E / mat.rho) / (2 * Math.PI * h));
}

export function decayRates(freqs: Float64Array, mat: Material): Float64Array {
  return freqs.map((f) => mat.alpha / 2 + mat.beta * (2 * Math.PI * f) ** 2 / 2);
}

function contactSpectrum(f: number, tau: number): number {
  const x = 2 * f * tau;
  if (Math.abs(1 - x) < 1e-6) return Math.PI / 4;
  return Math.abs(Math.cos(Math.PI * f * tau) / (1 - x * x));
}

/** Modal excitation of a hit at `vertex` along `normal`; modes laid out [mode][vertex][xyz]. */
export function strikeAmplitudes(modes: Float32Array, nVerts: number, vertex: number,
  normal: [number, number, number], freqs: Float64Array, tau: number): Float64Array {
  const a = new Float64Array(freqs.length);
  for (let i = 0; i < freqs.length; i++) {
    const b = (i * nVerts + vertex) * 3;
    const dot = modes[b] * normal[0] + modes[b + 1] * normal[1] + modes[b + 2] * normal[2];
    a[i] = dot * contactSpectrum(freqs[i], tau);
  }
  return a;
}

/** Same as strikeAmplitudes, from a precomputed normal-component vector (no geometry). */
export function amplitudesFromShape(shape: number[], freqs: Float64Array, tau: number): Float64Array {
  return freqs.map((f, i) => shape[i] * contactSpectrum(f, tau));
}

/** Lowest mode the default strike excites strongly: the object's "pitch". */
export function pitchMode(amps: Float64Array): number {
  let max = 0;
  for (const v of amps) max = Math.max(max, Math.abs(v));
  return amps.findIndex((v) => Math.abs(v) > 0.2 * max);
}

/** Sum of damped sinusoids via two-multiply resonator recursion, plus a contact click. */
export function renderStrike(ctx: BaseAudioContext, freqs: Float64Array, decays: Float64Array,
  amps: Float64Array): AudioBuffer {
  const sr = ctx.sampleRate;
  let peak = 0;
  for (const v of amps) peak = Math.max(peak, Math.abs(v));
  const live: number[] = [];
  let dur = 0.3;
  for (let i = 0; i < freqs.length; i++) {
    if (freqs[i] < 20 || freqs[i] > 0.45 * sr || Math.abs(amps[i]) < 1e-3 * peak) continue;
    live.push(i);
    dur = Math.max(dur, 6.9 / decays[i]);
  }
  const n = Math.floor(Math.min(dur, 4) * sr);
  const buf = ctx.createBuffer(1, n, sr);
  const out = buf.getChannelData(0);
  for (const i of live) {
    const r = Math.exp(-decays[i] / sr), w = (2 * Math.PI * freqs[i]) / sr;
    const c = 2 * r * Math.cos(w), r2 = r * r;
    const stop = Math.min(n, Math.ceil(Math.log((1e-4 * peak) / Math.abs(amps[i])) / Math.log(r)));
    let y0 = 0, y1 = amps[i] * r * Math.sin(w);
    out[1] += y1;
    for (let k = 2; k < stop; k++) {
      const y = c * y1 - r2 * y0;
      out[k] += y;
      y0 = y1; y1 = y;
    }
  }
  let max = 0;
  for (let k = 0; k < n; k++) max = Math.max(max, Math.abs(out[k]));
  const nc = Math.floor(0.003 * sr);
  // Objects too small to ring in the audible band (a 4.5 cm netsuke) are just the click.
  const clickGain = live.length ? 0.03 * max : 0.5;
  for (let k = 0; k < nc; k++) out[k] += (Math.random() * 2 - 1) * Math.exp(-k / (0.0008 * sr)) * clickGain;
  return buf;
}

/** Slow-motion visual oscillators: displacement_i = Im(z_i), z_i rotates and decays. */
export class Vibration {
  re: Float64Array; im: Float64Array; visFreq: Float64Array; visDecay: Float64Array;

  constructor(n: number) {
    this.re = new Float64Array(n); this.im = new Float64Array(n);
    this.visFreq = new Float64Array(n); this.visDecay = new Float64Array(n);
  }

  /** Map audio-rate modes to watchable motion: pitch mode at 1.4 Hz, ratios compressed. */
  retune(freqs: Float64Array, decays: Float64Array, ref: number) {
    for (let i = 0; i < freqs.length; i++) {
      this.visFreq[i] = 1.4 * Math.sqrt(freqs[i] / freqs[ref]);
      this.visDecay[i] = Math.min(Math.max(decays[i] / 6, 0.35), 15);
    }
  }

  strike(amps: Float64Array, gain: number) {
    for (let i = 0; i < amps.length; i++) this.re[i] += gain * amps[i];
  }

  step(dt: number) {
    for (let i = 0; i < this.re.length; i++) {
      const g = Math.exp(-this.visDecay[i] * dt), th = 2 * Math.PI * this.visFreq[i] * dt;
      const c = Math.cos(th) * g, s = Math.sin(th) * g;
      const re = this.re[i] * c - this.im[i] * s;
      this.im[i] = this.re[i] * s + this.im[i] * c;
      this.re[i] = re;
    }
  }

  energy(): number {
    let e = 0;
    for (let i = 0; i < this.re.length; i++) e += this.re[i] ** 2 + this.im[i] ** 2;
    return e;
  }

  reset() { this.re.fill(0); this.im.fill(0); }
}

const NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];

export function noteName(f: number): string {
  const midi = 69 + 12 * Math.log2(f / 440);
  const r = Math.round(midi), cents = Math.round((midi - r) * 100);
  return `${NOTE_NAMES[((r % 12) + 12) % 12]}${Math.floor(r / 12) - 1}${cents ? ` ${cents > 0 ? "+" : ""}${cents}¢` : ""}`;
}

export function midiToFreq(m: number): number {
  return 440 * 2 ** ((m - 69) / 12);
}

export function midiLabel(m: number): string {
  return `${NOTE_NAMES[m % 12]}${Math.floor(m / 12) - 1}`;
}

/** Strongest excited mode: what the ear hears as "the note" of a single hit. */
export function dominantMode(amps: Float64Array): number {
  let k = 0;
  for (let i = 1; i < amps.length; i++) if (Math.abs(amps[i]) > Math.abs(amps[k])) k = i;
  return k;
}

// Melodies as [MIDI note, beats]; traditional tunes in the public domain.
export const MELODIES: Record<string, { label: string; notes: [number, number][] }> = {
  jasmine: {
    label: "茉莉花（开头）",
    notes: [[76, .5], [76, .25], [79, .25], [81, .25], [84, .25], [84, .25], [81, .25], [79, .5],
      [79, .25], [81, .25], [79, 1]],
  },
  scale: { label: "C 大调音阶", notes: [60, 62, 64, 65, 67, 69, 71, 72].map((m) => [m, .5] as [number, number]) },
  twinkle: {
    label: "小星星",
    notes: [[60, .5], [60, .5], [67, .5], [67, .5], [69, .5], [69, .5], [67, 1],
      [65, .5], [65, .5], [64, .5], [64, .5], [62, .5], [62, .5], [60, 1]],
  },
};
