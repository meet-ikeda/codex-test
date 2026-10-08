/* exobrain — Room 06: memory.md, drawn out of the brain.
   A low-poly wireframe brain (the opening's brain, sampled into a mesh of points and thin edges) turns slowly
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

  const NV = 760;          // mesh vertices
  const K = 4;             // each vertex joins its nearest few
  const DEPTH = 1.55;      // the opening's brain is a profile; give it a rounder body to turn
  const PULSES = 22;
  const NET = 70;          // background network points
  const NET_LINK = 150;    // px
  let V = null, E = null, F = null, W = 0, H = 0, TOP = 0, DPR = 1, raf = 0, t0 = 0, last = 0, revealing = 0;
  let P = null;            // projected vertices: x, y, depth (0 back .. 1 front), scale
  let flyers = [], pulses = [], net = [], streaks = [], emitRate = 1.4, emitCarry = 0, generating = false;

  // ---- the mesh -------------------------------------------------------------------------

  function shape() {
    if (V) return;
    let cloud = window.exobrainBrainPoints ? window.exobrainBrainPoints(9000) : null;
    if (!cloud) { // the opening did not load: an ellipsoid
      cloud = [];
      for (let i = 0; i < 9000; i++) {
        const u = Math.random() * Math.PI * 2, v = Math.acos(2 * Math.random() - 1);
        cloud.push([Math.cos(u) * Math.sin(v), 0.7 * Math.cos(v), 0.4 * Math.sin(u) * Math.sin(v)]);
      }
    }
    // thin the cloud to evenly spaced vertices (a rough Poisson disc in 3D)
    const minD2 = 0.0052;
    V = [];
    for (const [x, y, z] of cloud) {
      const q = [x, y, z * DEPTH];
      if (V.every((w) => (w[0] - q[0]) ** 2 + (w[1] - q[1]) ** 2 + (w[2] - q[2]) ** 2 > minD2)) V.push(q);
      if (V.length >= NV) break;
    }
    // edges to the nearest neighbours, and the triangles they close
    const n = V.length, nb = [];
    const seen = new Set();
    E = [];
    for (let i = 0; i < n; i++) {
      const d = [];
      for (let j = 0; j < n; j++) if (j !== i) d.push([(V[i][0] - V[j][0]) ** 2 + (V[i][1] - V[j][1]) ** 2 + (V[i][2] - V[j][2]) ** 2, j]);
      d.sort((a, b) => a[0] - b[0]);
      nb[i] = d.slice(0, K).filter((x) => x[0] < 0.03).map((x) => x[1]);
      for (const j of nb[i]) {
        const key = i < j ? `${i}-${j}` : `${j}-${i}`;
        if (!seen.has(key)) { seen.add(key); E.push([i, j]); }
      }
    }
    F = [];
    const fs = new Set();
    for (let i = 0; i < n; i++) for (const j of nb[i]) for (const k of nb[i]) {
      if (j < k && (seen.has(j < k ? `${j}-${k}` : `${k}-${j}`))) {
        const key = [i, j, k].sort((a, b) => a - b).join("-");
        if (!fs.has(key)) { fs.add(key); F.push([i, j, k]); }
      }
    }
    V.nb = nb;
    P = new Float32Array(n * 4);
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
      : { cx: W * 0.25, cy: H * 0.5, s: Math.min(W * 0.2, H * 0.4, 300), alpha: 1 };
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

  function frame(now) {
    if (room.hidden) { raf = 0; return; } // requestAnimationFrame itself rests while the window is hidden
    const t = (now - t0) / 1000, dt = Math.min(0.05, (now - last) / 1000 || 0.016);
    last = now;
    const L = layout();
    ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
    ctx.clearRect(0, 0, W, H);
    drawNetwork(dt);

    // the brain: a full slow turn, tipped a little toward us, in perspective
    const yaw = t * (generating ? 0.5 : 0.18), pitch = -0.18;
    const cy = Math.cos(yaw), sy = Math.sin(yaw), cp = Math.cos(pitch), sp = Math.sin(pitch), f = 3.4;
    for (let i = 0; i < V.length; i++) {
      const [x, y, z] = V[i];
      const x1 = x * cy + z * sy, z1 = -x * sy + z * cy;
      const y2 = y * cp - z1 * sp, z2 = y * sp + z1 * cp;
      const k = f / (f - z2);
      P[i * 4] = L.cx + x1 * L.s * k; P[i * 4 + 1] = L.cy - y2 * L.s * k;
      P[i * 4 + 2] = Math.max(0, Math.min(1, (z2 + 0.75) / 1.5)); P[i * 4 + 3] = k;
    }
    ctx.fillStyle = INK; ctx.strokeStyle = INK;
    for (const [a, b, c] of F) { // facets: the faintest wash, a little darker toward us
      const d = (P[a * 4 + 2] + P[b * 4 + 2] + P[c * 4 + 2]) / 3;
      ctx.globalAlpha = L.alpha * 0.035 * d;
      ctx.beginPath(); ctx.moveTo(P[a * 4], P[a * 4 + 1]); ctx.lineTo(P[b * 4], P[b * 4 + 1]); ctx.lineTo(P[c * 4], P[c * 4 + 1]); ctx.fill();
    }
    ctx.lineWidth = 0.55;
    for (const [a, b] of E) {
      const d = (P[a * 4 + 2] + P[b * 4 + 2]) / 2;
      ctx.globalAlpha = L.alpha * (0.06 + 0.42 * d * d);
      ctx.beginPath(); ctx.moveTo(P[a * 4], P[a * 4 + 1]); ctx.lineTo(P[b * 4], P[b * 4 + 1]); ctx.stroke();
    }
    for (let i = 0; i < V.length; i++) {
      const d = P[i * 4 + 2], r = (0.8 + 1.1 * d) * P[i * 4 + 3];
      ctx.globalAlpha = L.alpha * (0.15 + 0.7 * d);
      ctx.fillRect(P[i * 4] - r / 2, P[i * 4 + 1] - r / 2, r, r);
    }
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

  const ID = /\[(n_[0-9a-f]+)(・脳に見当たらない)?\]/g;

  function inline(text) {
    const out = [];
    let rest = text;
    if (rest.startsWith("（読み）")) { out.push(el("span", { class: "yomi" }, "読み")); rest = rest.slice(4); }
    let at = 0;
    for (const m of rest.matchAll(new RegExp(`\\*\\*(.+?)\\*\\*|${ID.source}`, "g"))) {
      if (m.index > at) out.push(rest.slice(at, m.index));
      if (m[1] !== undefined) out.push(el("b", {}, m[1]));
      else if (m[3]) out.push(el("span", { class: "mid missing", title: "この id の記憶は脳に見当たりません" }, m[2].slice(-6)));
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

  let shown = null, polling = 0;
  async function load(force = false) {
    const m = await api("/api/memory");
    generating = !!m.running;
    $("#memory-meta").textContent = describe(m);
    $("#memory-write").disabled = !!m.running;
    if (m.markdown && (force || m.markdown !== shown)) { shown = m.markdown; reveal(m.markdown); }
    if (!m.markdown && !m.running) {
      doc.replaceChildren(el("p", { class: "mb on hint" },
        "まだ memory.md はありません。「いま読み直す」を押すと、大脳皮質の記憶を読んで作ります（1〜2 分。Claude Code を使います）。"
        + " 睡眠のあとにも自動で作り直します。"));
    }
    clearTimeout(polling);
    if (m.running) polling = setTimeout(() => guarded(() => load()), 3000);
  }

  $("#memory-write").addEventListener("click", () => guarded(async () => {
    await api("/api/memory", {});
    generating = true;
    await load();
  }));

  loaders.memory = () => { start(); guarded(() => load()); };
})();
