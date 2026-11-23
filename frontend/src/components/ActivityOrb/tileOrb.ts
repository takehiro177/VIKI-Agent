// VIKI Agent's thinking orb: a 64 px globe of small white tiles that folds and unfolds in the
// showcase film's motion language (docs/assets/motion/viki-showcase). Every 5 s, at the film's
// 96 BPM, the globe unfolds ring by ring into a flat lattice over its disk, computes there (a wave
// runs through the cells and a few glitch out of line on sixteenth notes), then folds back onto
// shuffled slots, left to right, settling in quantized steps; a scan line seals the globe, which
// turns on. Every value depends only on `seconds` and the tile's index, so two frames drawn for
// one moment match, and a paused state draws one still. Tiles are axis-aligned rectangles snapped
// to device pixels, so the picture stays crisp at 1x and 2x, and the globe or its lattice is on
// the canvas in every frame.

export const SIZE = 64;
const CENTER = SIZE / 2;
/** Globe radius in px; the lattice spreads a little past it. */
const R = 18.5;
/** The showcase soundtrack's grid, 96 BPM. */
export const BEAT = 0.625;
const SIXTEENTH = BEAT / 4;

export type OrbLook = "idle" | "active" | "waiting" | "offline" | "error";

// ---- deterministic helpers -------------------------------------------------------------------

const clamp01 = (v: number) => (v < 0 ? 0 : v > 1 ? 1 : v);
const lerp = (a: number, b: number, t: number) => a + (b - a) * t;
const easeInOutCubic = (t: number) => {
  const x = clamp01(t);
  return x < 0.5 ? 4 * x * x * x : 1 - (-2 * x + 2) ** 3 / 2;
};
const easeOutExpo = (t: number) => {
  const x = clamp01(t);
  return x === 1 ? 1 : 1 - 2 ** (-10 * x);
};
const easeOutBack = (t: number, overshoot = 1.6) => {
  const x = clamp01(t) - 1;
  return 1 + (overshoot + 1) * x * x * x + overshoot * x * x;
};
/** A spike that rises at `at` and decays over `length` seconds. */
const pulse = (time: number, at: number, length: number) => {
  const d = time - at;
  return d < 0 || d > length ? 0 : (1 - d / length) ** 2;
};
/** Integer hash to [0, 1). */
const hash01 = (a: number, b = 0) => {
  let h = Math.imul(a | 0, 0x27d4eb2d) ^ Math.imul(b | 0, 0x165667b1);
  h = Math.imul(h ^ (h >>> 15), 0x85ebca6b);
  h = Math.imul(h ^ (h >>> 13), 0xc2b2ae35);
  return ((h ^ (h >>> 16)) >>> 0) / 4294967296;
};
/** Mulberry32: a small seeded generator for the per-tile constants. */
const mulberry32 = (seed: number) => {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
};
const shuffled = (count: number, rand: () => number) => {
  const order = Array.from({ length: count }, (_, i) => i);
  for (let i = count - 1; i > 0; i--) {
    const j = Math.floor(rand() * (i + 1));
    const a = order[i] ?? 0;
    order[i] = order[j] ?? 0;
    order[j] = a;
  }
  return order;
};
/** Indexed reads under noUncheckedIndexedAccess; every index here is in range by construction. */
const at = (array: Float32Array | Uint8Array | Uint16Array, index: number) => array[index] ?? 0;
/** Lands a coordinate on a `step` grid around its goal once it is close: a digital settle. */
const quantize = (value: number, goal: number, step: number) =>
  goal - Math.round((goal - value) / step) * step;

// ---- the globe: slots on latitude rings, like the banner's sphere --------------------------------

type Slot = { lat: number; lon: number; ring: number };
const RINGS = 21;
const RING_TILES = 40;
const TILE_W = 1.7;
const TILE_H = 1.3;

