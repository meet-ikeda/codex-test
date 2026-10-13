/* exobrain — Room 06: memory.md, drawn out of the brain.
   A wireframe brain (the opening's brain in profile, covered with an even, closed triangular mesh) held still
   inside a scanner's instruments (a ticked dial, a scan line, this brain's real numbers, a signal trace)
   over a faint network of drifting points and light streaks; pulses run along its edges, particles leave it
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

  const STEP = 9;          // stencil pixels between mesh points (the sampled mesh averaged ~30)
  const ROUND_CELLS = 24;  // how far in from the outline the body reaches its full thickness
  const DEPTH = 1.55;      // the opening's brain is a profile; give it a rounder body to turn
  const PULSES = 22;
  const NET = 70;          // background network points
  const NET_LINK = 150;    // px
  let V = null, E = null, F = null, W = 0, H = 0, TOP = 0, DPR = 1, raf = 0, t0 = 0, last = 0, revealing = 0;
  let P = null;            // projected vertices: x, y, depth (0 back .. 1 front), scale
  let VAL = null, NRM = null, FACING = null, FACE_SIDE = null;  // in a fold; normal; turned toward us; shell
  let stats = null;        // real numbers for the readouts (from /api/memory)
  const MONO = "500 9px ui-monospace, SFMono-Regular, Menlo, monospace";
  const wave = new Float32Array(160);
  let flyers = [], pulses = [], net = [], streaks = [], emitRate = 1.4, emitCarry = 0, generating = false;

  // ---- the mesh -------------------------------------------------------------------------

  function shape() {
    if (V) return;
    const draw = window.exobrainBrainStencil;
    if (!draw) { V = []; E = []; F = []; VAL = []; FACE_SIDE = []; V.nb = []; P = new Float32Array(0); return; }
    // The opening's brain in profile, covered with an even triangular lattice (2026-10-13: the sampled points left
    // big facets and holes). A front and a back shell meet at the outline, so the surface is closed; the folds
    // (the stencil's white strokes) sink in a little and are drawn darker.
    const w = 1000, h = 720, sx = STEP, sy = STEP * 0.866;
    const tissue = draw(false).getImageData(0, 0, w, h).data, folds = draw(true).getImageData(0, 0, w, h).data;
    const ink = (d, x, y) => { const k = ((y | 0) * w + (x | 0)) * 4; return d[k + 3] > 128 && d[k] < 100; };
    const cols = Math.floor(w / sx), rows = Math.floor(h / sy);
    const px = (r, c) => c * sx + (r % 2) * sx / 2, py = (r) => r * sy;
    const cell = (r, c) => r * cols + c;
    const inside = new Uint8Array(rows * cols);
    for (let r = 0; r < rows; r++) for (let c = 0; c < cols; c++) inside[cell(r, c)] = ink(tissue, px(r, c), py(r)) ? 1 : 0;
    const around = (r, c) => (r % 2 ? [[r, c - 1], [r, c + 1], [r - 1, c], [r - 1, c + 1], [r + 1, c], [r + 1, c + 1]]
      : [[r, c - 1], [r, c + 1], [r - 1, c - 1], [r - 1, c], [r + 1, c - 1], [r + 1, c]])
      .filter(([a, b]) => a >= 0 && b >= 0 && a < rows && b < cols);
    // distance (in cells) to the outline, so the body rounds off toward its edge
    const dist = new Float32Array(rows * cols).fill(1e9), queue = [];
    for (let r = 0; r < rows; r++) for (let c = 0; c < cols; c++) {
      if (inside[cell(r, c)] && around(r, c).some(([a, b]) => !inside[cell(a, b)]) ) { dist[cell(r, c)] = 0; queue.push([r, c]); }
    }
    for (let q = 0; q < queue.length; q++) {
      const [r, c] = queue[q];
      for (const [a, b] of around(r, c)) if (inside[cell(a, b)] && dist[cell(a, b)] > dist[cell(r, c)] + 1) {
        dist[cell(a, b)] = dist[cell(r, c)] + 1; queue.push([a, b]);
      }
    }
    V = []; VAL = []; FACE_SIDE = [];
    const front = new Int32Array(rows * cols).fill(-1), back = new Int32Array(rows * cols).fill(-1);
    for (let r = 0; r < rows; r++) for (let c = 0; c < cols; c++) {
      const k = cell(r, c);
      if (!inside[k]) continue;
      const X = px(r, c), Y = py(r), x = (X - 520) / 415, y = -(Y - 350) / 415;
      const inStem = X > 360 && Y > 470 && X < 450;
      const nx = (X - 520) / 400, ny = (Y - 300) / 270;
      const body = inStem ? 0.06 : Math.max(0.05, 0.42 * Math.sqrt(Math.max(0, 1 - nx * nx * 0.8 - ny * ny * 0.6)));
      const e = Math.min(1, dist[k] / ROUND_CELLS);                 // a dome: rises like a circle from the outline
      const half = body * Math.sqrt(e * (2 - e));
      const fold = ink(folds, X, Y) ? 0 : 1;
      const z = half * DEPTH * (fold ? 0.86 : 1);
      front[k] = V.length; V.push([x, y, z]); VAL.push(fold); FACE_SIDE.push(dist[k] ? 1 : 0);
      if (dist[k]) { back[k] = V.length; V.push([x, y, -z]); VAL.push(fold); FACE_SIDE.push(-1); }
      else back[k] = front[k];            // on the outline the two shells share the point: no gap
    }
    F = [];
    for (let r = 0; r + 1 < rows; r++) for (let c = 0; c < cols; c++) {
      const tris = r % 2 ? [[[r, c], [r, c + 1], [r + 1, c + 1]], [[r, c], [r + 1, c], [r + 1, c + 1]]]
        : [[[r, c], [r, c + 1], [r + 1, c]], [[r, c], [r + 1, c - 1], [r + 1, c]]];
      for (const t of tris) {
        if (t.some(([a, b]) => b < 0 || b >= cols || !inside[cell(a, b)])) continue;
        F.push(t.map(([a, b]) => front[cell(a, b)]), t.map(([a, b]) => back[cell(a, b)]));
      }
    }
    const n = V.length, seen = new Set(), nb = Array.from({ length: n }, () => []);
    E = [];
    for (const [a, b, c] of F) for (const [i, j] of [[a, b], [b, c], [c, a]]) {
      const key = i < j ? i * 100000 + j : j * 100000 + i;
      if (!seen.has(key)) { seen.add(key); E.push([i, j]); nb[i].push(j); nb[j].push(i); }
    }
    // normals from the faces, turned outward (front shell toward +z, back toward -z)
    NRM = Array.from({ length: n }, () => [0, 0, 0]);
    for (const [a, b, c] of F) {
      const u = [V[b][0] - V[a][0], V[b][1] - V[a][1], V[b][2] - V[a][2]], v = [V[c][0] - V[a][0], V[c][1] - V[a][1], V[c][2] - V[a][2]];
      let cr = [u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0]];
      const zs = V[a][2] + V[b][2] + V[c][2];
      if ((cr[2] < 0) !== (zs < 0)) cr = cr.map((x) => -x);
      for (const i of [a, b, c]) { NRM[i][0] += cr[0]; NRM[i][1] += cr[1]; NRM[i][2] += cr[2]; }
    }
    NRM = NRM.map((x) => { const l = Math.hypot(...x) || 1; return [x[0] / l, x[1] / l, x[2] / l]; });
    V.nb = nb;
    P = new Float32Array(n * 4); FACING = new Float32Array(n);
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
      ? { cx: W * 0.5, cy: 150, s: Math.min(W * 0.34, 150), alpha: 0.35 }  // faint behind the text on a phone
      : { cx: W * 0.235, cy: H * 0.5, s: Math.min(W * 0.15, H * 0.33, 260), alpha: 1, hud: true };
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
      ctx.globalAlpha = 0.18; ctx.fillRect(a.x - 1, a.y - 1, 2, 2);
      for (let j = i + 1; j < net.length; j++) {
        const b = net[j], d = Math.hypot(a.x - b.x, a.y - b.y);
        if (d < NET_LINK) {
          ctx.globalAlpha = 0.09 * (1 - d / NET_LINK);
          ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke();
        }
      }
    }
    // light streaks, drawn in ink: long, thin, faint, drifting to the right
    while (streaks.length < 4) streaks.push({ y: Math.random() * H, x: -Math.random() * W, w: W * (0.3 + Math.random() * 0.4),
      v: 30 + Math.random() * 40, h: Math.random() < 0.5 ? 1 : 2 });
    streaks = streaks.filter((s) => {
      s.x += s.v * dt;
      if (s.x > W) return false;
      const g = ctx.createLinearGradient(s.x, 0, s.x + s.w, 0);
      g.addColorStop(0, "rgba(10,10,10,0)"); g.addColorStop(0.5, "rgba(10,10,10,0.08)"); g.addColorStop(1, "rgba(10,10,10,0)");
      ctx.globalAlpha = 1; ctx.fillStyle = g; ctx.fillRect(s.x, s.y, s.w, s.h);
      return true;
    });
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

    // the brain: a full slow turn, tipped a little toward us, in perspective
    const yaw = 0.44, pitch = -0.18;  // held still at a three-quarter view (2026-10-13: the turning brain was unsettling); the instruments move
    const cy = Math.cos(yaw), sy = Math.sin(yaw), cp = Math.cos(pitch), sp = Math.sin(pitch), f = 3.4;
    for (let i = 0; i < V.length; i++) {
      const [x, y, z] = V[i];
      const x1 = x * cy + z * sy, z1 = -x * sy + z * cy;
      const y2 = y * cp - z1 * sp, z2 = y * sp + z1 * cp;
      const k = f / (f - z2);
      P[i * 4] = L.cx + x1 * L.s * k; P[i * 4 + 1] = L.cy - y2 * L.s * k;
      P[i * 4 + 2] = Math.max(0, Math.min(1, (z2 + 0.75) / 1.5)); P[i * 4 + 3] = k;
      const [nx, ny, nz] = NRM[i], nz1 = -nx * sy + nz * cy;
      FACING[i] = ny * sp + nz1 * cp;
    }
    const by = L.s * 0.85, scanY = L.cy - by + ((t * 0.18) % 1) * 2 * by;
    // facets: the faintest wash, a little darker toward us (batched by shade: the mesh is fine now)
    ctx.fillStyle = INK; ctx.strokeStyle = INK;
    for (const b of buckets) b.length = 0;
    for (const f of F) {
      const [a, b, c] = f, face = (FACING[a] + FACING[b] + FACING[c]) / 3;
      if (face <= 0) continue;
      const d = (P[a * 4 + 2] + P[b * 4 + 2] + P[c * 4 + 2]) / 3;
      buckets[Math.min(BUCKETS - 1, (d * BUCKETS) | 0)].push(f);
    }
    buckets.forEach((list, k) => {
      if (!list.length) return;
      ctx.globalAlpha = L.alpha * 0.05 * ((k + 0.5) / BUCKETS);
      ctx.beginPath();
      for (const [a, b, c] of list) { ctx.moveTo(P[a * 4], P[a * 4 + 1]); ctx.lineTo(P[b * 4], P[b * 4 + 1]); ctx.lineTo(P[c * 4], P[c * 4 + 1]); }
      ctx.fill();
    });
    // edges: as before, clearer toward us; the side turned away a whisper; the folds darker; the scan line lights them
    for (const b of buckets) b.length = 0;
    for (const e of E) {
      const [a, b] = e, d = (P[a * 4 + 2] + P[b * 4 + 2]) / 2, face = (FACING[a] + FACING[b]) / 2;
      const fold = (VAL[a] + VAL[b]) / 2, my = (P[a * 4 + 1] + P[b * 4 + 1]) / 2;
      const scan = Math.exp(-(((my - scanY) / 7) ** 2));
      const v = face > -0.05 ? 0.05 + 0.5 * d * d + 0.35 * fold * d + 0.3 * scan : 0.02 + 0.04 * d;
      buckets[Math.min(BUCKETS - 1, (Math.min(1, v) * BUCKETS) | 0)].push(e);
    }
    ctx.lineWidth = 0.5;
    buckets.forEach((list, k) => {
      if (!list.length) return;
      ctx.globalAlpha = L.alpha * ((k + 0.5) / BUCKETS) * 0.85;
      ctx.beginPath();
      for (const [a, b] of list) { ctx.moveTo(P[a * 4], P[a * 4 + 1]); ctx.lineTo(P[b * 4], P[b * 4 + 1]); }
      ctx.stroke();
    });
    // the points, as before, but only every few (the mesh is ten times finer)
    for (let i = 0; i < V.length; i += 5) {
      if (FACING[i] <= 0) continue;
      const d = P[i * 4 + 2], r = (0.7 + 0.9 * d) * P[i * 4 + 3];
      ctx.globalAlpha = L.alpha * (0.15 + 0.6 * d);
      ctx.fillRect(P[i * 4] - r / 2, P[i * 4 + 1] - r / 2, r, r);
    }
    if (L.hud) drawHUD(L, t, yaw, scanY);
    // pulses running along the edges, like signals
    if (!reduce) {
      while (pulses.length < (generating ? PULSES * 2 : PULSES)) {
        const a = (Math.random() * V.length) | 0, nb = V.nb[a];
        if (nb.length) pulses.push({ a, b: nb[(Math.random() * nb.length) | 0], t: 0, v: 1.6 + Math.random() * 1.6, hops: 4 + ((Math.random() * 6) | 0) });
      }
      pulses = pulses.filter((p) => {
        p.t += p.v * dt;
        if (p.t >= 1) {
          if (--p.hops <= 0) return false;
          const nb = V.nb[p.b];
          if (!nb.length) return false;
          p.a = p.b; p.b = nb[(Math.random() * nb.length) | 0]; p.t = 0;
        }
        const x = P[p.a * 4] + (P[p.b * 4] - P[p.a * 4]) * p.t, y = P[p.a * 4 + 1] + (P[p.b * 4 + 1] - P[p.a * 4 + 1]) * p.t;
        const d = (P[p.a * 4 + 2] + P[p.b * 4 + 2]) / 2;
        if (FACING[p.a] + FACING[p.b] < 0) return true;
        ctx.globalAlpha = L.alpha * (0.25 + 0.75 * d);
        ctx.beginPath(); ctx.arc(x, y, 1.6 + d, 0, Math.PI * 2); ctx.fill();
        return true;
      });
    }

    // what leaves the brain
    if (!reduce) {
      emitCarry += (generating ? 22 : emitRate) * dt;
      const n = Math.floor(emitCarry);
      if (n) { emit(n, null); emitCarry -= n; }
    }
    ctx.lineWidth = 0.6;
    flyers = flyers.filter((f) => {
      f.t += dt / f.dur;
      if (f.t >= 1) return false;
      const e = f.t < 0.5 ? 2 * f.t * f.t : 1 - Math.pow(-2 * f.t + 2, 2) / 2;
      const u = 1 - e;
      const x = u * u * f.sx + 2 * u * e * f.mx + e * e * f.tx, y = u * u * f.sy + 2 * u * e * f.my + e * e * f.ty;
      const fade = Math.sin(Math.PI * f.t);
      ctx.globalAlpha = 0.5 * fade;
      ctx.beginPath(); ctx.moveTo(f.px, f.py); ctx.lineTo(x, y); ctx.stroke();
      ctx.globalAlpha = 0.85 * fade;
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
