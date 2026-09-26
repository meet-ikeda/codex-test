/* Galaxy: a WebGL particle renderer for the memory graph.
 *
 * Each memory is a small constellation: a core mark plus drifting dust whose amount grows with
 * the memory's strength. Kinds are told apart by the core's form (monochrome): knowledge = solid
 * disc, rule = ring, episode = soft glow, concept = cross. Links are hairlines. Ambient dust follows
 * the density of memories, so clusters read as nebulae on white.
 */
"use strict";

(function () {
  const SHAPE = { semantic: 0, procedural: 1, episode: 2, dust: 2, concept: 3 };

  const POINT_VS = `
    attribute vec2 a_pos; attribute float a_size; attribute float a_alpha; attribute float a_phase;
    attribute float a_shape; attribute float a_drift; attribute float a_state;
    uniform vec2 u_center; uniform vec2 u_viewport; uniform float u_scale; uniform float u_zoom;
    uniform float u_time; uniform float u_dpr; uniform float u_motion; uniform float u_focus;
    varying float v_alpha; varying float v_shape;
    void main() {
      float t = u_time * 0.35 + a_phase * 6.2831;
      vec2 wob = vec2(sin(t * 0.9 + a_phase * 13.0), cos(t * 0.7 + a_phase * 7.0)) * a_drift * u_motion;
      vec2 p = (a_pos + wob - u_center) * u_scale;
      gl_Position = vec4(p / (u_viewport * 0.5) * vec2(1.0, -1.0), 0.0, 1.0);
      float bright = step(1.5, a_state);
      float dim = u_focus * (1.0 - step(0.5, a_state));
      gl_PointSize = max(1.0, a_size * u_dpr * mix(1.0, 1.55, bright) * clamp(pow(u_zoom, 0.32), 0.7, 2.4));
      v_alpha = mix(a_alpha, min(1.0, a_alpha * 1.8 + 0.1), bright) * mix(1.0, 0.1, dim);
      v_shape = a_shape;
    }`;

  const POINT_FS = `
    precision mediump float;
    uniform vec3 u_ink; varying float v_alpha; varying float v_shape;
    void main() {
      vec2 c = gl_PointCoord * 2.0 - 1.0;
      float r = length(c);
      float a;
      if (v_shape < 0.5) {            // knowledge: solid disc
        a = 1.0 - smoothstep(0.72, 1.0, r);
      } else if (v_shape < 1.5) {     // rule: ring
        a = smoothstep(0.6, 0.72, r) * (1.0 - smoothstep(0.86, 1.0, r));
      } else if (v_shape < 2.5) {     // episode / dust: soft glow
        a = exp(-r * r * 3.2) * (1.0 - smoothstep(0.9, 1.0, r));
      } else {                        // concept: fine cross
        float bar = max(1.0 - smoothstep(0.04, 0.12, abs(c.x)), 1.0 - smoothstep(0.04, 0.12, abs(c.y)));
        a = bar * (1.0 - smoothstep(0.85, 1.0, r));
      }
      a *= v_alpha;
      if (a < 0.004) discard;
      gl_FragColor = vec4(u_ink * a, a);
    }`;

  const LINE_VS = `
    attribute vec2 a_pos; attribute float a_alpha;
    uniform vec2 u_center; uniform vec2 u_viewport; uniform float u_scale; uniform float u_mul;
    varying float v_alpha;
    void main() {
      vec2 p = (a_pos - u_center) * u_scale;
      gl_Position = vec4(p / (u_viewport * 0.5) * vec2(1.0, -1.0), 0.0, 1.0);
      v_alpha = a_alpha * u_mul;
    }`;

  const LINE_FS = `
    precision mediump float;
    uniform vec3 u_ink; varying float v_alpha;
    void main() { gl_FragColor = vec4(u_ink * v_alpha, v_alpha); }`;

  function hash(str) {
    let h = 2166136261;
    for (let i = 0; i < str.length; i++) { h ^= str.charCodeAt(i); h = Math.imul(h, 16777619); }
    return h >>> 0;
  }

  function rng(seed) { // mulberry32: the same memory always gets the same dust
    return function () {
      seed |= 0; seed = (seed + 0x6D2B79F5) | 0;
      let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  function gauss(r) { // Box-Muller
    const u = Math.max(1e-9, r()), v = r();
    return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
  }

  function compile(gl, vs, fs) {
    const prog = gl.createProgram();
    for (const [type, src] of [[gl.VERTEX_SHADER, vs], [gl.FRAGMENT_SHADER, fs]]) {
      const sh = gl.createShader(type);
      gl.shaderSource(sh, src);
      gl.compileShader(sh);
      if (!gl.getShaderParameter(sh, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(sh));
      gl.attachShader(prog, sh);
    }
    gl.linkProgram(prog);
    if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(prog));
    const loc = {};
    const n = gl.getProgramParameter(prog, gl.ACTIVE_ATTRIBUTES);
    for (let i = 0; i < n; i++) { const a = gl.getActiveAttrib(prog, i); loc[a.name] = gl.getAttribLocation(prog, a.name); }
    const m = gl.getProgramParameter(prog, gl.ACTIVE_UNIFORMS);
    for (let i = 0; i < m; i++) { const u = gl.getActiveUniform(prog, i); loc[u.name] = gl.getUniformLocation(prog, u.name); }
    return { prog, loc };
  }

  class Galaxy {
    constructor(container, opts = {}) {
      this.container = container;
      this.opts = opts;
      this.canvas = document.createElement("canvas");
      this.canvas.className = "galaxy-canvas";
      container.append(this.canvas);
      const gl = this.canvas.getContext("webgl", { antialias: true, premultipliedAlpha: true, alpha: true });
      if (!gl) throw new Error("WebGL が使えないため、グラフを表示できません。");
      this.gl = gl;
      this.points = compile(gl, POINT_VS, POINT_FS);
      this.lines = compile(gl, LINE_VS, LINE_FS);
      this.buf = { points: gl.createBuffer(), state: gl.createBuffer(), lines: gl.createBuffer(), focus: gl.createBuffer() };
      this.ink = [0, 0, 0];
      this.camera = { x: 0, y: 0, zoom: 1 };
      this.base = 1;
      this.focusMode = false;
      this.motion = !matchMedia("(prefers-reduced-motion: reduce)").matches;
      this.start = performance.now();
      this.nodes = [];
      this.index = new Map();
      this._bind();
      this.resizeObserver = new ResizeObserver(() => this.resize());
      this.resizeObserver.observe(container);
      this.resize();
      this._loop = this._loop.bind(this);
      this.raf = requestAnimationFrame(this._loop);
    }

    setInk(rgb) { this.ink = rgb; this.draw(); }

    /* nodes: [{id, kind, size, x, y, status}], edges: [{s, t, w}] (positions already laid out) */
    setData(nodes, edges) {
      this.nodes = nodes;
      this.index = new Map(nodes.map((n, i) => [n.id, i]));
      this.edges = edges;
      let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
      for (const n of nodes) { minX = Math.min(minX, n.x); maxX = Math.max(maxX, n.x); minY = Math.min(minY, n.y); maxY = Math.max(maxY, n.y); }
      if (!nodes.length) { minX = minY = -1; maxX = maxY = 1; }
      this.bounds = { minX, maxX, minY, maxY, cx: (minX + maxX) / 2, cy: (minY + maxY) / 2,
        extent: Math.max(maxX - minX, maxY - minY, 1e-6) };
      this._buildParticles();
      this._buildLines();
      this.fit(false);
    }

    _buildParticles() {
      const ext = this.bounds.extent;
      const data = [];
      const owner = [];
      const push = (x, y, size, alpha, phase, shape, drift, own) => { data.push(x, y, size, alpha, phase, shape, drift); owner.push(own); };
      // A sparse sky needs larger stars to be legible; a crowded one reads better with fine ones.
      const few = this.nodes.length < 300 ? 1 + 0.8 * (300 - this.nodes.length) / 300 : 1;
      // Particle budget: large skies get thinner dust and haze so the frame rate holds up.
      const dustScale = Math.min(1, 45000 / Math.max(1, this.nodes.length * 18));
      const hazeEvery = Math.max(1, Math.round(this.nodes.length / 1200));
      this.nodes.forEach((n, i) => {
        const r = rng(hash(n.id));
        const dormant = n.status === "dormant";
        if (n.kind === "concept") {
          push(n.x, n.y, 5 + Math.min(4, n.size * 0.3), dormant ? 0.2 : 0.45, r(), SHAPE.concept, 0, i);
          return;
        }
        const core = n.kind === "procedural" ? 3.6 + n.size * 0.26 : n.kind === "episode" ? 4 + n.size * 0.4 : 1.4 + n.size * 0.2;
        push(n.x, n.y, core * few, dormant ? 0.22 : n.kind === "episode" ? 0.6 : n.kind === "procedural" ? 0.7 : 0.85, r(), SHAPE[n.kind], 0, i);
        // Haze: a few large, faint motes; where memories gather they add up to a nebula.
        if (i % hazeEvery === 0) {
          const k = Math.sqrt(hazeEvery); // fewer motes, each a little stronger
          push(n.x + gauss(r) * ext * 0.012, n.y + gauss(r) * ext * 0.012, 24 + r() * 34, ((dormant ? 0.004 : 0.012) + r() * 0.012) * k,
            r(), SHAPE.dust, ext * 0.002, i);
        }
        // Dust: a fine cloud around each memory; stronger, more-recalled memories are denser.
        const count = Math.round((10 + n.size * 2.4) * dustScale);
        const spread = ext * (0.005 + 0.0016 * n.size);
        for (let k = 0; k < count; k++) {
          const g = Math.abs(gauss(r));
          push(n.x + gauss(r) * spread * (0.4 + g), n.y + gauss(r) * spread * (0.4 + g), 0.7 + r() * 1.5,
            (dormant ? 0.05 : 0.08) + r() * 0.28, r(), SHAPE.dust, ext * (0.0006 + r() * 0.002), i);
        }
      });
      // Ambient dust follows where memories are dense (nebulae), plus a faint general field.
      const r = rng(7);
      const members = this.nodes.filter((n) => n.kind !== "concept");
      const ambient = Math.min(9000, 2500 + members.length * 10);
      for (let k = 0; k < ambient; k++) {
        const near = members.length && r() < 0.85;
        const anchor = near ? members[Math.floor(r() * members.length)] : { x: this.bounds.cx, y: this.bounds.cy };
        const s = near ? ext * (0.012 + r() * r() * 0.07) : ext * 0.5;
        push(anchor.x + gauss(r) * s, anchor.y + gauss(r) * s, 0.6 + r() * 1.1, 0.03 + r() * 0.12, r(), SHAPE.dust,
          ext * (0.001 + r() * 0.003), -1);
      }
      this.count = owner.length;
      this.owner = Int32Array.from(owner);
      this.state = new Float32Array(this.count).fill(1);
      const gl = this.gl;
      gl.bindBuffer(gl.ARRAY_BUFFER, this.buf.points);
      gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(data), gl.STATIC_DRAW);
      gl.bindBuffer(gl.ARRAY_BUFFER, this.buf.state);
      gl.bufferData(gl.ARRAY_BUFFER, this.state, gl.DYNAMIC_DRAW);
    }

    _buildLines() {
      const v = [];
      for (const e of this.edges) {
        const a = this.nodes[this.index.get(e.s)], b = this.nodes[this.index.get(e.t)];
        if (!a || !b) continue;
        const alpha = 0.012 + 0.07 * e.w * e.w; // hairlines: only strong links read
        v.push(a.x, a.y, alpha, b.x, b.y, alpha);
      }
      this.lineCount = v.length / 3;
      const gl = this.gl;
      gl.bindBuffer(gl.ARRAY_BUFFER, this.buf.lines);
      gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(v), gl.STATIC_DRAW);
      this.focusCount = 0;
    }

    /* emphasis: Map id -> 0 (dim), 1 (normal), 2 (bright); focusEdges: [{s, t}] drawn darker. */
    setEmphasis(emphasis, focusEdges = []) {
      this.focusMode = emphasis !== null;
      for (let i = 0; i < this.count; i++) {
        const o = this.owner[i];
        this.state[i] = !this.focusMode ? 1 : o < 0 ? 0 : (emphasis.get(this.nodes[o].id) ?? 0);
      }
      const gl = this.gl;
      gl.bindBuffer(gl.ARRAY_BUFFER, this.buf.state);
      gl.bufferSubData(gl.ARRAY_BUFFER, 0, this.state);
      const v = [];
      for (const e of focusEdges) {
        const a = this.nodes[this.index.get(e.s)], b = this.nodes[this.index.get(e.t)];
        if (a && b) v.push(a.x, a.y, 0.28 + 0.5 * (e.w ?? 0.5), b.x, b.y, 0.28 + 0.5 * (e.w ?? 0.5));
      }
      this.focusCount = v.length / 3;
      gl.bindBuffer(gl.ARRAY_BUFFER, this.buf.focus);
      gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(v), gl.DYNAMIC_DRAW);
      this.draw();
    }

    // ---- camera --------------------------------------------------------------------

    resize() {
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      const w = this.container.clientWidth, h = this.container.clientHeight;
      this.dpr = dpr;
      this.w = w; this.h = h;
      this.canvas.width = Math.max(1, Math.round(w * dpr));
      this.canvas.height = Math.max(1, Math.round(h * dpr));
      this.canvas.style.width = `${w}px`;
      this.canvas.style.height = `${h}px`;
      if (this.bounds) this.base = Math.min(w, h) * 0.82 / this.bounds.extent;
      this.draw();
      this._changed();
    }

    fit(animate = true) {
      if (!this.bounds) return;
      this.base = Math.min(this.w, this.h) * 0.82 / this.bounds.extent;
      this._animateTo({ x: this.bounds.cx, y: this.bounds.cy, zoom: 1 }, animate ? 700 : 0);
    }

    focusOn(id, zoom = null) {
      const n = this.nodes[this.index.get(id)];
      if (!n) return;
      this._animateTo({ x: n.x, y: n.y, zoom: zoom ?? Math.max(this.camera.zoom, 1.6) }, 800);
    }

    project(id) {
      const n = this.nodes[this.index.get(id)];
      if (!n) return null;
      const s = this.base * this.camera.zoom;
      return { x: (n.x - this.camera.x) * s + this.w / 2, y: (n.y - this.camera.y) * s + this.h / 2 };
    }

    pick(sx, sy, radius = 12) {
      const s = this.base * this.camera.zoom;
      let best = null, bestD = radius * radius;
      for (const n of this.nodes) {
        const dx = (n.x - this.camera.x) * s + this.w / 2 - sx, dy = (n.y - this.camera.y) * s + this.h / 2 - sy;
        const d = dx * dx + dy * dy;
        if (d < bestD) { bestD = d; best = n.id; }
      }
      return best;
    }

    _animateTo(target, ms) {
      const from = { ...this.camera };
      if (!ms) { Object.assign(this.camera, target); this.draw(); this._changed(); return; }
      const t0 = performance.now();
      this.tween = (now) => {
        const k = Math.min(1, (now - t0) / ms);
        const e = 1 - Math.pow(1 - k, 3);
        this.camera.x = from.x + (target.x - from.x) * e;
        this.camera.y = from.y + (target.y - from.y) * e;
        this.camera.zoom = from.zoom * Math.pow(target.zoom / from.zoom, e);
        this._changed();
        if (k >= 1) this.tween = null;
      };
    }

    _changed() { if (this.opts.onView) this.opts.onView(); }

    _bind() {
      const c = this.canvas;
      let drag = null;
      c.addEventListener("wheel", (e) => {
        e.preventDefault();
        const rect = c.getBoundingClientRect();
        const sx = e.clientX - rect.left, sy = e.clientY - rect.top;
        const s = this.base * this.camera.zoom;
        const wx = (sx - this.w / 2) / s + this.camera.x, wy = (sy - this.h / 2) / s + this.camera.y;
        const zoom = Math.min(40, Math.max(0.3, this.camera.zoom * Math.exp(-e.deltaY * 0.0015)));
        const s2 = this.base * zoom;
        this.camera.zoom = zoom;
        this.camera.x = wx - (sx - this.w / 2) / s2;
        this.camera.y = wy - (sy - this.h / 2) / s2;
        this.tween = null;
        this.draw();
        this._changed();
      }, { passive: false });
      c.addEventListener("pointerdown", (e) => {
        drag = { x: e.clientX, y: e.clientY, cx: this.camera.x, cy: this.camera.y, moved: false };
        c.setPointerCapture(e.pointerId);
      });
      c.addEventListener("pointermove", (e) => {
        const rect = c.getBoundingClientRect();
        if (drag) {
          const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
          if (Math.abs(dx) + Math.abs(dy) > 3) drag.moved = true;
          const s = this.base * this.camera.zoom;
          this.camera.x = drag.cx - dx / s;
          this.camera.y = drag.cy - dy / s;
          this.tween = null;
          this.draw();
          this._changed();
          return;
        }
        const id = this.pick(e.clientX - rect.left, e.clientY - rect.top);
        c.style.cursor = id ? "pointer" : "grab";
        if (id !== this.hovered) { this.hovered = id; if (this.opts.onHover) this.opts.onHover(id); }
      });
      c.addEventListener("pointerup", (e) => {
        const rect = c.getBoundingClientRect();
        if (drag && !drag.moved && this.opts.onSelect) this.opts.onSelect(this.pick(e.clientX - rect.left, e.clientY - rect.top));
        drag = null;
      });
      c.addEventListener("pointerleave", () => { if (this.hovered) { this.hovered = null; if (this.opts.onHover) this.opts.onHover(null); } });
      c.addEventListener("dblclick", () => this.fit());
      c.addEventListener("pointermove", () => { this.dirty = true; }, { passive: true });
    }

    // ---- drawing ---------------------------------------------------------------------

    _loop(now) {
      // If the machine cannot keep the drift smooth (e.g. no GPU), stop drifting and draw on demand.
      if (this.motion && this.last) {
        const dt = now - this.last;
        this.frameEma = this.frameEma ? this.frameEma * 0.9 + dt * 0.1 : dt;
        this.frames = (this.frames || 0) + 1;
        if (this.frames > 40 && this.frameEma > 45) { this.motion = false; this.stilled = true; }
      }
      this.last = now;
      if (this.tween) this.tween(now);
      if (this.motion || this.tween || this.dirty) this.draw(now);
      this.raf = requestAnimationFrame(this._loop);
    }

    draw(now = performance.now()) {
      this.dirty = false;
      const gl = this.gl;
      if (!this.count) { gl.viewport(0, 0, this.canvas.width, this.canvas.height); gl.clearColor(0, 0, 0, 0); gl.clear(gl.COLOR_BUFFER_BIT); return; }
      gl.viewport(0, 0, this.canvas.width, this.canvas.height);
      gl.clearColor(0, 0, 0, 0);
      gl.clear(gl.COLOR_BUFFER_BIT);
      gl.enable(gl.BLEND);
      gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
      const scale = this.base * this.camera.zoom * this.dpr;
      const vp = [this.canvas.width, this.canvas.height];

      // hairlines
      const L = this.lines;
      gl.useProgram(L.prog);
      gl.uniform2f(L.loc.u_center, this.camera.x, this.camera.y);
      gl.uniform2f(L.loc.u_viewport, vp[0], vp[1]);
      gl.uniform1f(L.loc.u_scale, scale);
      gl.uniform3fv(L.loc.u_ink, this.ink);
      const drawLines = (buffer, count, mul) => {
        if (!count) return;
        gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
        gl.enableVertexAttribArray(L.loc.a_pos);
        gl.vertexAttribPointer(L.loc.a_pos, 2, gl.FLOAT, false, 12, 0);
        gl.enableVertexAttribArray(L.loc.a_alpha);
        gl.vertexAttribPointer(L.loc.a_alpha, 1, gl.FLOAT, false, 12, 8);
        gl.uniform1f(L.loc.u_mul, mul);
        gl.drawArrays(gl.LINES, 0, count);
      };
      drawLines(this.buf.lines, this.lineCount, this.focusMode ? 0.25 : 1);
      drawLines(this.buf.focus, this.focusCount, 1);

      // particles
      const P = this.points;
      gl.useProgram(P.prog);
      gl.uniform2f(P.loc.u_center, this.camera.x, this.camera.y);
      gl.uniform2f(P.loc.u_viewport, vp[0], vp[1]);
      gl.uniform1f(P.loc.u_scale, scale);
      gl.uniform1f(P.loc.u_zoom, this.camera.zoom);
      gl.uniform1f(P.loc.u_time, (now - this.start) / 1000);
      gl.uniform1f(P.loc.u_dpr, this.dpr);
      gl.uniform1f(P.loc.u_motion, this.motion ? 1 : 0);
      gl.uniform1f(P.loc.u_focus, this.focusMode ? 1 : 0);
      gl.uniform3fv(P.loc.u_ink, this.ink);
      gl.bindBuffer(gl.ARRAY_BUFFER, this.buf.points);
      const stride = 7 * 4;
      [["a_pos", 2, 0], ["a_size", 1, 8], ["a_alpha", 1, 12], ["a_phase", 1, 16], ["a_shape", 1, 20], ["a_drift", 1, 24]]
        .forEach(([name, size, off]) => {
          gl.enableVertexAttribArray(P.loc[name]);
          gl.vertexAttribPointer(P.loc[name], size, gl.FLOAT, false, stride, off);
        });
      gl.bindBuffer(gl.ARRAY_BUFFER, this.buf.state);
      gl.enableVertexAttribArray(P.loc.a_state);
      gl.vertexAttribPointer(P.loc.a_state, 1, gl.FLOAT, false, 4, 0);
      gl.drawArrays(gl.POINTS, 0, this.count);
    }

    destroy() {
      cancelAnimationFrame(this.raf);
      this.resizeObserver.disconnect();
      this.canvas.remove();
    }
  }

  window.Galaxy = Galaxy;
})();
