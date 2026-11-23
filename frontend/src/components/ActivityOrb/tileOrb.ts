// VIKI Agent's thinking orb: a 64 px globe of small white tiles that plays the showcase film's
// genesis (docs/assets/motion/viki-showcase) on a loop. One tile blinks and divides into a
// lattice on eighth notes, the lattice bursts, the storm is drawn into tilted orbit lanes, snaps
// into a grid, implodes into a spinning cloud and lands on the globe left to right in quantized
// steps; a scan line locks the globe, it holds and turns, then dissolves to begin again. Every
// value depends only on `seconds` and the tile's index, so two frames drawn for one moment match,
// and a paused state draws one still. Tiles are axis-aligned rectangles snapped to device pixels
// (only the storm spins them), so the picture stays crisp at 1x and 2x.

export const SIZE = 64;
const CENTER = SIZE / 2;
/** Globe radius in px; the orbit lanes need room out to the canvas edge. */
const R = 18.5;
/** The showcase soundtrack's grid, 96 BPM. */
export const BEAT = 0.625;
const EIGHTH = BEAT / 2;
const SIXTEENTH = BEAT / 4;

export type OrbLook = "idle" | "active" | "waiting" | "offline" | "error";

// ---- deterministic helpers -------------------------------------------------------------------

const clamp01 = (v: number) => (v < 0 ? 0 : v > 1 ? 1 : v);
const lerp = (a: number, b: number, t: number) => a + (b - a) * t;
const smoothstep = (t: number) => {
  const x = clamp01(t);
  return x * x * (3 - 2 * x);
};
const easeOutCubic = (t: number) => 1 - (1 - clamp01(t)) ** 3;
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
/** Progress in `steps` ticks, each an eased jump: a clock, not a glide. */
const stepped = (t: number, steps: number, sharpness = 0.35) => {
  const x = clamp01(t) * steps;
  const whole = Math.floor(x);
  const part = x - whole;
  const jump = part < 1 - sharpness ? 0 : easeOutCubic((part - (1 - sharpness)) / sharpness);
  return Math.min(1, (whole + jump) / steps);
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

/** Fibonacci-sphere unit vectors, one per tile, for the cloud. */
const FIB = (() => {
  const fx = new Float32Array(N);
  const fy = new Float32Array(N);
  const fz = new Float32Array(N);
  const golden = Math.PI * (3 - Math.sqrt(5));
  shuffled(N, mulberry32(0x5eed)).forEach((tile, k) => {
    const y = 1 - (2 * (k + 0.5)) / N;
    const radius = Math.sqrt(1 - y * y);
    fx[tile] = Math.cos(golden * k) * radius;
    fy[tile] = y;
    fz[tile] = Math.sin(golden * k) * radius;
  });
  return { fx, fy, fz };
})();

/** Quadtree birth: every tile's generation and finest cell. Generations 0 to 4 are complete; the
 * last generation's 768 cells take the tiles left over, so the final division is a sprinkle. */
const GENERATIONS = 5;
const QUAD = (() => {
  const rand = mulberry32(0x9e37);
  const slotX: number[] = [0];
  const slotY: number[] = [0];
  const slotGen: number[] = [0];
  for (let g = 1; g <= GENERATIONS; g++) {
    const existing = slotX.length;
    for (let s = 0; s < existing; s++) {
      const keep = Math.floor(rand() * 4);
      const px = slotX[s] ?? 0;
      const py = slotY[s] ?? 0;
      for (let c = 0; c < 4; c++) {
        const cx = px * 2 + (c & 1);
        const cy = py * 2 + (c >> 1);
        if (c === keep) {
          slotX[s] = cx;
          slotY[s] = cy;
        } else {
          slotX.push(cx);
          slotY.push(cy);
          slotGen.push(g);
        }
      }
    }
  }
  const complete = 4 ** (GENERATIONS - 1);
  const last = shuffled(slotX.length - complete, rand).map((s) => s + complete);
  const slots = [...Array.from({ length: complete }, (_, s) => s), ...last].slice(0, N);
  const tiles = shuffled(N, rand);
  const gen = new Uint8Array(N);
  const cellX = new Uint8Array(N);
  const cellY = new Uint8Array(N);
  slots.forEach((slot, k) => {
    const tile = tiles[k] ?? 0;
    gen[tile] = slotGen[slot] ?? 0;
    cellX[tile] = slotX[slot] ?? 0;
    cellY[tile] = slotY[slot] ?? 0;
  });
  return { gen, cellX, cellY };
})();

// Scratch outputs of the formation helpers, so the hot loop allocates nothing.
let ox = 0;
let oy = 0;
let oz = 0;
let os = 1;

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

/** Orbit: three lanes on a ring tilted out of the screen, with perspective. */
const orbitPoint = (i: number, elapsed: number) => {
  const lane = Math.floor(rnd(i, 0) * 3);
  const radius = R * (1.36 + 0.09 * lane + 0.05 * (rnd(i, 1) - 0.5));
  const psi = Math.PI * 2 * rnd(i, 2) + 1.3 * (1 - 0.1 * lane) * elapsed;
  const incline = 1.2;
  const x = radius * Math.cos(psi);
  const y = radius * Math.sin(psi) * Math.cos(incline) + (rnd(i, 3) - 0.5) * R * 0.07;
  const z = radius * Math.sin(psi) * Math.sin(incline);
  const tilt = -0.24;
  const f = 6 * R;
  const scale = f / (f - z);
  ox = CENTER + (x * Math.cos(tilt) - y * Math.sin(tilt)) * scale;
  oy = CENTER + (x * Math.sin(tilt) + y * Math.cos(tilt)) * scale;
  os = scale;
  oz = z / radius;
};

/** Cloud: the Fibonacci sphere spinning about a tilted axis, speeding up as it forms. */
const cloudPoint = (i: number, elapsed: number) => {
  const spin = 2.3 * (1 - Math.exp(-elapsed / 0.8)) * elapsed * 0.9;
  const c = Math.cos(spin);
  const s = Math.sin(spin);
  const fx = at(FIB.fx, i);
  const fy = at(FIB.fy, i);
  const fz = at(FIB.fz, i);
  const x1 = fx * c + fz * s;
  const z1 = -fx * s + fz * c;
  const k = 0.42;
  const y2 = fy * Math.cos(k) - z1 * Math.sin(k);
  const z2 = fy * Math.sin(k) + z1 * Math.cos(k);
  const radius = R * 1.06;
  const f = 4 * R;
  const scale = f / (f - z2 * radius);
  ox = CENTER + x1 * radius * scale;
  oy = CENTER + y2 * radius * scale;
  os = scale;
  oz = z2;
};

const LATTICE_RADIUS = [0, 0.34, 0.52, 0.67, 0.8, 0.91, 1];
const LATTICE_R = R * 1.12;
/** A cell of generation `g` (a 2^g square), mapped from the square onto a disk. */
const latticeCell = (cx: number, cy: number, g: number, breathe: number) => {
  const side = 2 ** g;
  const u = ((cx + 0.5) / side) * 2 - 1;
  const v = ((cy + 0.5) / side) * 2 - 1;
  const du = g === 0 ? 0 : u * Math.sqrt(1 - (v * v) / 2);
  const dv = g === 0 ? 0 : v * Math.sqrt(1 - (u * u) / 2);
  const radius = (LATTICE_RADIUS[g] ?? 1) * LATTICE_R * breathe;
  ox = CENTER + du * radius;
  oy = CENTER + dv * radius;
};
const latticeSize = (g: number) =>
  g === 0 ? LATTICE_R * 0.24 : ((2 * (LATTICE_RADIUS[g] ?? 1) * LATTICE_R) / 2 ** g) * 0.74;

/** The film's clock, in seconds from the start of a cycle. */
const T = {
  blink: 0,
  divide: 0.5 * BEAT,
  drop: 3 * BEAT,
  orbit: 4.5 * BEAT,
  grid: 6.25 * BEAT,
  cloud: 7.25 * BEAT,
  assemble: 8 * BEAT,
  lock: 10 * BEAT,
  dissolve: 12.25 * BEAT,
};
export const PERIOD = 13 * BEAT;
const genAt = (g: number) => (g === 0 ? T.blink : T.divide + (g - 1) * EIGHTH);
const latticeBreathe = (t: number) => {
  let breathe = 1;
  for (let g = 1; g <= GENERATIONS; g++) breathe += 0.035 * pulse(t, genAt(g), 0.3);
  return breathe;
};

/** The grid: columns and rows over the canvas, one cell per tile, taken in orbit order so the
 * snap reads as a zip rather than a shuffle. */
const GRID = (() => {
  const cols = Math.ceil(Math.sqrt(N));
  const rows = Math.ceil(N / cols);
  const xs = new Float32Array(N);
  const ys = new Float32Array(N);
  for (let i = 0; i < N; i++) {
    orbitPoint(i, T.grid - T.orbit);
    xs[i] = ox;
    ys[i] = oy;
  }
  const byX = Array.from({ length: N }, (_, i) => i).sort((a, b) => at(xs, a) - at(xs, b));
  const col = new Uint8Array(N);
  const row = new Uint8Array(N);
  for (let c = 0; c < cols; c++) {
    const column = byX.slice(c * rows, (c + 1) * rows).sort((a, b) => at(ys, a) - at(ys, b));
    column.forEach((tile, r) => {
      col[tile] = c;
      row[tile] = r;
    });
  }
  return { cols, rows, col, row };
})();

// ---- the frame --------------------------------------------------------------------------------

type Tile = {
  x: number;
  y: number;
  w: number;
  h: number;
  a: number;
  alpha: number;
  glow: number;
  z: number;
};
const FRAME: Tile[] = Array.from({ length: N }, () => ({
  x: 0,
  y: 0,
  w: 0,
  h: 0,
  a: 0,
  alpha: 0,
  glow: 0,
  z: 0,
}));
const ORDER = new Uint16Array(N);
const REST: Tile = { x: 0, y: 0, w: 0, h: 0, a: 0, alpha: 0, glow: 0, z: 0 };
const tileAt = (i: number): Tile => FRAME[i] ?? REST;

const simulate = (time: number) => {
  const k = Math.floor(time / PERIOD);
  const t = time - k * PERIOD;
  const half = 28;
  const scanStart = T.lock + 0.07;
  const scanY = CENTER - 1.15 * R + 2.3 * R * easeInOutCubic((t - scanStart) / 0.6);
  for (let i = 0; i < N; i++) {
    const out = tileAt(i);
    const g0 = at(QUAD.gen, i);
    const r0 = rnd(i, 0);
    const r1 = rnd(i, 1);
    const r2 = rnd(i, 2);
    const r3 = rnd(i, 3);
    const r4 = rnd(i, 4);
    const r5 = rnd(i, 5);
    const r6 = rnd(i, 6);
    const r7 = rnd(i, 7);
    let x = 0;
    let y = 0;
    let w = 0;
    let h = 0;
    let a = 0;
    let alpha = 1;
    let glow = 0;
    let z = 0;
    if (t < T.drop) {
      // 1. The lattice: one tile, dividing on every eighth note.
      const born = genAt(g0) + (g0 === GENERATIONS ? r0 * 0.1 : 0);
      if (t < born) {
        out.alpha = 0;
        continue;
      }
      let g = g0;
      while (g < GENERATIONS && t >= genAt(g + 1)) g++;
      const breathe = latticeBreathe(t);
      const fromGen = g === 0 ? 0 : g - 1;
      const shiftFrom = GENERATIONS - fromGen;
      const cellX = at(QUAD.cellX, i);
      const cellY = at(QUAD.cellY, i);
      latticeCell(cellX >> shiftFrom, cellY >> shiftFrom, fromGen, breathe);
      const fx = ox;
      const fy = oy;
      const shift = GENERATIONS - g;
      latticeCell(cellX >> shift, cellY >> shift, g, breathe);
      const start = g === g0 ? born : genAt(g);
      const hop = r0 < 0.12 && g >= 2;
      const p = clamp01((t - start - (g === g0 ? 0 : r0 * 0.08)) / 0.2);
      const q = hop ? easeOutBack(p, 3) : easeOutCubic(p);
      x = lerp(fx, ox, q);
      y = lerp(fy, oy, q);
      const size = lerp(latticeSize(fromGen) * (g === g0 && g > 0 ? 0.6 : 1), latticeSize(g), q);
      w = size;
      h = size;
      alpha = g0 === 0 ? 1 : clamp01((t - born) / 0.08);
      if (g0 === 0 && t < T.divide) alpha *= 0.55 + 0.45 * Math.round(0.5 + 0.5 * Math.sin(t * 40));
      if (hash01(i, Math.floor(t / SIXTEENTH)) < 0.06) alpha *= 0.3;
      glow = 0.25 + 0.75 * pulse(t, start, 0.23);
      z = 2;
    } else {
      // 2. The drop: the lattice bursts outward in a vortex; some tiles tick, some glide.
      const tau = t - T.drop;
      latticeCell(at(QUAD.cellX, i), at(QUAD.cellY, i), GENERATIONS, 1);
      const dx = ox - CENTER;
      const dy = oy - CENTER;
      const r0Dist = Math.hypot(dx, dy);
      let phi = Math.atan2(dy, dx);
      // A quarter of the tiles fire along eight rays; the rest scatter freely, near and far.
      if (r2 < 0.26) phi = Math.round(phi / (Math.PI / 4)) * (Math.PI / 4);
      const reach = r0Dist * 1.05 + (0.03 + 1.2 * r1 ** 1.45) * half;
      const span = 0.53 + 0.87 * r2;
      const pr = r4 < 0.38 ? stepped(tau / span, 4) : easeOutExpo(tau / span);
      const radius = lerp(r0Dist, reach, pr);
      const swirl = (0.35 + 1.25 * r3) * (r3 < 0.14 ? -1 : 1);
      const angle = phi + swirl * easeOutCubic(tau / 2.3) + 0.12 * tau;
      x = CENTER + Math.cos(angle) * radius;
      y = CENTER + Math.sin(angle) * radius;
      let size = (1.1 + 0.8 * r7) * (r5 > 0.93 ? 2.2 : 1);
      size = lerp(latticeSize(GENERATIONS), size, clamp01(tau / 0.33));
      w = size;
      h = r5 > 0.96 ? size * 2.6 : size;
      a = r7 < 0.35 ? (r6 - 0.5) * 4.8 * tau : 0;
      alpha = 1;
      glow = 1 - clamp01(tau / 0.6);
      z = 2;

      // 3. The orbit: tiles are drawn into three tilted lanes, near tiles larger and brighter.
      const orbitStart = T.orbit + r6 * 0.47;
      if (t > orbitStart) {
        orbitPoint(i, t - orbitStart);
        const p = easeInOutCubic((t - orbitStart) / 0.67);
        x = lerp(x, ox, p);
        y = lerp(y, oy, p);
        const orbitSize = 1.5 * os * (r5 > 0.93 ? 1.6 : 1);
        w = lerp(w, orbitSize * (r5 > 0.96 ? 2 : 1), p);
        h = lerp(h, orbitSize, p);
        a = lerp(a, 0, p);
        alpha = lerp(alpha, 0.3 + 0.7 * (oz * 0.5 + 0.5), p);
        glow = lerp(glow, 0.15 + 0.35 * clamp01(oz), p);
        z = lerp(z, 1 + oz, p);
      }

      // 4. The grid: every tile zips into one canvas-wide lattice on the beat; a wave runs
      // through it and a few cells glitch out of line.
      const col = at(GRID.col, i);
      const row = at(GRID.row, i);
      const gridStart = T.grid + (col / GRID.cols) * 0.23 + r0 * 0.05;
      if (t > gridStart) {
        const gx = SIZE / GRID.cols;
        const gy = SIZE / GRID.rows;
        let tx = (col + 0.5) * gx;
        let ty = (row + 0.5) * gy;
        const beatIndex = Math.floor((t - T.grid) / SIXTEENTH);
        if (r5 < 0.07 && hash01(i, beatIndex) < 0.5) {
          tx += (hash01(i, beatIndex + 7) < 0.5 ? -1 : 1) * gx;
          ty += Math.round((hash01(i, beatIndex + 3) - 0.5) * 2) * gy;
        }
        const p = easeOutExpo((t - gridStart) / 0.23);
        x = lerp(x, tx, p);
        y = lerp(y, ty, p);
        const wave = 0.5 + 0.5 * Math.sin(col * 0.5 + row * 0.3 - (t - T.grid) * 18);
        const cellSize = Math.min(gx, gy) * (0.35 + 0.4 * wave);
        w = lerp(w, cellSize, p);
        h = lerp(h, cellSize, p);
        a = lerp(a, 0, p);
        alpha = lerp(alpha, 0.3 + 0.7 * wave, p);
        glow = lerp(glow, 0.5 * pulse(t, T.grid + 0.03, 0.33) + 0.25 * wave, p);
        z = lerp(z, 2, p);
      }

      // 5. The cloud: the grid implodes into a spinning sphere of tiles.
      const cloudStart = T.cloud + (1 - Math.hypot(x - CENTER, y - CENTER) / 45) * 0.17 + r1 * 0.1;
      if (t > cloudStart) {
        cloudPoint(i, t - cloudStart);
        const p = easeInOutCubic((t - cloudStart) / 0.4);
        x = lerp(x, ox, p);
        y = lerp(y, oy, p);
        const cloudSize = 1.4 * os;
        w = lerp(w, cloudSize, p);
        h = lerp(h, cloudSize, p);
        alpha = lerp(alpha, 0.35 + 0.65 * (oz * 0.5 + 0.5), p);
        glow = lerp(glow, 0.2, p);
        z = lerp(z, oz, p);
      }

      // 6. Assembly: tiles land on the globe, left to right, and step into place.
      const spinAt = k * PERIOD + T.assemble;
      globePoint(i, yawAt(spinAt) + 0.33 * Math.max(0, t - T.assemble), nodAt(spinAt));
      const goalX = ox;
      const goalY = oy;
      const goalW = TILE_W * os;
      const goalH = TILE_H * os;
      const goalZ = oz;
      const goalAlpha = 0.28 + 0.72 * ((oz + 1) / 2);
      const order = clamp01(((goalX - CENTER) / R + 1) * 0.4 + r6 * 0.2);
      const landStart = T.assemble + order * 0.9;
      const landSpan = 0.33 + 0.3 * r7;
      if (t > landStart) {
        const p = clamp01((t - landStart) / landSpan);
        const q = r4 < 0.3 ? easeOutBack(p, 1.3) : easeOutExpo(p);
        x = lerp(x, goalX, q);
        y = lerp(y, goalY, q);
        if (p > 0.6) {
          x = quantize(x, goalX, 0.75);
          y = quantize(y, goalY, 0.75);
        }
        w = lerp(w, goalW, q);
        h = lerp(h, goalH, q);
        a = lerp(a, 0, q);
        alpha = lerp(alpha, goalAlpha, q);
        glow = 0.1 + 0.9 * pulse(t, landStart + landSpan, 0.27) + 0.6 * pulse(t, T.lock, 0.33);
        z = lerp(z, goalZ, q);
      }
      // 7. The lock: a scan line runs down the globe and seals it.
      if (t > scanStart) {
        const at = scanStart + ((y - CENTER + 1.15 * R) / (2.3 * R)) * 0.6;
        glow += 0.8 * pulse(t, at, 0.2);
        alpha = Math.min(1, alpha + 0.3 * pulse(t, at, 0.18));
        if (y < scanY) glow += 0.05;
      }
      // 8. Hold: a few tiles leave the globe to orbit it or drift off its face.
      const role = r5 >= 0.3 && r5 < 0.33 ? 1 : r5 >= 0.33 && r5 < 0.4 && goalZ > 0.4 ? 2 : 0;
      if (role && t > T.lock + 0.45) {
        const enter = easeInOutCubic((t - (T.lock + 0.45) - r1 * 0.6) / 0.9);
        const loop = (t - T.lock) / (2.5 * BEAT);
        if (role === 1) {
          const lane = Math.floor(r0 * 3);
          const radius = R * (1.42 + 0.12 * lane);
          const psi = Math.PI * 2 * (r3 + (r2 < 0.5 ? 1 : 2) * loop);
          const ex = radius * Math.cos(psi);
          const ey = radius * Math.sin(psi) * Math.cos(1.25);
          const ez = Math.sin(psi);
          const tilt = -0.24;
          x = lerp(x, CENTER + ex * Math.cos(tilt) - ey * Math.sin(tilt), enter);
          y = lerp(y, CENTER + ex * Math.sin(tilt) + ey * Math.cos(tilt), enter);
          const size = 1.5 * (1 + 0.25 * ez);
          w = lerp(w, size, enter);
          h = lerp(h, size, enter);
          alpha = lerp(alpha, 0.35 + 0.5 * (ez * 0.5 + 0.5), enter);
          glow = lerp(glow, 0.3, enter);
          z = lerp(z, 1 + ez, enter);
        } else {
          const c = (((loop * 2 + r3) % 1) + 1) % 1;
          const away = smoothstep((c - 0.12) / 0.88);
          const dirX = (goalX - CENTER) / R + 0.35;
          const dirY = ((goalY - CENTER) / R) * 0.6;
          const len = Math.hypot(dirX, dirY) || 1;
          const dist = R * (0.08 + 0.32 * r4) * away;
          x = lerp(x, goalX + (dirX / len) * dist, enter);
          y = lerp(y, goalY + (dirY / len) * dist, enter);
          alpha = lerp(alpha, clamp01(c / 0.12) * (1 - away), enter);
          w = lerp(w, goalW * (1 - 0.4 * away), enter);
          h = lerp(h, goalH * (1 - 0.4 * away), enter);
          glow = lerp(glow, 0.2 * (1 - away), enter);
        }
      }
      // 9. Dissolve: the globe flickers out in random order, while the seed tile glides back to
      // the centre and becomes the one that blinks and divides in the next cycle, so the loop
      // closes without an empty frame.
      if (t > T.dissolve) {
        if (g0 === 0) {
          const p = easeInOutCubic((t - T.dissolve) / (PERIOD - T.dissolve));
          x = lerp(x, CENTER, p);
          y = lerp(y, CENTER, p);
          w = lerp(w, latticeSize(0), p);
          h = lerp(h, latticeSize(0), p);
          a = 0;
          alpha = Math.max(alpha, p);
          glow = Math.max(glow, 0.5 * p);
          z = 2;
        } else {
          const gone = T.dissolve + r2 * 0.4;
          if (t > gone) alpha = 0;
          glow += 0.6 * pulse(t, gone - 0.08, 0.1);
        }
      }
    }
    out.x = x;
    out.y = y;
    out.w = w;
    out.h = h;
    out.a = a;
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
 * Axis-aligned tiles snap to device pixels (`unit` is one device pixel in canvas units) and are
 * at least one device pixel wide and high; a spinning tile is drawn as a quad. */
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
      const w = tile.w + grow;
      const h = tile.h + grow;
      if (Math.abs(tile.a) < 0.002) {
        const pw = Math.max(unit, Math.round(w / unit) * unit);
        const ph = Math.max(unit, Math.round(h / unit) * unit);
        const px = Math.round((tile.x - pw / 2) / unit) * unit;
        const py = Math.round((tile.y - ph / 2) / unit) * unit;
        ctx.rect(px, py, pw, ph);
      } else {
        const hw = Math.max(unit, w) / 2;
        const hh = Math.max(unit, h) / 2;
        const c = Math.cos(tile.a);
        const s = Math.sin(tile.a);
        const { x, y } = tile;
        ctx.moveTo(x - hw * c + hh * s, y - hw * s - hh * c);
        ctx.lineTo(x + hw * c + hh * s, y + hw * s - hh * c);
        ctx.lineTo(x + hw * c - hh * s, y + hw * s + hh * c);
        ctx.lineTo(x - hw * c - hh * s, y - hw * s + hh * c);
        ctx.closePath();
      }
    }
    if (any) ctx.fill();
  }
  ctx.globalAlpha = 1;
};

/** A representative moment of the cycle for the states that draw one still: the held globe,
 * with its orbiters out for "waiting". */
export const stillSeconds = (look: OrbLook): number => T.lock + (look === "waiting" ? 1.0 : 1.4);

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
