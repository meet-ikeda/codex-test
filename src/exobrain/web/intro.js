/* exobrain — opening: ink particles on the white wall.
   A drifting cloud gathers into a brain, bursts, and settles into the title.
   Plain Canvas 2D, no libraries. Skippable; shown once per browser session. */
"use strict";

(function () {
  window.exobrainBrainPoints = (n) => brainPoints(n); // shared with memory.js (Room 06)
  const overlay = document.getElementById("intro");
  const canvas = document.getElementById("intro-canvas");
  if (!overlay || !canvas) return;
  const ctx = canvas.getContext("2d");
  const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  // ---- timeline (seconds) -------------------------------------------------------------
  const T = { brain: 0.5, brainDur: 2.3, burst: 4.3, burstDur: 0.9, title: 5.0, titleDur: 1.9, fade: 7.5, end: 8.3 };
  const STAGGER = 0.35;

  let N, P, running = false, start = 0, raf = 0, mouse = { x: -1e4, y: -1e4 }, W = 0, H = 0, DPR = 1;

  // ---- shapes -------------------------------------------------------------------------
  function rand(a, b) { return a + Math.random() * (b - a); }
  function gauss() { return (Math.random() + Math.random() + Math.random() - 1.5) / 1.5; }

  // A brain in profile (front to the right), drawn as a stencil: ink for tissue, white strokes for
  // the sulci. Particles sample the ink; depth follows the outline so the brain can turn a little.
  function brainPoints(n) {
    const off = document.createElement("canvas");
    const w = 1000, h = 720;
    off.width = w; off.height = h;
    const g = off.getContext("2d");
    g.fillStyle = "#000";
    // cerebrum
    g.beginPath();
    g.moveTo(905, 385);
    g.bezierCurveTo(930, 215, 790, 95, 610, 88);
    g.bezierCurveTo(420, 72, 215, 118, 150, 250);
    g.bezierCurveTo(108, 335, 135, 425, 215, 455);
    g.bezierCurveTo(268, 474, 330, 458, 385, 468);
    g.bezierCurveTo(430, 560, 615, 585, 725, 522);
    g.bezierCurveTo(785, 492, 828, 470, 862, 452);
    g.bezierCurveTo(892, 432, 908, 410, 905, 385);
    g.fill();
    // cerebellum and brainstem
    g.beginPath(); g.ellipse(262, 520, 118, 68, -0.12, 0, Math.PI * 2); g.fill();
    g.beginPath(); g.moveTo(372, 470); g.lineTo(425, 470); g.lineTo(438, 660); g.lineTo(398, 662); g.closePath(); g.fill();
    // sulci: the lateral fissure, the central sulcus, and the smaller folds
    g.strokeStyle = "#fff"; g.lineCap = "round"; g.lineJoin = "round";
    const S = [
      [16, [[745, 470], [650, 430], [560, 400], [470, 365]]],          // lateral (Sylvian) fissure
      [13, [[585, 92], [565, 170], [545, 250], [520, 340]]],             // central sulcus
      [10, [[700, 110], [690, 190], [705, 260], [690, 330]]],            // precentral
      [10, [[455, 90], [470, 170], [450, 240], [430, 320]]],             // postcentral
      [10, [[820, 200], [760, 230], [720, 300], [770, 360]]],            // frontal folds
      [9, [[860, 290], [800, 300], [785, 360], [840, 400]]],
      [9, [[650, 200], [610, 240], [640, 300], [600, 360]]],
      [9, [[330, 120], [360, 190], [320, 250], [350, 330]]],            // parietal
      [9, [[230, 190], [280, 230], [250, 300], [290, 360]]],            // occipital
      [9, [[190, 330], [240, 360], [220, 410], [270, 430]]],
      [9, [[430, 425], [520, 450], [600, 470], [690, 495]]],            // superior temporal
      [8, [[470, 505], [560, 530], [640, 525]]],                        // inferior temporal
      [8, [[380, 400], [360, 440], [330, 440]]],
    ];
    for (const [lw, pts] of S) {
      g.lineWidth = lw;
      g.beginPath();
      g.moveTo(pts[0][0], pts[0][1]);
      for (let i = 1; i < pts.length - 1; i++) {
        const mx = (pts[i][0] + pts[i + 1][0]) / 2, my = (pts[i][1] + pts[i + 1][1]) / 2;
        g.quadraticCurveTo(pts[i][0], pts[i][1], mx, my);
      }
      g.lineTo(pts[pts.length - 1][0], pts[pts.length - 1][1]);
      g.stroke();
    }
    g.lineWidth = 6; // the cerebellum's fine stripes
    for (let k = -3; k <= 3; k++) { g.beginPath(); g.ellipse(262, 520 + k * 16, 118, 10, -0.12, Math.PI * 0.1, Math.PI * 0.9); g.stroke(); }
    const data = g.getImageData(0, 0, w, h).data;
    const pts = [];
    for (let y = 0; y < h; y += 2) for (let x = 0; x < w; x += 2) if (data[(y * w + x) * 4 + 3] > 128 && data[(y * w + x) * 4] < 100) pts.push([x, y]);
    const out = [];
    for (let i = 0; i < n; i++) {
      const [px, py] = pts[(Math.random() * pts.length) | 0];
      const x = (px - 520) / 415, y = -(py - 350) / 415;
      // depth: thick in the middle of the cerebrum, thin at the edges, stem and cerebellum narrower
      const inStem = px > 360 && py > 470 && px < 450;
      const nx = (px - 520) / 400, ny = (py - 300) / 270;
      const half = inStem ? 0.06 : Math.max(0.05, 0.42 * Math.sqrt(Math.max(0, 1 - nx * nx * 0.8 - ny * ny * 0.6)));
      const side = Math.random() < 0.5 ? -1 : 1;
      const z = Math.random() < 0.7 ? side * half * (0.85 + Math.random() * 0.15) : (Math.random() * 2 - 1) * half;
      out.push([x, y, z]);
    }
    return out;
  }

  function textPoints(n) {
    const off = document.createElement("canvas");
    const w = 1400, h = 420;
    off.width = w; off.height = h;
    const g = off.getContext("2d");
    g.fillStyle = "#000";
    g.textAlign = "center";
    g.textBaseline = "alphabetic";
    g.font = '300 250px "Inter Tight", "Helvetica Neue", sans-serif';
    g.fillText("exobrain", w / 2, 270);
    g.font = '400 44px "Hiragino Sans", "Noto Sans JP", sans-serif';
    g.fillText("外 部 脳", w / 2, 370);
    const data = g.getImageData(0, 0, w, h).data;
    const pts = [];
    for (let y = 0; y < h; y += 2) for (let x = 0; x < w; x += 2) if (data[(y * w + x) * 4 + 3] > 128) pts.push([x, y]);
    const out = [];
    for (let i = 0; i < n; i++) {
      const [x, y] = pts[(Math.random() * pts.length) | 0];
      out.push([(x - w / 2) / 470 + rand(-0.004, 0.004), -(y - h / 2) / 470, rand(-0.03, 0.03)]);
    }
    return out;
  }

  // ---- particles ----------------------------------------------------------------------
  function build() {
    N = Math.min(16000, Math.max(6000, Math.round((W * H) / 110)));
    const title = textPoints(N);
    const brain = brainPoints(N);
    P = new Array(N);
    for (let i = 0; i < N; i++) {
      const b = brain[i];
      const len = Math.hypot(b[0], b[1], b[2]) || 1;
      const k = rand(1.6, 3.4);
      P[i] = {
        cloud: [rand(-2.6, 2.6), rand(-1.6, 1.6), rand(-1.5, 1.5)],
        brain: b,
        burst: [b[0] / len * k + gauss() * 0.4, b[1] / len * k + gauss() * 0.4, b[2] / len * k + gauss() * 0.4],
        title: title[i],
        delay: Math.random() * STAGGER,
        size: Math.random() < 0.12 ? 1.9 : Math.random() < 0.5 ? 1.25 : 0.9,
        seed: Math.random() * 1000,
      };
    }
  }

  const easeInOut = (t) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);
  const easeOutExpo = (t) => (t >= 1 ? 1 : 1 - Math.pow(2, -9 * t));
  function progress(t, s, dur, d, ease) {
    const local = (t - s - d * dur) / (dur * (1 - STAGGER));
    return ease(Math.min(1, Math.max(0, local)));
  }
  const mix = (a, b, k) => [a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k, a[2] + (b[2] - a[2]) * k];

  function positionAt(p, t) {
    let q = p.cloud;
    q = mix(q, p.brain, progress(t, T.brain, T.brainDur, p.delay, easeInOut));
    if (t > T.burst) q = mix(q, p.burst, progress(t, T.burst, T.burstDur, p.delay * 0.4, easeOutExpo));
    if (t > T.title) q = mix(q, p.title, progress(t, T.title, T.titleDur, p.delay, easeInOut));
    return q;
  }

  // The brain turns slowly while it holds; the title faces us.
  function rotationAt(t) {
    const brainHold = Math.min(1, Math.max(0, (t - T.brain) / T.brainDur));
    const toTitle = Math.min(1, Math.max(0, (t - T.title) / T.titleDur));
    const swing = Math.sin((t - T.brain) * 1.1) * 0.42 * brainHold;
    return swing * (1 - easeInOut(toTitle));
  }

  // ---- drawing ------------------------------------------------------------------------
  const BUCKETS = 5;
  function frame(now) {
    const t = (now - start) / 1000;
    ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
    ctx.clearRect(0, 0, W, H);
    const rot = rotationAt(t), cs = Math.cos(rot), sn = Math.sin(rot);
    const scale = Math.min(W, H * 1.35) * 0.3, cx = W / 2, cy = H / 2;
    const buckets = Array.from({ length: BUCKETS }, () => []);
    for (let i = 0; i < N; i++) {
      const p = P[i];
      const q = positionAt(p, t);
      const drift = t < T.brain + 0.2 || (t > T.burst && t < T.title + 0.5) ? 0.02 : 0.004;
      const x0 = q[0] + Math.sin(t * 1.3 + p.seed) * drift, y0 = q[1] + Math.cos(t * 1.1 + p.seed) * drift;
      const x = x0 * cs + q[2] * sn, z = -x0 * sn + q[2] * cs;
      const f = 3.4 / (3.4 + z);
      let sx = cx + x * scale * f, sy = cy - y0 * scale * f;
      const dx = sx - mouse.x, dy = sy - mouse.y, d2 = dx * dx + dy * dy;
      if (d2 < 9000) { // the cursor parts the ink
        const push = (1 - d2 / 9000) * 26 / (Math.sqrt(d2) + 1);
        sx += dx * push; sy += dy * push;
      }
      const b = Math.min(BUCKETS - 1, Math.max(0, Math.floor((z + 1.4) / 2.8 * BUCKETS)));
      buckets[b].push(sx, sy, Math.min(2.2, p.size * f));
    }
    const fade = Math.min(1, Math.max(0, (t - T.fade) / (T.end - T.fade)));
    for (let b = BUCKETS - 1; b >= 0; b--) { // far first, lighter
      ctx.fillStyle = `rgba(10,10,10,${(0.28 + (BUCKETS - 1 - b) * 0.16) * (1 - fade)})`;
      const arr = buckets[b];
      for (let i = 0; i < arr.length; i += 3) ctx.fillRect(arr[i], arr[i + 1], arr[i + 2], arr[i + 2]);
    }
    overlay.style.setProperty("opacity", String(1 - fade));
    if (t >= T.end) return finish();
    raf = requestAnimationFrame(frame);
  }

  // ---- lifecycle ----------------------------------------------------------------------
  function resize() {
    DPR = Math.min(2, window.devicePixelRatio || 1);
    W = window.innerWidth; H = window.innerHeight;
    canvas.width = Math.round(W * DPR); canvas.height = Math.round(H * DPR);
  }

  function finish() {
    running = false;
    cancelAnimationFrame(raf);
    overlay.hidden = true;
    overlay.style.removeProperty("opacity");
    document.body.classList.remove("intro-on");
  }

  async function play() {
    if (running) return;
    running = true;
    overlay.hidden = false;
    document.body.classList.add("intro-on");
    resize();
    try { await document.fonts.load('300 250px "Inter Tight"'); } catch (e) { /* the fallback font is fine */ }
    build();
    start = performance.now();
    raf = requestAnimationFrame(frame);
  }

  function skip() {
    if (!running) return;
    start = performance.now() - T.fade * 1000; // jump to the fade, never cut
  }

  overlay.addEventListener("click", skip);
  window.addEventListener("keydown", (e) => { if (running && e.key !== "Tab") skip(); });
  window.addEventListener("resize", () => { if (running) resize(); });
  overlay.addEventListener("mousemove", (e) => { mouse = { x: e.clientX, y: e.clientY }; });
  overlay.addEventListener("mouseleave", () => { mouse = { x: -1e4, y: -1e4 }; });

  window.exobrainIntro = play;
  let seen = false;
  try { seen = sessionStorage.getItem("exobrain-intro") === "1"; sessionStorage.setItem("exobrain-intro", "1"); } catch (e) { /* private mode */ }
  if (!seen && !reduce) play();
  else overlay.hidden = true;
})();
