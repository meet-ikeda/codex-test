/* exobrain — Room 06: memory.md, drawn out of the brain.
   The brain from the opening turns slowly while a scan line passes through it; particles leave the
   tissue it touches and fly to the page, and each paragraph appears as its particles arrive.
   Ink on the white wall, Canvas 2D, no libraries. Runs only while this room is open. */
"use strict";

(function () {
  const room = document.getElementById("tab-memory");
  const canvas = document.getElementById("memory-canvas");
  const doc = document.getElementById("memory-doc");
  if (!room || !canvas || !doc) return;
  const ctx = canvas.getContext("2d");
  const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  const N = 2400;
  let pts = null, W = 0, H = 0, DPR = 1, raf = 0, t0 = 0, last = 0;
  let flyers = [], emitRate = 1.2, emitCarry = 0, generating = false;
  let projected = new Float32Array(N * 3); // x, y, depth for this frame

  function shape() {
    if (pts) return;
    if (window.exobrainBrainPoints) pts = window.exobrainBrainPoints(N);
    else { // the opening did not load: a plain ellipsoid
      pts = [];
      for (let i = 0; i < N; i++) {
        const u = Math.random() * Math.PI * 2, v = Math.acos(2 * Math.random() - 1);
        pts.push([Math.cos(u) * Math.sin(v), 0.7 * Math.cos(v), 0.45 * Math.sin(u) * Math.sin(v)]);
      }
    }
  }

  function resize() {
    const r = room.getBoundingClientRect();
    DPR = Math.min(2, window.devicePixelRatio || 1);
    W = r.width; H = Math.max(r.height, window.innerHeight - r.top);
    canvas.width = Math.round(W * DPR); canvas.height = Math.round(H * DPR);
    canvas.style.width = `${W}px`; canvas.style.height = `${H}px`;
  }

  function layout() {
    const narrow = W < 900;
    const vh = Math.min(H, window.innerHeight);
    return narrow
      ? { cx: W * 0.5, cy: 190, s: Math.min(W * 0.36, 170), alpha: 0.35 }
      : { cx: W * 0.25, cy: Math.min(vh * 0.5, 430), s: Math.min(W * 0.2, vh * 0.42, 300), alpha: 0.6 };
  }

  // ---- particles flying from the brain to the page ------------------------------------

  function target(block) {
    const rr = room.getBoundingClientRect();
    if (block) {
      const b = block.getBoundingClientRect();
      return { x: b.left - rr.left - 10, y: b.top - rr.top + Math.random() * Math.max(8, b.height) };
    }
    const d = doc.getBoundingClientRect();
    return { x: d.left - rr.left - 10, y: d.top - rr.top + 20 + Math.random() * Math.min(d.height + 40, window.innerHeight * 0.6) };
  }

  function emit(n, block, scanX) {
    if (!pts) return;
    for (let k = 0; k < n; k++) {
      // start where the scan line is (or anywhere on the surface)
      let i = 0;
      for (let tries = 0; tries < 12; tries++) {
        i = (Math.random() * N) | 0;
        if (scanX == null || Math.abs(projected[i * 3] - scanX) < 14) break;
      }
      const sx = projected[i * 3], sy = projected[i * 3 + 1];
      const tg = target(block);
      const mx = (sx + tg.x) / 2 + (Math.random() - 0.5) * 60, my = Math.min(sy, tg.y) - 40 - Math.random() * 120;
      flyers.push({ sx, sy, mx, my, tx: tg.x, ty: tg.y, t: 0, dur: 0.9 + Math.random() * 0.9, px: sx, py: sy });
    }
  }

  // ---- frame ------------------------------------------------------------------------------

  function frame(now) {
    if (room.hidden || document.visibilityState !== "visible") { raf = 0; return; }
    const t = (now - t0) / 1000, dt = Math.min(0.05, (now - last) / 1000 || 0.016);
    last = now;
    const L = layout();
    ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
    ctx.clearRect(0, 0, W, H);

    // the brain, turning a little
    const a = Math.sin(t * 0.25) * 0.45, ca = Math.cos(a), sa = Math.sin(a);
    const breathe = 1 + Math.sin(t * 1.3) * 0.008;
    const period = generating ? 2.2 : 4.5;
    const phase = (t % period) / period;
    const scanX = L.cx + (phase * 2.3 - 1.15) * L.s; // sweeps across and a little beyond
    ctx.fillStyle = "#0a0a0a";
    for (let i = 0; i < N; i++) {
      const [x, y, z] = pts[i];
      const xr = x * ca + z * sa, zr = -x * sa + z * ca;
      const px = L.cx + xr * L.s * breathe, py = L.cy - y * L.s * breathe;
      projected[i * 3] = px; projected[i * 3 + 1] = py; projected[i * 3 + 2] = zr;
      const near = Math.max(0, 1 - Math.abs(px - scanX) / 18);
      ctx.globalAlpha = L.alpha * (0.35 + 0.45 * (zr + 0.5)) + near * 0.4;
      const r = 1 + near * 1.2;
      ctx.fillRect(px - r / 2, py - r / 2, r, r);
    }
    // the scan line
    ctx.globalAlpha = 0.22;
    ctx.fillRect(scanX, L.cy - L.s * 0.95, 1, L.s * 1.9);
    ctx.globalAlpha = 0.07;
    ctx.fillRect(scanX - 6, L.cy - L.s * 0.95, 12, L.s * 1.9);

    // what leaves the brain
    if (!reduce) {
      emitCarry += (generating ? 22 : emitRate) * dt;
      const n = Math.floor(emitCarry);
      if (n) { emit(n, null, scanX); emitCarry -= n; }
    }
    ctx.lineWidth = 0.6;
    ctx.strokeStyle = "#0a0a0a";
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
    if (reduce) { generating = false; t0 = performance.now(); frame(t0); cancelAnimationFrame(raf); raf = 0; return; }
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
    doc.replaceChildren(...parts);
    if (reduce) { parts.forEach((p) => p.classList.add("on")); return; }
    for (const p of parts) {
      await new Promise((r) => setTimeout(r, p.tagName === "UL" ? 260 : 140));
      if (room.hidden) { parts.forEach((q) => q.classList.add("on")); return; }
      emit(p.tagName === "UL" ? 26 : 14, p, null);
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