const SLOTS: Slot[] = (() => {
  const slots: Slot[] = [];
  for (let ring = 0; ring < RINGS; ring++) {
    const lat = ((-80 + (160 * ring) / (RINGS - 1)) * Math.PI) / 180;
    const count = Math.max(6, Math.round(RING_TILES * Math.cos(lat)));
    for (let k = 0; k < count; k++) {
      const lon = (k / count) * Math.PI * 2 + (ring % 2) * (Math.PI / count);
      slots.push({ lat, lon, ring });
    }
  }
  return slots;
})();
const N = SLOTS.length;
const slotAt = (s: number): Slot => SLOTS[s] ?? { lat: 0, lon: 0, ring: 0 };
const RANDOMS = 8;
const RAND = (() => {
  const rand = mulberry32(0x71e5);
  const r = new Float32Array(N * RANDOMS);
  for (let i = 0; i < r.length; i++) r[i] = rand();
  return r;
})();
const rnd = (i: number, k: number) => at(RAND, i * RANDOMS + k);

// Scratch outputs of the formation helpers, so the hot loop allocates nothing.
let ox = 0;
let oy = 0;
let oz = 0;
let os = 1;
let ocx = 0;
let ocy = 0;

/** Where slot `s` sits on the turning globe: screen position, depth (-1 far, 1 near), size. */
const globePoint = (s: number, yaw: number, nod: number) => {
  const slot = slotAt(s);
  const cl = Math.cos(slot.lat);
  const lon = slot.lon + yaw;
  const px = cl * Math.cos(lon);
  const py = Math.sin(slot.lat);
  const pz = cl * Math.sin(lon);
  const tilt = 0.42 + nod;
  const up = py * Math.cos(tilt) - pz * Math.sin(tilt);
  const depth = py * Math.sin(tilt) + pz * Math.cos(tilt);
  ox = CENTER + px * R;
  oy = CENTER - up * R;
  oz = depth;
  os = 0.8 + 0.4 * ((depth + 1) / 2);
};
const yawAt = (t: number) => 0.33 * t;
const nodAt = (t: number) => 0.06 * Math.sin(t * 0.7);

/** The lattice: a square grid of cells mapped onto the globe's disk, one cell per tile, in a
 * fixed shuffled order so the unfold scatters each ring across the disk. */
const LATTICE_SIDE = Math.ceil(Math.sqrt(N));
const LATTICE_R = R * 1.15;
const CELL = (2 * LATTICE_R) / LATTICE_SIDE;
const CELL_OF = (() => {
  const cells = shuffled(LATTICE_SIDE * LATTICE_SIDE, mulberry32(0xf01d));
  const own = new Uint16Array(N);
  for (let i = 0; i < N; i++) own[i] = cells[i] ?? 0;
  return own;
})();
const latticeCell = (i: number) => {
  const c = at(CELL_OF, i);
  const cx = c % LATTICE_SIDE;
  const cy = Math.floor(c / LATTICE_SIDE);
  const u = ((cx + 0.5) / LATTICE_SIDE) * 2 - 1;
  const v = ((cy + 0.5) / LATTICE_SIDE) * 2 - 1;
  ox = CENTER + u * Math.sqrt(1 - (v * v) / 2) * LATTICE_R;
  oy = CENTER + v * Math.sqrt(1 - (u * u) / 2) * LATTICE_R;
  ocx = cx;
  ocy = cy;
};

/** The cycle's clock, in seconds. */
const T = {
  unfold: 0,
  fold: 4.1 * BEAT,
  scan: 6.1 * BEAT,
};
export const PERIOD = 8 * BEAT;

// ---- the frame --------------------------------------------------------------------------------

type Tile = {
  x: number;
  y: number;
  w: number;
  h: number;
  alpha: number;
  glow: number;
  z: number;
};
const FRAME: Tile[] = Array.from({ length: N }, () => ({
  x: 0,
  y: 0,
  w: 0,
  h: 0,
  alpha: 0,
  glow: 0,
  z: 0,
}));
const ORDER = new Uint16Array(N);
const REST: Tile = { x: 0, y: 0, w: 0, h: 0, alpha: 0, glow: 0, z: 0 };
const tileAt = (i: number): Tile => FRAME[i] ?? REST;

