/* exobrain — Room 06: memory.md, drawn out of the brain.
   A wireframe brain (two hemispheres with their folds, the cerebellum and the stem, built as an even closed mesh)
   turns slowly inside a scanner's instruments — a ticked dial, a scan line, readouts of this brain's real numbers
   and a signal trace —
   over a faint network of drifting points; pulses run along its edges, particles leave it
   and fly to the page, and each paragraph appears as its particles arrive.
   Ink on the white wall, Canvas 2D, no libraries. Runs only while this room is open. */
"use strict";

(function () {
  const room = document.getElementById("tab-memory");
  const canvas = document.getElementById("memory-canvas");
  const doc = document.getElementById("memory-doc");
  if (!room || !canvas || !doc) return;
  const ctx = canvas.getContext("2d");
  const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const INK = "#0a0a0a";

  const PULSES = 26;
  const NET = 46;          // background network points
  const NET_LINK = 140;    // px
  const MONO = "500 9px ui-monospace, SFMono-Regular, Menlo, monospace";
  let V = null, N = null, VAL = null, E = null, F = null, W = 0, H = 0, TOP = 0, DPR = 1, raf = 0, t0 = 0, last = 0, revealing = 0;
  let P = null;            // projected vertices: x, y, depth (0 back .. 1 front), scale
  let R = null;            // rotated normals' z (facing us > 0)
  let INNER = null;        // +1 / -1: on that hemisphere's inner wall
  let SEG = null, SP = null; // the sulci as segments: a, b, normal, inner, kind (0 sulcus / 1 fissure); projected
  const SEG_STRIDE = 11;
  const CEREB = [0, 0];      // the cerebellum's points: hidden while it is behind the hemispheres
  let LIT = null;          // how much light each point catches (light from the upper left, in front)
  let flyers = [], pulses = [], net = [], streaks = [], marks = [], emitRate = 1.4, emitCarry = 0, generating = false;
  let stats = null;        // real numbers for the readouts (from /api/memory)
  const wave = new Float32Array(160);

  // ---- the brain: a closed, even mesh (2026-10-13 — the sampled profile left big facets and holes) -------------

  // Perlin noise, seeded, for the folds
  const PERM = (() => { const p = [...Array(256).keys()]; let s = 7;
    for (let i = 255; i > 0; i--) { s = (s * 16807) % 2147483647; const j = s % (i + 1); [p[i], p[j]] = [p[j], p[i]]; }
    return p.concat(p); })();
  const fade = (t) => t * t * t * (t * (t * 6 - 15) + 10);
  function grad(h, x, y, z) { const u = h < 8 ? x : y, v = h < 4 ? y : (h === 12 || h === 14 ? x : z);
    return ((h & 1) ? -u : u) + ((h & 2) ? -v : v); }
  function noise(x, y, z) {
    const X = Math.floor(x) & 255, Y = Math.floor(y) & 255, Z = Math.floor(z) & 255;
    x -= Math.floor(x); y -= Math.floor(y); z -= Math.floor(z);
    const u = fade(x), v = fade(y), w = fade(z), p = PERM;
    const A = p[X] + Y, AA = p[A] + Z, AB = p[A + 1] + Z, B = p[X + 1] + Y, BA = p[B] + Z, BB = p[B + 1] + Z;
    const l = (a, b, t) => a + t * (b - a);
    return l(l(l(grad(p[AA] & 15, x, y, z), grad(p[BA] & 15, x - 1, y, z), u),
               l(grad(p[AB] & 15, x, y - 1, z), grad(p[BB] & 15, x - 1, y - 1, z), u), v),
             l(l(grad(p[AA + 1] & 15, x, y, z - 1), grad(p[BA + 1] & 15, x - 1, y, z - 1), u),
               l(grad(p[AB + 1] & 15, x, y - 1, z - 1), grad(p[BB + 1] & 15, x - 1, y - 1, z - 1), u), v), w);
  }
  const clamp01 = (x) => Math.max(0, Math.min(1, x));
  const norm3 = (a) => { const l = Math.hypot(a[0], a[1], a[2]); return [a[0] / l, a[1] / l, a[2] / l]; };

  function icosphere(level) {
    const t = (1 + Math.sqrt(5)) / 2;
    const v = [[-1, t, 0], [1, t, 0], [-1, -t, 0], [1, -t, 0], [0, -1, t], [0, 1, t], [0, -1, -t], [0, 1, -t],
      [t, 0, -1], [t, 0, 1], [-t, 0, -1], [-t, 0, 1]].map(norm3);
    let f = [[0, 11, 5], [0, 5, 1], [0, 1, 7], [0, 7, 10], [0, 10, 11], [1, 5, 9], [5, 11, 4], [11, 10, 2], [10, 7, 6],
      [7, 1, 8], [3, 9, 4], [3, 4, 2], [3, 2, 6], [3, 6, 8], [3, 8, 9], [4, 9, 5], [2, 4, 11], [6, 2, 10], [8, 6, 7], [9, 8, 1]];
    for (let k = 0; k < level; k++) {
      const mid = new Map(), nf = [];
      const m = (a, b) => { const key = a < b ? a * 100000 + b : b * 100000 + a;
        if (!mid.has(key)) { v.push(norm3([(v[a][0] + v[b][0]) / 2, (v[a][1] + v[b][1]) / 2, (v[a][2] + v[b][2]) / 2])); mid.set(key, v.length - 1); }
        return mid.get(key); };
      for (const [a, b, c] of f) { const ab = m(a, b), bc = m(b, c), ca = m(c, a); nf.push([a, ab, ca], [b, bc, ab], [c, ca, bc], [ab, bc, ca]); }
      f = nf;
    }
    return { v, f };
  }

  // One hemisphere (side +1 / -1): x front(+)/back, y up, z to the side. Returns the position, how deep in a fold,
  // and the fields whose zero lines are drawn as the sulci (the folds' floors) and the two big fissures.
  function hemisphere(q, side) {
    const [qx, qy, qz] = q, lat = qz * side;
    const wx = 0.22 * noise(qx * 2 + 3, qy * 2, qz * 2 + side), wy = 0.22 * noise(qx * 2, qy * 2 + 5, qz * 2);
    const n = noise((qx + wx) * 5.6 + side * 11, (qy + wy) * 5.6, qz * 5.6) + 0.3 * noise(qx * 11, qy * 11 + side * 7, qz * 11);
    let fold = Math.pow(clamp01(1 - Math.abs(n) / 0.1), 1.2);
    const yl = -0.22 + (0.5 - qx) * 0.36;                                           // lateral (Sylvian) fissure
    const fisMask = clamp01((0.6 - qx) * 5) * clamp01((qx + 0.45) * 5) * clamp01(lat * 3);
    const fis = Math.pow(clamp01(1 - Math.abs(qy - yl) / 0.06), 2) * fisMask;
    const xc = 0.02 - 0.22 * (qy - 0.3);                                            // central sulcus
    const cenMask = clamp01((qy - yl - 0.05) * 6);
    const cen = Math.pow(clamp01(1 - Math.abs(qx - xc) / 0.045), 2) * cenMask;
    fold = Math.max(fold, fis, cen * 0.9);
    const r = 1 - 0.045 * fold - 0.05 * fis;
    let x = qx * r, y = qy * r * (qy > 0 ? 0.78 : 0.6);
    if (qy < 0) y -= 0.1 * Math.exp(-((qx - 0.15) ** 2) / 0.12) * clamp01(lat * 2);  // temporal lobe hangs low
    y *= 1 - 0.18 * clamp01(qx - 0.45);                                             // frontal lobe slopes down
    const taper = 1 - 0.14 * clamp01(qx - 0.4);
    const zz = lat > 0 ? 0.27 + 0.52 * lat * taper                                    // the rounded outer side
      : 0.27 - 0.26 * Math.pow(-lat, 0.35) * taper;                                   // the flat inner wall, close to the midline
    return { p: [x * 1.02, y, side * zz * (1 - 0.07 * fold)], fold,
             iso: [[n, 1], [qy - yl, fisMask], [qx - xc, cenMask], [lat - 0.12, 1]] };  // last: the midline's lip
  }

  // A sphere mapped into a brain part: positions, fold depth, outward normals, and which hemisphere's inner wall.
  function mapped(level, place, side) {
    const s = icosphere(level), v = [], val = [], iso = [], inner = [];
    for (const q of s.v) {
      const r = place(q);
      v.push(r.p); val.push(r.fold); iso.push(r.iso || []);
      inner.push(side && q[2] * side < 0.05 ? side : 0);  // the inner wall of a hemisphere, hidden behind the other one
    }
    const n = v.map(() => [0, 0, 0]);
    for (const [a, b, c] of s.f) {
      const u = [v[b][0] - v[a][0], v[b][1] - v[a][1], v[b][2] - v[a][2]], w = [v[c][0] - v[a][0], v[c][1] - v[a][1], v[c][2] - v[a][2]];
      const cr = [u[1] * w[2] - u[2] * w[1], u[2] * w[0] - u[0] * w[2], u[0] * w[1] - u[1] * w[0]];
      for (const i of [a, b, c]) { n[i][0] += cr[0]; n[i][1] += cr[1]; n[i][2] += cr[2]; }
    }
    const cx = v.reduce((m, p) => m + p[0], 0) / v.length, cy = v.reduce((m, p) => m + p[1], 0) / v.length,
      cz = v.reduce((m, p) => m + p[2], 0) / v.length;
    let out = 0;
    for (let i = 0; i < v.length; i += 7) out += n[i][0] * (v[i][0] - cx) + n[i][1] * (v[i][1] - cy) + n[i][2] * (v[i][2] - cz);
    return { v, val, f: s.f, n: n.map((x) => (out < 0 ? norm3(x).map((c) => -c) : norm3(x))), iso, inner };
  }

  function addPart(m) {
    const base = V.length;
    m.v.forEach((p, i) => { V.push(p); VAL.push(m.val[i]); N.push(m.n[i]); INNER.push(m.inner[i]); });
    for (const [a, b, c] of m.f) F.push([base + a, base + b, base + c]);
  }

  // The zero lines of each field over a fine mesh, as segments (marching triangles): the sulci as smooth curves.
  function addLines(m, kind = null) {
    for (const [a, b, c] of m.f) {
      for (let k = 0; k < m.iso[a].length; k++) {
        const fa = m.iso[a][k], fb = m.iso[b][k], fc = m.iso[c][k];
        if ((fa[1] + fb[1] + fc[1]) / 3 < 0.5) continue;
        const pts = [];
        for (const [i, j, fi, fj] of [[a, b, fa[0], fb[0]], [b, c, fb[0], fc[0]], [c, a, fc[0], fa[0]]]) {
          if ((fi < 0) === (fj < 0)) continue;
          const t = fi / (fi - fj);
          pts.push([0, 1, 2].map((d) => m.v[i][d] + (m.v[j][d] - m.v[i][d]) * t));
        }
        if (pts.length !== 2) continue;
        const nn = [0, 1, 2].map((d) => (m.n[a][d] + m.n[b][d] + m.n[c][d]) / 3);
        SEG.push(...pts[0], ...pts[1], ...norm3(nn), m.inner[a] || m.inner[b] || m.inner[c], kind ?? (k ? 1 : 0));
      }
    }
  }

  function shape() {
    if (V) return;
    V = []; VAL = []; F = []; N = []; INNER = []; SEG = [];
    for (const side of [1, -1]) {
      addPart(mapped(4, (q) => hemisphere(q, side), side));   // the mesh: 2,562 points a side, even, closed
      addLines(mapped(5, (q) => hemisphere(q, side), side));  // the sulci, traced on a finer mesh
    }
    const cereb = (q) => {                                     // cerebellum, with its fine horizontal folia
      const ph = Math.sin(q[1] * 14 + q[0] * 3), fol = Math.pow(clamp01(1 - Math.abs(ph) / 0.35), 2), r = 1 - 0.06 * fol;
      return { p: [-0.56 + q[0] * 0.33 * r, -0.4 + q[1] * 0.21 * r, q[2] * 0.52 * r], fold: fol * 0.8, iso: [[ph, 1]] };
    };
    CEREB[0] = V.length;
    addPart(mapped(3, cereb, 0));
    CEREB[1] = V.length;
    addLines(mapped(4, cereb, 0), 2);
    addPart(mapped(2, (q) => ({ p: [-0.2 + q[0] * 0.1, -0.6 + q[1] * 0.26, q[2] * 0.11], fold: 0 }), 0));  // brainstem
    SEG = Float32Array.from(SEG);
    const n = V.length, seen = new Set(), nb = Array.from({ length: n }, () => []);
    E = [];
    for (const [a, b, c] of F) for (const [i, j] of [[a, b], [b, c], [c, a]]) {
      const key = i < j ? i * 100000 + j : j * 100000 + i;
      if (!seen.has(key)) { seen.add(key); E.push([i, j]); nb[i].push(j); nb[j].push(i); }
    }
    V.nb = nb;
    P = new Float32Array(n * 4); R = new Float32Array(n); LIT = new Float32Array(n);
    SP = new Float32Array((SEG.length / SEG_STRIDE) * 5);
    // marked memories: a few points on the outer surface, called out like readings
    const lateral = [];
    for (let i = 0; i < 2562; i++) if (VAL[i] < 0.2 && V[i][2] > 0.45 && Math.abs(V[i][1]) < 0.5) lateral.push(i);
    for (let k = 0; k < 7 && lateral.length; k++) marks.push(lateral[(k * 97 + 13) % lateral.length]);
  }

  function resize() { // the canvas stays put under the bar while the page scrolls over it
    const bar = document.querySelector("header.bar");
    TOP = bar ? bar.getBoundingClientRect().bottom : 0;
    DPR = Math.min(2, window.devicePixelRatio || 1);
    W = window.innerWidth; H = window.innerHeight - TOP;
    canvas.style.top = `${TOP}px`;
    canvas.width = Math.round(W * DPR); canvas.height = Math.round(H * DPR);
    canvas.style.width = `${W}px`; canvas.style.height = `${H}px`;
    if (!net.length) {
      for (let i = 0; i < NET; i++) net.push({ x: Math.random() * W, y: Math.random() * H,
        vx: (Math.random() - 0.5) * 6, vy: (Math.random() - 0.5) * 6 });
    }
  }

  function layout() {
    return W < 900
      ? { cx: W * 0.5, cy: 160, s: Math.min(W * 0.3, 130), alpha: 0.35, hud: false }  // faint behind the text on a phone
      : { cx: W * 0.235, cy: H * 0.5, s: Math.min(W * 0.135, H * 0.3, 240), alpha: 1, hud: true };
  }

  // ---- particles flying from the brain to the page ------------------------------------

  function target(block) { // where on the canvas a block (or the page) is; null if it is not on screen
    const b = (block && block.isConnected ? block : doc).getBoundingClientRect();
    if (!b.width) return null;
    const y0 = Math.max(b.top, TOP + 20) - TOP, y1 = Math.min(b.bottom, window.innerHeight - 20) - TOP;
    if (y1 < y0) return null; // scrolled out of sight
    return { x: b.left - 10, y: y0 + Math.random() * Math.max(6, Math.min(y1 - y0, 360)) };
  }

  function emit(n, block) {
    if (!V) return;
    for (let k = 0; k < n; k++) {
      let i = 0;
      for (let tries = 0; tries < 10; tries++) { // from the side facing us
        i = (Math.random() * V.length) | 0;
        if (P[i * 4 + 2] > 0.55) break;
      }
      const sx = P[i * 4], sy = P[i * 4 + 1];
      const tg = target(block);
      if (!tg) return;
      const mx = (sx + tg.x) / 2 + (Math.random() - 0.5) * 60, my = Math.min(sy, tg.y) - 40 - Math.random() * 120;
      flyers.push({ sx, sy, mx, my, tx: tg.x, ty: tg.y, t: 0, dur: 0.9 + Math.random() * 0.9, px: sx, py: sy });
    }
  }

  // ---- the instruments around it (thin ink, like a scanner's screen) ----------------------

  function text(s, x, y, align = "left", a = 0.75) {
    ctx.globalAlpha = a; ctx.font = MONO; ctx.textAlign = align; ctx.fillText(s, x, y);
  }

  function drawHUD(L, t, yaw, scanY) {
    const { cx, cy, s } = L, R1 = s * 1.32, R2 = s * 1.42;
    ctx.strokeStyle = INK; ctx.fillStyle = INK; ctx.lineWidth = 0.6;
    // rings: a ticked dial turning one way, a dashed ring the other, and a few heavier arcs
    ctx.globalAlpha = 0.28; ctx.beginPath(); ctx.arc(cx, cy, R1, 0, Math.PI * 2); ctx.stroke();
    const rot = t * 0.05;
    ctx.globalAlpha = 0.45; ctx.beginPath();
    for (let k = 0; k < 180; k++) {
      const a = rot + k * Math.PI / 90, l = k % 15 === 0 ? 10 : k % 5 === 0 ? 6 : 3;
      ctx.moveTo(cx + Math.cos(a) * R1, cy + Math.sin(a) * R1);
      ctx.lineTo(cx + Math.cos(a) * (R1 - l), cy + Math.sin(a) * (R1 - l));
    }
    ctx.stroke();
    ctx.globalAlpha = 0.22; ctx.setLineDash([2, 6]); ctx.lineDashOffset = -t * 6;
    ctx.beginPath(); ctx.arc(cx, cy, R2, 0, Math.PI * 2); ctx.stroke(); ctx.setLineDash([]);
    ctx.lineWidth = 2.2; ctx.globalAlpha = 0.7;
    for (const [a0, len] of [[-2.2, 0.5], [-0.35, 0.22], [0.9, 0.35], [2.5, 0.16]]) {
      ctx.beginPath(); ctx.arc(cx, cy, R2 + 6, a0 - rot * 2, a0 + len - rot * 2); ctx.stroke();
    }
    ctx.lineWidth = 0.6;
    // degree marks at the dial's quarters
    for (let k = 0; k < 4; k++) {
      const a = -Math.PI / 2 + k * Math.PI / 2;
      text(String(k * 90).padStart(3, "0"), cx + Math.cos(a) * (R2 + 20), cy + Math.sin(a) * (R2 + 20) + 3, "center", 0.5);
    }
    // corner brackets of the scan field
    const bx = s * 1.18, by = s * 0.98, c = 14;
    ctx.globalAlpha = 0.6; ctx.beginPath();
    for (const [sx, sy] of [[-1, -1], [1, -1], [1, 1], [-1, 1]]) {
      const x = cx + sx * bx, y = cy + sy * by;
      ctx.moveTo(x, y - sy * -c); ctx.lineTo(x, y); ctx.lineTo(x - sx * c, y);
    }
    ctx.stroke();
    // the scan line, with its reading
    ctx.globalAlpha = 0.5; ctx.beginPath(); ctx.moveTo(cx - bx, scanY); ctx.lineTo(cx + bx, scanY); ctx.stroke();
    ctx.globalAlpha = 0.9; ctx.fillRect(cx - bx - 4, scanY - 1.5, 4, 3); ctx.fillRect(cx + bx, scanY - 1.5, 4, 3);
    text(`SCAN ${String(Math.round((scanY - (cy - by)) / (2 * by) * 100)).padStart(3, "0")}%`, cx + bx + 8, scanY + 3, "left", 0.6);
    // readouts: real numbers from this brain
    const st = stats || {};
    const lx = cx - R2, ty = cy - R2 - 34;
    text("EXOBRAIN / CORTEX", lx, ty, "left", 0.9);
    text(`YAW ${String(((yaw * 180 / Math.PI) % 360).toFixed(1)).padStart(5, "0")}°   ${generating ? "READING" : "IDLE"}`, lx, ty + 13, "left", 0.55);
    const rows = [["CORTEX", st.cortex], ["HIPPOCAMPUS", st.waiting], ["RULES", st.rules], ["CASES", st.cases],
      ["STRENGTH μ", st.strength != null ? st.strength.toFixed(2) : null]];
    const rx = cx + R2 + 18, ry = cy + R2 + 22;
    rows.forEach(([k, v], i) => { text(k, rx - 70, ry + i * 14, "right", 0.5); text(v == null ? "—" : String(v), rx, ry + i * 14, "right", 0.9); });
    // the waveform: a brain signal, faster while the brain is being read
    const wx = cx - R2, wy = cy + R2 + 34, ww = R2 * 1.1, sp = generating ? 3 : 1;
    for (let k = 0; k < sp; k++) {
      wave.copyWithin(0, 1);
      const tt = t * 7 + k * 0.3;
      wave[wave.length - 1] = Math.sin(tt) * 0.4 + Math.sin(tt * 2.7) * 0.25 + (Math.random() - 0.5) * (generating ? 0.9 : 0.35);
    }
    ctx.globalAlpha = 0.18; ctx.beginPath(); ctx.moveTo(wx, wy); ctx.lineTo(wx + ww, wy); ctx.stroke();
    ctx.globalAlpha = 0.75; ctx.beginPath();
    for (let k = 0; k < wave.length; k++) { const x = wx + k / (wave.length - 1) * ww, y = wy - wave[k] * 12; k ? ctx.lineTo(x, y) : ctx.moveTo(x, y); }
    ctx.stroke();
    text("SIGNAL", wx, wy + 22, "left", 0.5);
  }

  // ---- frame ------------------------------------------------------------------------------

  function drawNetwork(dt) {
    ctx.strokeStyle = INK; ctx.fillStyle = INK; ctx.lineWidth = 0.5;
    for (const p of net) {
      p.x += p.vx * dt; p.y += p.vy * dt;
      if (p.x < -20) p.x = W + 20; if (p.x > W + 20) p.x = -20;
      if (p.y < -20) p.y = H + 20; if (p.y > H + 20) p.y = -20;
    }
    for (let i = 0; i < net.length; i++) {
      const a = net[i];
      ctx.globalAlpha = 0.14; ctx.fillRect(a.x - 1, a.y - 1, 2, 2);
      for (let j = i + 1; j < net.length; j++) {
        const b = net[j], d = Math.hypot(a.x - b.x, a.y - b.y);
        if (d < NET_LINK) {
          ctx.globalAlpha = 0.06 * (1 - d / NET_LINK);
          ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke();
        }
      }
    }
  }

  const BUCKETS = 10;
  const buckets = Array.from({ length: BUCKETS }, () => []);

  function frame(now) {
    if (room.hidden) { raf = 0; return; } // requestAnimationFrame itself rests while the window is hidden
    const t = (now - t0) / 1000, dt = Math.min(0.05, (now - last) / 1000 || 0.016);
    last = now;
    const L = layout();
    ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
    ctx.clearRect(0, 0, W, H);
    drawNetwork(dt);

    // the brain: a slow full turn, tipped a little toward us, in perspective
    const yaw = 0.9 + t * (generating ? 0.45 : 0.14), pitch = -0.22;
    const cyw = Math.cos(yaw), syw = Math.sin(yaw), cp = Math.cos(pitch), sp = Math.sin(pitch), fl = 4;
    const n = V.length;
    for (let i = 0; i < n; i++) {
      const [x, y, z] = V[i];
      const x1 = x * cyw + z * syw, z1 = -x * syw + z * cyw;
      const y2 = y * cp - z1 * sp, z2 = y * sp + z1 * cp;
      const k = fl / (fl - z2);
      P[i * 4] = L.cx + x1 * L.s * k; P[i * 4 + 1] = L.cy - (y2 + 0.1) * L.s * k;
      P[i * 4 + 2] = clamp01((z2 + 1) / 2); P[i * 4 + 3] = k;
      const [nx, ny, nz] = N[i], nx1 = nx * cyw + nz * syw, nz1 = -nx * syw + nz * cyw;
      const ny2 = ny * cp - nz1 * sp;
      R[i] = ny * sp + nz1 * cp;
      LIT[i] = Math.max(0, -0.45 * nx1 + 0.55 * ny2 + 0.7 * R[i]);
    }
    const far = cyw > 0 ? -1 : 1;  // the hemisphere turned away from us; its inner wall is behind the near one
    for (let i = 0; i < n; i++) if (INNER[i] === far) R[i] = Math.min(R[i], -0.01);
    const cerebBehind = -0.4 * sp + 0.56 * syw * cp < 0.05;  // its centre turned away from us
    if (cerebBehind) for (let i = CEREB[0]; i < CEREB[1]; i++) R[i] = Math.min(R[i], -0.01);
    const by = L.s * 0.98, scanY = L.cy - by + ((t * 0.18) % 1) * 2 * by;
    // facets: a faint wash, darker in the folds and on the side turned away from the light
    ctx.fillStyle = INK;
    for (const b of buckets) b.length = 0;
    for (const f of F) {
      const [a, b, c] = f, face = (R[a] + R[b] + R[c]) / 3;
      if (face <= 0) continue;
      const fold = (VAL[a] + VAL[b] + VAL[c]) / 3, lit = (LIT[a] + LIT[b] + LIT[c]) / 3;
      const v = clamp01(0.6 * (1 - lit) + 0.25 * fold);
      buckets[Math.min(BUCKETS - 1, (v * BUCKETS) | 0)].push(f);
    }
    buckets.forEach((list, k) => {
      if (!list.length) return;
      ctx.globalAlpha = L.alpha * 0.16 * ((k + 0.5) / BUCKETS);
      ctx.beginPath();
      for (const [a, b, c] of list) { ctx.moveTo(P[a * 4], P[a * 4 + 1]); ctx.lineTo(P[b * 4], P[b * 4 + 1]); ctx.lineTo(P[c * 4], P[c * 4 + 1]); }
      ctx.fill();
    });
    // edges: the front clear, the back a whisper; the folds drawn darker; brighter where the scan passes
    for (const b of buckets) b.length = 0;
    for (const e of E) {
      const [a, b] = e, face = (R[a] + R[b]) / 2;
      const fold = (VAL[a] + VAL[b]) / 2, my = (P[a * 4 + 1] + P[b * 4 + 1]) / 2;
      const scan = Math.exp(-(((my - scanY) / 7) ** 2));
      const v = face > 0 ? 0.05 + 0.22 * face + 0.2 * fold * face + 0.35 * scan : 0.02 + 0.03 * scan;
      buckets[Math.min(BUCKETS - 1, (clamp01(v) * BUCKETS) | 0)].push(e);
    }
    ctx.strokeStyle = INK; ctx.lineWidth = 0.4;
    buckets.forEach((list, k) => {
      if (!list.length) return;
      ctx.globalAlpha = L.alpha * ((k + 0.5) / BUCKETS) * 0.85;
      ctx.beginPath();
      for (const [a, b] of list) { ctx.moveTo(P[a * 4], P[a * 4 + 1]); ctx.lineTo(P[b * 4], P[b * 4 + 1]); }
      ctx.stroke();
    });
    // the sulci: smooth dark lines along the folds' floors, the two great fissures heavier
    for (const b of buckets) b.length = 0;
    const segs = SEG.length / SEG_STRIDE;
    for (let k = 0; k < segs; k++) {
      const o = k * SEG_STRIDE;
      const nx = SEG[o + 6], ny = SEG[o + 7], nz = SEG[o + 8];
      const face = ny * sp + (-nx * syw + nz * cyw) * cp;
      if (face <= 0.02 || SEG[o + 9] === far || (cerebBehind && SEG[o + 10] === 2)) continue;
      for (let e = 0; e < 2; e++) {
        const x = SEG[o + e * 3], y = SEG[o + e * 3 + 1], z = SEG[o + e * 3 + 2];
        const x1 = x * cyw + z * syw, z1 = -x * syw + z * cyw;
        const y2 = y * cp - z1 * sp, z2 = y * sp + z1 * cp, kk = fl / (fl - z2);
        SP[k * 5 + e * 2] = L.cx + x1 * L.s * kk; SP[k * 5 + e * 2 + 1] = L.cy - (y2 + 0.1) * L.s * kk;
      }
      const my = (SP[k * 5 + 1] + SP[k * 5 + 3]) / 2, scan = Math.exp(-(((my - scanY) / 7) ** 2));
      const v = clamp01(0.2 + 0.7 * Math.pow(face, 0.7) + 0.4 * scan + (SEG[o + 10] === 1 ? 0.25 : 0));
      buckets[Math.min(BUCKETS - 1, (v * BUCKETS) | 0)].push(k);
    }
    ctx.lineWidth = 0.85; ctx.lineCap = "round";
    buckets.forEach((list, k) => {
      if (!list.length) return;
      ctx.globalAlpha = L.alpha * ((k + 0.5) / BUCKETS) * 0.95;
      ctx.beginPath();
      for (const i of list) { ctx.moveTo(SP[i * 5], SP[i * 5 + 1]); ctx.lineTo(SP[i * 5 + 2], SP[i * 5 + 3]); }
      ctx.stroke();
    });
    ctx.lineCap = "butt";
    if (L.hud) drawHUD(L, t, yaw, scanY);
    // marked memories, called out with a leader line
    ctx.fillStyle = INK; ctx.strokeStyle = INK; ctx.lineWidth = 0.6;
    marks.forEach((i, k) => {
      if (R[i] <= 0.15) return;
      const x = P[i * 4], y = P[i * 4 + 1], a = L.alpha * clamp01(R[i] * 2);
      const ping = ((t * 0.6 + k * 0.37) % 1);
      ctx.globalAlpha = a; ctx.beginPath(); ctx.arc(x, y, 2.2, 0, Math.PI * 2); ctx.fill();
      ctx.globalAlpha = a * (1 - ping); ctx.beginPath(); ctx.arc(x, y, 3 + ping * 12, 0, Math.PI * 2); ctx.stroke();
      if (L.hud && k < 3) {
        const ex = x + (k % 2 ? 46 : -46), ey = y - 26 - k * 6;
        ctx.globalAlpha = a * 0.6; ctx.beginPath(); ctx.moveTo(x, y); ctx.lineTo(ex, ey); ctx.lineTo(ex + (k % 2 ? 26 : -26), ey); ctx.stroke();
        text(`N.${String(i).padStart(4, "0")}`, ex + (k % 2 ? 28 : -28), ey + 3, k % 2 ? "left" : "right", a * 0.8);
      }
    });
    // pulses running along the edges, like signals
    if (!reduce) {
      while (pulses.length < (generating ? PULSES * 2 : PULSES)) {
        const a = (Math.random() * n) | 0, nb = V.nb[a];
        if (nb.length) pulses.push({ a, b: nb[(Math.random() * nb.length) | 0], t: 0, v: 2.4 + Math.random() * 2, hops: 6 + ((Math.random() * 10) | 0) });
      }
      pulses = pulses.filter((p) => {
        p.t += p.v * dt;
        if (p.t >= 1) {
          if (--p.hops <= 0) return false;
          const nb = V.nb[p.b];
          p.a = p.b; p.b = nb[(Math.random() * nb.length) | 0]; p.t = 0;
        }
        const face = (R[p.a] + R[p.b]) / 2;
        if (face <= 0) return true;
        const x = P[p.a * 4] + (P[p.b * 4] - P[p.a * 4]) * p.t, y = P[p.a * 4 + 1] + (P[p.b * 4 + 1] - P[p.a * 4 + 1]) * p.t;
        ctx.globalAlpha = L.alpha * (0.3 + 0.7 * face);
        ctx.beginPath(); ctx.arc(x, y, 1.3 + face, 0, Math.PI * 2); ctx.fill();
        return true;
      });
    }

    // what leaves the brain
    if (!reduce) {
      emitCarry += (generating ? 22 : emitRate) * dt;
      const k = Math.floor(emitCarry);
      if (k) { emit(k, null); emitCarry -= k; }
    }
    ctx.lineWidth = 0.6;
    flyers = flyers.filter((f) => {
      f.t += dt / f.dur;
      if (f.t >= 1) return false;
      const e = f.t < 0.5 ? 2 * f.t * f.t : 1 - Math.pow(-2 * f.t + 2, 2) / 2;
      const u = 1 - e;
      const x = u * u * f.sx + 2 * u * e * f.mx + e * e * f.tx, y = u * u * f.sy + 2 * u * e * f.my + e * e * f.ty;
      const fd = Math.sin(Math.PI * f.t);
      ctx.globalAlpha = 0.5 * fd;
      ctx.beginPath(); ctx.moveTo(f.px, f.py); ctx.lineTo(x, y); ctx.stroke();
      ctx.globalAlpha = 0.85 * fd;
      ctx.fillRect(x - 0.9, y - 0.9, 1.8, 1.8);
      f.px = x; f.py = y;
      return true;
    });
    ctx.globalAlpha = 1;
    raf = requestAnimationFrame(frame);
  }

  function start() {
    shape(); resize();
    if (reduce) { generating = false; t0 = performance.now(); last = t0; frame(t0); cancelAnimationFrame(raf); raf = 0; return; }
    if (!raf) { t0 = t0 || performance.now(); last = performance.now(); raf = requestAnimationFrame(frame); }
  }
  window.addEventListener("resize", () => { if (!room.hidden) resize(); });
  document.addEventListener("visibilitychange", () => { if (!room.hidden && document.visibilityState === "visible") start(); });

  // ---- memory.md → the page ------------------------------------------------------------

  const ID = /\[((?:n|src)_[0-9a-f]+)(・脳に見当たらない|・見当たらない)?\]/g; // memories, and originals (最近の出来事)

  function inline(text) {
    const out = [];
    let rest = text;
    if (rest.startsWith("（読み）")) { out.push(el("span", { class: "yomi" }, "読み")); rest = rest.slice(4); }
    let at = 0;
    for (const m of rest.matchAll(new RegExp(`\\*\\*(.+?)\\*\\*|${ID.source}`, "g"))) {
      if (m.index > at) out.push(rest.slice(at, m.index));
      if (m[1] !== undefined) out.push(el("b", {}, m[1]));
      else if (m[3]) out.push(el("span", { class: "mid missing", title: "この id の記憶は脳に見当たりません" }, m[2].slice(-6)));
      else if (m[2].startsWith("src_")) out.push(el("button", { class: "mid", title: "この原文を開く", on: { click: () => openSource(m[2]) } }, m[2].slice(-6)));
      else out.push(el("button", { class: "mid", title: "この記憶を開く", on: { click: () => openMemory(m[2]) } }, m[2].slice(-6)));
      at = m.index + m[0].length;
    }
    if (at < rest.length) out.push(rest.slice(at));
    return out;
  }

  function blocks(md) {
    const out = [];
    let list = null, quote = null;
    const flush = () => { list = null; quote = null; };
    for (const raw of md.split("\n")) {
      const line = raw.trimEnd();
      if (!line.trim()) { flush(); continue; }
      let m;
      if ((m = line.match(/^(#{1,3})\s+(.*)$/))) {
        flush();
        out.push(el(`h${m[1].length + 1}`, { class: "mb" }, inline(m[2])));
      } else if ((m = line.match(/^\s*[-*]\s+(.*)$/))) {
        quote = null;
        if (!list) { list = el("ul", { class: "mb" }); out.push(list); }
        list.append(el("li", {}, inline(m[1])));
      } else if ((m = line.match(/^>\s?(.*)$/))) {
        list = null;
        if (!quote) { quote = el("blockquote", { class: "mb" }); out.push(quote); }
        quote.append(el("p", {}, inline(m[1])));
      } else {
        flush();
        out.push(el("p", { class: "mb" }, inline(line)));
      }
    }
    return out;
  }

  function openMemory(id) {
    showTab("graph");
    setTimeout(() => selectNode(id), 400);
  }

  async function reveal(md) {
    const parts = blocks(md);
    const mine = ++revealing; // a newer reveal takes over
    doc.replaceChildren(...parts);
    if (reduce) { parts.forEach((p) => p.classList.add("on")); return; }
    for (const p of parts) {
      await new Promise((r) => setTimeout(r, p.tagName === "UL" ? 260 : 140));
      if (mine !== revealing) return;
      if (room.hidden) { parts.forEach((q) => q.classList.add("on")); return; }
      emit(p.tagName === "UL" ? 26 : 14, p);
      setTimeout(() => p.classList.add("on"), 420);
    }
  }

  function describe(m) {
    if (m.running) return "読み直しています — 記憶を引き出しています…";
    if (m.error) return `読み直せませんでした: ${m.error}`;
    if (!m.markdown) return "まだ作っていません。";
    const at = m.generated_at ? new Date(m.generated_at) : null;
    const bits = [at ? `${at.getMonth() + 1}/${at.getDate()} ${String(at.getHours()).padStart(2, "0")}:${String(at.getMinutes()).padStart(2, "0")} に読んだもの` : null,
      m.changes_since ? `その後 ${m.changes_since} 件の変化` : "脳はその後変わっていません"];
    return bits.filter(Boolean).join(" · ");
  }

  const n = (x) => Number(x || 0).toLocaleString("ja-JP");
  function cortexTokens(m) {
    const u = m.usage || {};
    if (!m.markdown || !u.digest_tokens) return "";
    return `読んだ記憶 ${n(u.read)} / ${n(u.total)} 件 · AI に渡した量 約 ${n(u.digest_tokens)} トークン`
      + `（Claude Code の決まった前置きを含む入力は ${n(u.input_tokens)}・出力 ${n(u.output_tokens)}）`
      + (u.model ? ` · ${u.model}` : "") + (m.cost_usd != null ? ` · 料金換算 約 $${Number(m.cost_usd).toFixed(3)}` : "");
  }

  function showRecall(r) { // what an AI receives on "/思い出して" (spec v0.8 §8.1)
    const lines = r.profile.split("\n").filter((x) => x.startsWith("- "));
    const parts = [el("h2", { class: "mb on" }, "「/思い出して」で AI に渡すもの"),
      el("h3", { class: "mb on" }, "1. 会話の最初に、いつも渡すもの"),
      el("p", { class: "mb on" }, `本決まりで、どの仕事にも当てはまるルールだけ。いまは ${r.rules_shown} 件`
        + (r.rules_total > r.rules_shown ? `（全 ${r.rules_total} 件のうち、上限 ${n(r.profile_budget)} トークンに入る分）` : "") + "。"),
      lines.length ? el("ul", { class: "mb on" }, lines.map((x) => el("li", {}, inline(x.slice(2))))) :
        el("p", { class: "mb on hint" }, "まだ本決まりのルールはありません。ルールは事例から育ち、あなたが「そう」と言うと本決まりになります。"),
      el("h3", { class: "mb on" }, "2. 会話の最初に、いっしょに渡すもの — 最近の出来事"),
      r.recent ? el("p", { class: "mb on" }, `海馬にあるこの 1 週間の要約（上限 ${n(r.recent_budget)} トークン）。中身は「最近の出来事」のタブで見られます。`
        + "大脳皮質にまだ入っていない、いまの状況を AI に伝えるためのものです。ルールとしては扱わせません。") :
        el("p", { class: "mb on hint" }, "まだ作っていません。睡眠のあとに作ります（「最近の出来事」のタブからも作れます）。"),
      el("h3", { class: "mb on" }, "3. そのあと、話題ごとに思い出すもの"),
      el("p", { class: "mb on" }, `AI が「/思い出して」で話題を渡すたびに、関係する記憶を大脳皮質 → 海馬 → 本棚の順に探し、`
        + `1 回あたり最大 ${n(r.recall_budget)} トークンにまとめて渡します。何が入るかは、その時の話題で変わります。`)];
    doc.replaceChildren(...parts);
    $("#memory-tokens").textContent = `会話の最初: ルール 約 ${n(r.profile_tokens)} ＋ 最近の出来事 約 ${n(r.recent_tokens)} トークン · 思い出すたび: 最大 ${n(r.recall_budget)} トークン`;
  }

  function describeRecent(w) {
    if (w.running) return "書いています — 海馬の 1 週間を読んでいます…";
    const at = w.generated_at ? new Date(w.generated_at) : null;
    const how = { ai: "AI が要約", mechanical: "AI に頼めず、項目を並べただけ", none: "出来事なし" }[w.how] || "";
    return [at ? `${at.getMonth() + 1}/${at.getDate()} ${String(at.getHours()).padStart(2, "0")}:${String(at.getMinutes()).padStart(2, "0")} に作成` : null,
      w.sources != null ? `原文 ${w.sources} 件・案件 ${w.projects} 件` : null, how,
      w.bad_quotes ? `原文と一致しない引用 ${w.bad_quotes} 件` : null,
      w.error ? `AI のエラー: ${w.error}` : null].filter(Boolean).join(" · ");
  }
  function showRecent(w) {
    const u = w.usage || {};
    $("#memory-tokens").textContent = u.digest_tokens ? `AI に渡した原文 約 ${n(u.digest_tokens)} トークン（入力 ${n(u.input_tokens)}・出力 ${n(u.output_tokens)}）`
      + (u.model ? ` · ${u.model}` : "") + (w.cost_usd != null ? ` · 料金換算 約 $${Number(w.cost_usd).toFixed(3)}` : "") : "";
    $("#memory-meta").textContent = describeRecent(w);
    $("#memory-write").disabled = !!w.running;
    if (w.markdown && w.markdown !== shown) { shown = w.markdown; reveal(w.markdown); }
    if (!w.markdown && !w.running) {
      doc.replaceChildren(el("p", { class: "mb on hint" },
        "まだありません。「いま書き直す」を押すと、海馬にあるこの 1 週間の原文から作ります（数分。Claude Code を使います）。睡眠のあとにも自動で作り直します。"));
    }
  }

  let shown = null, polling = 0, view = "cortex", last_m = null;
  function setView(v) {
    view = v;
    $("#mv-cortex").setAttribute("aria-selected", String(v === "cortex"));
    $("#mv-recall").setAttribute("aria-selected", String(v === "recall"));
    $("#mv-recent").setAttribute("aria-selected", String(v === "recent"));
    $("#memory-write").hidden = v === "recall";
    $("#memory-meta").hidden = v === "recall";
    $("#memory-write").textContent = v === "recent" ? "いま書き直す" : "いま読み直す";
    shown = null;
    if (last_m) render(last_m);
  }
  $("#mv-cortex").addEventListener("click", () => setView("cortex"));
  $("#mv-recall").addEventListener("click", () => setView("recall"));
  $("#mv-recent").addEventListener("click", () => setView("recent"));

  function render(m) {
    if (view === "recall") { showRecall(m.recall_view); return; }
    if (view === "recent") { showRecent(m.recent || {}); return; }
    $("#memory-meta").textContent = describe(m);
    $("#memory-write").disabled = !!m.running;
    $("#memory-tokens").textContent = cortexTokens(m);
    if (m.markdown && m.markdown !== shown) { shown = m.markdown; reveal(m.markdown); }
    if (!m.markdown && !m.running) {
      doc.replaceChildren(el("p", { class: "mb on hint" },
        "まだ memory.md はありません。「いま読み直す」を押すと、大脳皮質の記憶を読んで作ります（1〜2 分。Claude Code を使います）。"
        + " 睡眠のあとにも自動で作り直します。"));
    }
  }

  async function load() {
    const m = await api("/api/memory");
    last_m = m;
    stats = m.stats || null;
    generating = !!m.running || !!(m.recent && m.recent.running);
    render(m);
    clearTimeout(polling);
    if (generating) polling = setTimeout(() => guarded(() => load()), 3000);
  }

  $("#memory-write").addEventListener("click", () => guarded(async () => {
    await api("/api/memory", view === "recent" ? { which: "recent" } : {});
    generating = true;
    await load();
  }));

  loaders.memory = () => { start(); guarded(() => load()); };
})();