/** The resting globe tile for slot `s`, with depth shading. */
const restTile = (out: Tile, s: number, time: number) => {
  globePoint(s, yawAt(time), nodAt(time));
  const near = (oz + 1) / 2;
  out.x = ox;
  out.y = oy;
  out.w = TILE_W * os;
  out.h = TILE_H * os;
  out.alpha = 0.28 + 0.72 * near;
  out.glow = 0.1 + 0.15 * near;
  out.z = oz;
};

/** The slot each tile folds back onto in cycle `k`: a half turn through the slot list, so every
 * tile crosses the disk and lands among other neighbours. Tiles are alike, so the next cycle can
 * start from the identity again without a visible jump. */
const targetOf = (i: number, k: number) =>
  (i + Math.floor(N / 2) + Math.floor(hash01(k, 3) * 40)) % N;

const simulate = (time: number) => {
  const k = Math.floor(time / PERIOD);
  const t = time - k * PERIOD;
  const sixteenth = Math.floor(t / SIXTEENTH);
  for (let i = 0; i < N; i++) {
    const out = FRAME[i] ?? REST;
    restTile(out, i, time);
    // 1. The unfold: rings peel into the lattice from the top down.
    const unfoldStart =
      T.unfold + (slotAt(i).ring / (RINGS - 1)) * 0.9 * BEAT + rnd(i, 0) * 0.15 * BEAT;
    if (t <= unfoldStart) continue;
    latticeCell(i);
    const cx = ocx;
    const cy = ocy;
    const p = easeInOutCubic((t - unfoldStart) / (0.7 * BEAT));
    // 2. The lattice at work: a wave of brightness, and a few cells that jump a step on
    // sixteenth notes.
    const wave = 0.5 + 0.5 * Math.sin(cx * 0.55 + cy * 0.35 - t * 9);
    const glitch = rnd(i, 5) < 0.07 && hash01(i, sixteenth) < 0.5;
    const tx = ox + (glitch ? (hash01(i, sixteenth + 7) < 0.5 ? -1 : 1) * CELL : 0);
    const ty = oy + (glitch ? Math.round((hash01(i, sixteenth + 3) - 0.5) * 2) * CELL : 0);
    const size = CELL * (0.45 + 0.3 * wave);
    let x = lerp(out.x, tx, p);
    let y = lerp(out.y, ty, p);
    let w = lerp(out.w, size, p);
    let h = lerp(out.h, size, p);
    let alpha = lerp(out.alpha, 0.4 + 0.6 * wave, p);
    let glow =
      lerp(out.glow, 0.15 + 0.35 * wave, p) + 0.5 * pulse(t, unfoldStart + 0.7 * BEAT, 0.2);
    let z = lerp(out.z, 2, p);
    // 3. The fold: tiles zip back onto other slots, left to right, settling in steps.
    const target = targetOf(i, k);
    globePoint(target, yawAt(k * PERIOD + T.fold), 0);
    const order = clamp01((ox - CENTER) / R / 2 + 0.5);
    const foldStart = T.fold + order * 1.1 * BEAT + rnd(i, 1) * 0.1 * BEAT;
    if (t > foldStart) {
      const span = (0.4 + 0.25 * rnd(i, 7)) * BEAT;
      const q0 = clamp01((t - foldStart) / span);
      const q = rnd(i, 4) < 0.3 ? easeOutBack(q0, 1.3) : easeOutExpo(q0);
      restTile(out, target, time);
      // 4. The scan: a line runs down the sealed globe and lights each ring as it passes.
      if (t > T.scan) {
        const scanAt = T.scan + ((out.y - CENTER + 1.15 * R) / (2.3 * R)) * 0.6 * BEAT;
        out.glow += 0.9 * pulse(t, scanAt, 0.22);
        out.alpha = Math.min(1, out.alpha + 0.3 * pulse(t, scanAt, 0.18));
      }
      if (q0 >= 1) continue;
      x = lerp(x, out.x, q);
      y = lerp(y, out.y, q);
      if (q0 > 0.6) {
        x = quantize(x, out.x, 0.75);
        y = quantize(y, out.y, 0.75);
      }
      w = lerp(w, out.w, q);
      h = lerp(h, out.h, q);
      alpha = lerp(alpha, out.alpha, q);
      glow = lerp(glow, out.glow, q) + 0.9 * pulse(t, foldStart + span, 0.3);
      z = lerp(z, out.z, q);
    }
    out.x = x;
    out.y = y;
    out.w = w;
    out.h = h;
    out.alpha = alpha;
    out.glow = glow;
    out.z = z;
  }
};

// ---- painting ---------------------------------------------------------------------------------

const BUCKETS = 12;
const bucketOf = new Uint8Array(N);
const byDepth = (p: number, q: number) => tileAt(p).z - tileAt(q).z;
/** A crisp halo, one device pixel wider on each side, for the tiles that are lit. */
const haloWeight = (tile: Tile) => tile.alpha * 0.45 * Math.max(0, tile.glow - 0.25);
const crispWeight = (tile: Tile) => tile.alpha;

/** Fills every visible tile in alpha buckets: one path per bucket, so a frame is a dozen fills.
 * Tiles snap to device pixels (`unit` is one device pixel in canvas units) and are at least one
 * device pixel wide and high. */
const fillTiles = (
  ctx: CanvasRenderingContext2D,
  unit: number,
  grow: number,
  weight: (tile: Tile) => number,
) => {
  for (let i = 0; i < N; i++) {
    const tile = tileAt(at(ORDER, i));
    const v = tile.alpha > 0.004 ? weight(tile) : 0;
    bucketOf[i] = v <= 0.01 ? 0 : Math.min(BUCKETS, Math.ceil(v * BUCKETS));
  }
  for (let b = 1; b <= BUCKETS; b++) {
    ctx.globalAlpha = b / BUCKETS;
    ctx.beginPath();
    let any = false;
    for (let i = 0; i < N; i++) {
      if (at(bucketOf, i) !== b) continue;
      any = true;
      const tile = tileAt(at(ORDER, i));
      const pw = Math.max(unit, Math.round((tile.w + grow) / unit) * unit);
      const ph = Math.max(unit, Math.round((tile.h + grow) / unit) * unit);
      const px = Math.round((tile.x - pw / 2) / unit) * unit;
      const py = Math.round((tile.y - ph / 2) / unit) * unit;
      ctx.rect(px, py, pw, ph);
    }
    if (any) ctx.fill();
  }
  ctx.globalAlpha = 1;
};

/** A representative moment of the cycle for the states that draw one still: the sealed globe at
 * rest, or the lattice at work for "waiting". */
export const stillSeconds = (look: OrbLook): number =>
  look === "waiting" ? 2.6 * BEAT : PERIOD - 0.4;

/** Paints one frame of the orb, `seconds` into its animation, onto a 64-unit canvas space whose
 * transform already carries `pixelRatio`. `ink` is the tile colour: the app's `--or` token as the
 * caller resolved it (ghost white on the dark theme, black on the light one); an empty string
 * keeps the context's current fill. */
export const paintTileOrb = (
  ctx: CanvasRenderingContext2D,
  seconds: number,
  ink: string,
  pixelRatio = 1,
  look: OrbLook = "active",
): void => {
  simulate(seconds);
  // Offline thins the globe and dims it; error knocks a few tiles out of line.
  if (look === "offline") {
    for (let i = 0; i < N; i++) {
      const tile = tileAt(i);
      if (slotAt(i).ring % 2 === 1 || rnd(i, 6) < 0.3) tile.alpha = 0;
      else tile.alpha *= 0.55;
      tile.glow = 0;
    }
  } else if (look === "error") {
    for (let i = 0; i < N; i++) {
      const tile = tileAt(i);
      if (rnd(i, 6) < 0.12) {
        tile.x += (rnd(i, 7) < 0.5 ? -1 : 1) * 3;
        tile.alpha = 1;
      }
    }
  }
  for (let i = 0; i < N; i++) ORDER[i] = i;
  // Far tiles first, so nearer ones paint over them.
  ORDER.sort(byDepth);
  const unit = 1 / Math.max(1, pixelRatio);
  if (ink) ctx.fillStyle = ink;
  fillTiles(ctx, unit, 2 * unit, haloWeight);
  fillTiles(ctx, unit, 0, crispWeight);
};
