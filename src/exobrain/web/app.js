/* exobrain screen. All user/AI text is inserted with textContent (never innerHTML). */
"use strict";

const TOKEN = document.body.dataset.token;
const KIND_JA = { episode: "出来事", semantic: "知識", procedural: "ルール", concept: "概念" };
const KIND_EN = { episode: "EPISODE", semantic: "KNOWLEDGE", procedural: "RULE", concept: "CONCEPT" };
const SOURCE_KIND = { memo: "YOUR NOTE", daily_report: "AI REPORT", dream: "DREAM JOURNAL" };
const $ = (sel) => document.querySelector(sel);

// ---- helpers --------------------------------------------------------------------

async function api(path, body) {
  const opts = { headers: { "X-Exobrain-Token": TOKEN } };
  if (body !== undefined) {
    opts.method = "POST";
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(path, opts);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `エラー (${res.status})`);
  return data;
}

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k === "on") for (const [ev, fn] of Object.entries(v)) node.addEventListener(ev, fn);
    else if (v !== undefined && v !== null && v !== false) node.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) if (c !== null && c !== undefined) node.append(c);
  return node;
}

let toastTimer;
function toast(message, isError = false) {
  const t = $("#toast");
  t.textContent = message;
  t.className = "toast" + (isError ? " error" : "");
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (t.hidden = true), isError ? 8000 : 4000);
}

async function guarded(fn) {
  try {
    return await fn();
  } catch (e) {
    toast(e.message, true);
  }
}

const pad = (n, w = 3) => String(n).padStart(w, "0");
function stamp(iso, withTime = true) {
  if (!iso) return "—";
  const d = new Date(iso);
  const day = `${d.getFullYear()}.${pad(d.getMonth() + 1, 2)}.${pad(d.getDate(), 2)}`;
  return withTime ? `${day} ${pad(d.getHours(), 2)}:${pad(d.getMinutes(), 2)}` : day;
}
const who = (createdBy) => (!createdBy ? "—" : createdBy.startsWith("ai:") ? createdBy.slice(3)
  : createdBy === "sleep" ? "SLEEP" : createdBy === "human" ? "YOU" : createdBy);
const cssVar = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const label = (text, cls = "label") => el("span", { class: cls }, text);

// Colors are read once per theme, not once per node per frame (that made 5,000-node graphs sluggish).
let C = {};
function readColors() {
  C = Object.fromEntries(["--concept", "--series-episode", "--series-semantic", "--series-procedural", "--edge",
    "--edge-focus", "--dim", "--muted", "--ink", "--ink-2", "--paper", "--f-sans", "--f-mono"].map((v) => [v, cssVar(v)]));
}
readColors();

function kindLine(kind) {
  return el("div", { class: "kindline" }, el("span", { class: `dot dot-${kind}` }),
    label(`(${KIND_EN[kind] || kind}) ${KIND_JA[kind] || ""}`));
}

async function openSource(id) {
  await guarded(async () => {
    const s = await api(`/api/source/${encodeURIComponent(id)}`);
    $("#dialog-body").replaceChildren(renderSource(s));
    $("#reader-dialog").showModal();
  });
}

function renderSource(s) {
  const chars = (s.body || "").length;
  const frag = document.createDocumentFragment();
  frag.append(label(`(${SOURCE_KIND[s.kind] || s.kind})`));
  frag.append(el("h2", {}, s.title));
  frag.append(el("div", { class: "meta-line" },
    label(`FROM // ${s.ai_name || (s.author === "human" ? "YOU" : "—")}`),
    label(`FILED // ${stamp(s.created_at)}`),
    label(`${chars.toLocaleString()} CHARACTERS`),
    s.intact === false ? label("(MODIFIED) 原文が変更されています", "label bad") : null));
  frag.append(el("pre", {}, s.body || "（消去済み）"));
  frag.append(el("button", { class: "link label", on: { click: () => addToErase("sources", s.id) } }, "(ERASE) 消去の候補に入れる"));
  return frag;
}

function addToErase(kind, id) {
  const box = kind === "sources" ? $("#erase-sources") : $("#erase-nodes");
  const ids = new Set(box.value.split(/\s+/).filter(Boolean));
  ids.add(id);
  box.value = [...ids].join("\n");
  if ($("#reader-dialog").open) $("#reader-dialog").close();
  showTab("safety");
  toast("安全装置の「消去」に追加しました。内容を確認してから消去してください。");
}

async function loadMeta() {
  await guarded(async () => {
    const [s, z] = await Promise.all([api("/api/safety"), api("/api/sleep")]);
    const st = s.stats;
    $("#m-nodes").textContent = pad(st.episode + st.semantic + st.procedural);
    $("#m-edges").textContent = pad(st.edges);
    $("#m-sources").textContent = pad(st.sources);
    $("#m-sleep").textContent = z.last_sleep ? stamp(z.last_sleep).slice(5) : "—";
    $("#paused-badge").hidden = !s.paused;
  });
}

// ---- tabs -------------------------------------------------------------------------

const loaders = {};
function showTab(name) {
  document.querySelectorAll(".tabs button").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.tab === name)));
  document.querySelectorAll(".tab").forEach((t) => (t.hidden = t.id !== `tab-${name}`));
  if (loaders[name]) loaders[name]();
}
document.querySelectorAll(".tabs button").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));

// ---- graph ------------------------------------------------------------------------

const G = { renderer: null, graph: null, data: null, hovered: null, selected: null, matches: null, neighbors: null };
window.__exobrain = G; // for tests and debugging

function kindColor(kind) {
  return C[kind === "concept" ? "--concept" : `--series-${kind}`];
}

function graphQuery() {
  const kinds = [...document.querySelectorAll(".kinds input:checked")].map((i) => i.value);
  const p = new URLSearchParams({ kinds: kinds.join(",") });
  if ($("#g-days").value) p.set("days", $("#g-days").value);
  if ($("#g-origin").value) p.set("origin", $("#g-origin").value);
  if ($("#g-dormant").checked) p.set("dormant", "1");
  return p.toString();
}

function hashAngle(id) {
  let h = 0;
  for (const ch of id) h = (h * 31 + ch.charCodeAt(0)) | 0;
  return (Math.abs(h) % 3600) / 3600 * Math.PI * 2;
}

// Hover label as a paper tag with an ink rule, instead of sigma's default white box.
function drawHover(ctx, data, settings) {
  if (!data.label) return;
  const size = settings.labelSize;
  ctx.font = `${settings.labelWeight} ${size}px ${settings.labelFont}`;
  const w = ctx.measureText(data.label).width;
  const x = data.x + data.size + 8;
  ctx.fillStyle = C["--paper"];
  ctx.strokeStyle = C["--ink"];
  ctx.lineWidth = 1;
  ctx.fillRect(x - 6, data.y - size / 2 - 6, w + 12, size + 12);
  ctx.strokeRect(x - 6, data.y - size / 2 - 6, w + 12, size + 12);
  ctx.beginPath();
  ctx.arc(data.x, data.y, data.size + 3.5, 0, Math.PI * 2);
  ctx.stroke();
  ctx.fillStyle = C["--ink"];
  ctx.fillText(data.label, x, data.y + size / 3);
}

async function loadGraph() {
  await guarded(async () => {
    const data = await api(`/api/graph?${graphQuery()}`);
    G.data = data;
    updateOrigins(data.origins);
    $("#g-changed").disabled = !data.has_changes;
    const elements = data.nodes.filter((n) => n.kind !== "concept");
    $("#graph-empty").hidden = elements.length > 0;
    $("#graph-stats").dataset.base = `MEMORIES // ${pad(elements.length)}   CONCEPTS // ${pad(data.nodes.length - elements.length)}   LINKS // ${pad(data.edges.length)}`
      + (data.truncated ? "   (TRUNCATED) よく使われる記憶から表示" : "");
    showStats();
    renderTable(elements);
    const graph = new graphology.Graph({ type: "undirected", multi: false });
    data.nodes.forEach((n, i) => {
      const a = hashAngle(n.id), r = 10 + (i % 97);
      graph.addNode(n.id, {
        x: Math.cos(a) * r, y: Math.sin(a) * r, size: n.kind === "concept" ? Math.max(2, n.size * 0.7) : n.size,
        label: n.label, color: kindColor(n.kind), kind: n.kind, status: n.status, changed: n.changed, pinned: n.pinned,
      });
    });
    for (const e of data.edges) {
      if (e.s === e.t || graph.hasEdge(e.s, e.t)) continue;
      graph.addEdge(e.s, e.t, { size: 0.35 + 2.6 * e.w, w: e.w, color: C["--edge"] });
    }
    $("#graph-busy").hidden = false;
    await new Promise((r) => setTimeout(r, 20)); // let the message paint
    if (graph.order > 1) {
      const settings = graphologyLibrary.layoutForceAtlas2.inferSettings(graph);
      const iterations = graph.order < 800 ? 300 : graph.order < 3000 ? 120 : 60;
      graphologyLibrary.layoutForceAtlas2.assign(graph, { iterations, settings: { ...settings, barnesHutOptimize: graph.order > 500 } });
    }
    $("#graph-busy").hidden = true;
    G.graph = graph;
    if (G.renderer) G.renderer.kill();
    G.renderer = new Sigma(graph, $("#graph"), {
      renderEdgeLabels: false,
      labelRenderedSizeThreshold: graph.order < 300 ? 0 : 6, // few memories: label everything, like Obsidian
      labelColor: { color: C["--ink-2"] },
      labelFont: C["--f-sans"],
      labelWeight: "500",
      labelSize: 11,
      zIndex: true,
      defaultDrawNodeHover: drawHover,
      nodeReducer,
      edgeReducer,
    });
    G.renderer.on("enterNode", ({ node }) => { G.hovered = node; refreshHighlight(); });
    G.renderer.on("leaveNode", () => { G.hovered = null; refreshHighlight(); });
    G.renderer.on("clickNode", ({ node }) => selectNode(node));
    G.renderer.on("clickStage", () => { G.selected = null; refreshHighlight(); });
    applySearch();
  });
}

function refreshHighlight() {
  G.onlyChanged = $("#g-changed").checked;
  const focus = G.hovered || G.selected;
  G.neighbors = focus && G.graph && G.graph.hasNode(focus) ? new Set(G.graph.neighbors(focus)) : null;
  if (G.renderer) G.renderer.refresh({ skipIndexation: true });
}

function nodeReducer(node, attrs) {
  const res = { ...attrs };
  const focus = G.hovered || G.selected;
  const dimmed = (focus && node !== focus && !(G.neighbors && G.neighbors.has(node)))
    || (G.matches && !G.matches.has(node))
    || (G.onlyChanged && !attrs.changed);
  if (attrs.status === "dormant") res.color = C["--muted"];
  if (dimmed) {
    res.color = C["--dim"];
    res.label = "";
    res.zIndex = 0;
  } else {
    res.zIndex = 1;
    if (node === focus || (G.matches && G.matches.has(node))) res.highlighted = true;
  }
  if (node === G.selected) { res.highlighted = true; res.zIndex = 2; }
  return res;
}

function edgeReducer(edge, attrs) {
  const res = { ...attrs };
  const focus = G.hovered || G.selected;
  if (focus) {
    const [s, t] = G.graph.extremities(edge);
    if (s === focus || t === focus) { res.color = C["--edge-focus"]; res.zIndex = 1; }
    else res.hidden = true;
  } else if (G.matches || G.onlyChanged) {
    res.color = C["--dim"];
  }
  return res;
}

function applySearch() {
  const q = $("#g-search").value.trim().toLowerCase();
  G.matches = null;
  G.selected = null; // a search replaces the current selection
  if (q && G.graph) {
    G.matches = new Set();
    G.graph.forEachNode((n, a) => { if ((a.label || "").toLowerCase().includes(q)) G.matches.add(n); });
  }
  refreshHighlight();
  $("#graph-stats").dataset.search = G.matches ? `(SEARCH) 「${q}」 MATCHES // ${pad(G.matches.size)} — ENTER で移動` : "";
  showStats();
}

function showStats() {
  const f = $("#graph-stats");
  f.textContent = [f.dataset.base, f.dataset.search].filter(Boolean).join("      ");
}

function focusNode(node, select = true) {
  if (!G.renderer || !G.graph.hasNode(node)) return;
  const d = G.renderer.getNodeDisplayData(node);
  if (d) G.renderer.getCamera().animate({ x: d.x, y: d.y }, { duration: 500 }); // keep the zoom: neighbors stay in view
  if (select) selectNode(node);
}

async function selectNode(node) {
  G.selected = node;
  refreshHighlight();
  await guarded(async () => {
    const d = await api(`/api/node/${encodeURIComponent(node)}`);
    renderDetail(d);
  });
}

function renderDetail({ node, neighbors, source, shelves }) {
  const parts = [
    el("div", { class: "no" }, el("small", {}, "NO."), pad(node.no, 4)),
    kindLine(node.kind),
  ];
  if (node.pinned) parts.push(label("(PINNED) 毎回必ず思い出すルール", "label pin"));
  if (node.status !== "active") parts.push(label(node.status === "dormant" ? "(DORMANT) 眠っている記憶" : "(SUPERSEDED) 置き換え済み"));
  parts.push(el("p", { class: "quote" }, node.body || node.label));
  if (node.kind !== "concept") {
    const kv = [["FROM", who(node.created_by)], ["LEARNED", stamp(node.created_at)], ["RECALLED", pad(node.access_count, 2)],
      ["LAST RECALL", stamp(node.last_activated_at)], ["STRENGTH", node.base_strength.toFixed(1)],
      ["IMPORTANCE", node.importance.toFixed(1)], ["CORRECTED", pad(node.corrections, 2)]];
    parts.push(el("dl", { class: "kv" }, kv.flatMap(([k, v]) => [el("dt", {}, k), el("dd", {}, v)])));
  }
  if (source) {
    parts.push(el("div", { class: "section-title" }, label("(SOURCE) もとになった原文"), label(stamp(source.created_at, false))));
    parts.push(el("button", { class: "link source-link", on: { click: () => openSource(source.id) } }, source.title));
    if (shelves.length) parts.push(label(`SHELVES // ${shelves.join(" / ")}`));
  }
  parts.push(el("div", { class: "section-title" }, label("(LINKS) つながり"), label(pad(neighbors.length, 2))));
  const maxW = Math.max(0.01, ...neighbors.map((n) => n.w));
  for (const n of neighbors) {
    const bar = el("span", { class: "wbar" });
    bar.style.width = `${Math.round(6 + 38 * (n.w / maxW))}px`;
    parts.push(el("div", { class: "nbr", on: { click: () => focusNode(n.id) } },
      el("span", { class: `dot dot-${n.kind}` }), bar, el("span", { class: "t" }, n.label)));
  }
  if (node.kind !== "concept") {
    parts.push(el("p", {}, el("button", { class: "link label", on: { click: () => addToErase("nodes", node.id) } }, "(ERASE) この記憶を消去の候補に入れる")));
  }
  $("#detail").replaceChildren(...parts);
  $("#detail").scrollTop = 0;
}

function renderTable(elements) {
  const body = $("#graph-table tbody");
  body.replaceChildren(...elements.map((n) => el("tr", { on: { click: () => { $("#g-table").checked = false; toggleTable(); focusNode(n.id); } } },
    el("td", { class: "no" }, pad(n.no, 4)),
    el("td", {}, el("span", { class: "kindline" }, el("span", { class: `dot dot-${n.kind}` }), label(KIND_EN[n.kind]))),
    el("td", {}, n.label), el("td", { class: "date" }, who(n.created_by)), el("td", { class: "date" }, stamp(n.created_at, false)))));
}

function updateOrigins(origins) {
  const sel = $("#g-origin");
  const current = sel.value;
  [...sel.querySelectorAll("option[data-ai]")].forEach((o) => o.remove());
  for (const o of origins) sel.append(el("option", { value: o, "data-ai": "1" }, o));
  sel.value = current;
}

function toggleTable() {
  const on = $("#g-table").checked;
  $("#graph-table").hidden = !on;
  $("#graph").hidden = on;
}

$("#g-search").addEventListener("input", applySearch);
$("#g-search").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && G.matches && G.matches.size) focusNode([...G.matches][0]);
});
document.querySelectorAll(".kinds input, #g-days, #g-origin, #g-dormant").forEach((i) => i.addEventListener("change", loadGraph));
$("#g-changed").addEventListener("change", refreshHighlight);
$("#g-table").addEventListener("change", toggleTable);
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => { readColors(); loadGraph(); });
loaders.graph = () => { if (!G.graph) loadGraph(); };

// ---- bookshelf ----------------------------------------------------------------------

const S = { data: null };

async function loadShelves() {
  await guarded(async () => {
    S.data = await api("/api/shelves");
    const { sources, topics } = S.data;
    const byId = Object.fromEntries(sources.map((s) => [s.id, s]));
    const item = (name, list) => el("li", { on: { click: (e) => { markActive(e.currentTarget); showList(name, list); } } },
      el("span", {}, name), el("span", { class: "c" }, pad(list.length, 2)));
    $("#s-topics").replaceChildren(...Object.entries(topics).map(([name, ids]) => item(name, ids.map((i) => byId[i]).filter(Boolean))));
    if (!Object.keys(topics).length) $("#s-topics").append(el("li", { class: "hint" }, "睡眠で話題ごとに並びます"));
    const months = {};
    const origins = {};
    for (const s of sources) {
      (months[s.created_at.slice(0, 7).replace("-", ".")] ||= []).push(s);
      const o = s.kind === "memo" ? "あなたのメモ" : s.kind === "dream" ? "夢日記" : (s.ai_name || "不明");
      (origins[o] ||= []).push(s);
    }
    $("#s-months").replaceChildren(...Object.entries(months).map(([m, l]) => item(m, l)));
    $("#s-origins").replaceChildren(...Object.entries(origins).sort().map(([o, l]) => item(o, l)));
    showList("ALL ORIGINALS — すべての原文", sources);
  });
}

function markActive(li) {
  document.querySelectorAll(".shelf-nav li.active").forEach((x) => x.classList.remove("active"));
  li.classList.add("active");
}

function indexItem(i, s, extra = null) {
  return el("li", { class: "clickable", on: { click: () => readSource(s.source_id || s.id) } },
    el("span", { class: "n" }, `NO. ${pad(i + 1)}`),
    el("span", { class: "title" }, s.title),
    el("span", { class: "meta" }, `${SOURCE_KIND[s.kind] || s.kind} // ${s.ai_name || (s.author === "human" ? "YOU" : "—")} // ${stamp(s.created_at, false)}`),
    extra);
}

function showList(title, list, passages = false) {
  $("#s-list-title").textContent = title;
  $("#s-list").replaceChildren(...list.map((s, i) => indexItem(i, s, passages ? el("span", { class: "passage" }, s.passage) : null)));
  if (!list.length) $("#s-list").append(el("li", { class: "muted" }, "(NO RESULTS) 見つかりませんでした"));
}

async function readSource(id) {
  await guarded(async () => {
    const s = await api(`/api/source/${encodeURIComponent(id)}`);
    $("#s-reader").replaceChildren(renderSource(s));
    $("#s-reader").scrollTop = 0;
  });
}

let searchTimer;
$("#s-search").addEventListener("input", () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(async () => {
    const q = $("#s-search").value.trim();
    if (!q) return showList("ALL ORIGINALS — すべての原文", S.data ? S.data.sources : []);
    await guarded(async () => {
      const r = await api(`/api/search?q=${encodeURIComponent(q)}`);
      showList(`(SEARCH) 「${q}」 — ${pad(r.bookshelf.length, 2)} RESULTS`, r.bookshelf, true);
    });
  }, 250);
});
loaders.shelf = loadShelves;

// ---- memo -------------------------------------------------------------------------

$("#memo-text").addEventListener("input", () => { $("#memo-count").textContent = $("#memo-text").value.length.toLocaleString(); });
$("#memo-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  await guarded(async () => {
    const r = await api("/api/memo", { title: $("#memo-title").value, text: $("#memo-text").value });
    if (r.duplicate) return toast("同じメモがすでに本棚にあります。");
    $("#memo-title").value = "";
    $("#memo-text").value = "";
    $("#memo-count").textContent = "0";
    G.graph = null; // refresh next time
    loadMeta();
    toast(`「${r.title}」を本棚に保管しました。次の睡眠で記憶に分解されます。`);
  });
});

// ---- sleep -------------------------------------------------------------------------

let sleepPoll;
async function loadSleep() {
  await guarded(async () => {
    const s = await api("/api/sleep");
    const status = $("#sleep-status");
    if (s.running) status.replaceChildren(label("(ASLEEP) 睡眠中"), `Dreaming since ${stamp(s.started_at).slice(11)}…`);
    else if (s.error) status.replaceChildren(label("(ERROR)", "label bad"), s.error);
    else status.replaceChildren(label(s.due ? "(DUE) そろそろ眠る時間です" : "(AWAKE) 前回の睡眠"), s.last_sleep ? stamp(s.last_sleep) : "Never slept.");
    if (s.result && s.result.note) status.append(el("span", { class: "hint", style: null }, s.result.note));
    $("#sleep-ai").disabled = s.running || !s.claude_found;
    $("#sleep-noai").disabled = s.running;
    $("#sleep-claude").textContent = s.claude_found ? "AI による整理には Claude Code（Claude Pro の利用枠）を使います。"
      : "Claude Code が見つからないため、AI による整理はできません（AI なしの整理は使えます）。";
    const hhmm = s.timer ? `${pad(s.timer.hour, 2)}:${pad(s.timer.minute, 2)}` : null;
    $("#timer-status").textContent = hhmm ? `(ON) EVERY DAY // ${hhmm}` : "(OFF) タイマーは切れています";
    if (hhmm) $("#timer-time").value = hhmm;
    $("#dreams").replaceChildren(...s.dreams.map((d, i) => el("li", { class: "clickable", on: { click: () => openSource(d.id) } },
      el("span", { class: "n" }, `NO. ${pad(s.dreams.length - i)}`), el("span", { class: "title" }, d.title),
      el("span", { class: "meta" }, `DREAM JOURNAL // ${stamp(d.created_at)}`))));
    if (!s.dreams.length) $("#dreams").append(el("li", { class: "muted" }, "(EMPTY) まだ夢日記はありません"));
    clearTimeout(sleepPoll);
    if (s.running) sleepPoll = setTimeout(loadSleep, 3000);
    else if (s.result) { G.graph = null; loadMeta(); }
  });
}

async function goToSleep(useAi) {
  await guarded(async () => {
    await api("/api/sleep", { use_ai: useAi });
    toast("眠りにつきました。");
    loadSleep();
  });
}
$("#sleep-ai").addEventListener("click", () => goToSleep(true));
$("#sleep-noai").addEventListener("click", () => goToSleep(false));
$("#timer-set").addEventListener("click", async () => {
  const [h, m] = ($("#timer-time").value || "").split(":").map(Number);
  if (Number.isNaN(h)) return toast("時刻を選んでください。", true);
  await guarded(async () => {
    const r = await api("/api/sleep/timer", { hour: h, minute: m || 0 });
    toast(r.applied ? "タイマーを設定しました。" : "タイマーを保存しました（Mac への反映は exobrain install で行います）。");
    loadSleep();
  });
});
$("#timer-off").addEventListener("click", async () => {
  await guarded(async () => { await api("/api/sleep/timer", { off: true }); toast("タイマーを切りました。"); loadSleep(); });
});
loaders.sleep = loadSleep;

// ---- safeguards --------------------------------------------------------------------

async function loadSafety() {
  await guarded(async () => {
    const s = await api("/api/safety");
    $("#pause").checked = s.paused;
    $("#paused-badge").hidden = !s.paused;
    $("#confirm-phrase").textContent = s.confirm_phrase;
    $("#backups").replaceChildren(...s.backups.map((name, i) => el("li", {},
      el("span", { class: "n" }, `NO. ${pad(i + 1)}`),
      el("span", { class: "row" }, el("span", { class: "label" }, name),
        el("button", { class: "link label", on: { click: () => restore(name) } }, "(RESTORE) この時点に戻す")))));
    if (!s.backups.length) $("#backups").append(el("li", { class: "muted" }, "(EMPTY) まだバックアップはありません"));
  });
}

async function restore(name) {
  if (!confirm(`${name} の時点に脳を戻します。いまの脳は控えとして残します。よろしいですか？`)) return;
  await guarded(async () => {
    const r = await api("/api/restore", { name });
    toast(r.verified ? "戻しました。改ざんチェックも正常です。" : `戻しましたが、チェックで問題が見つかりました: ${r.message}`, !r.verified);
    G.graph = null;
    loadMeta();
  });
}

$("#pause").addEventListener("change", async () => {
  await guarded(async () => {
    const r = await api("/api/pause", { paused: $("#pause").checked });
    $("#paused-badge").hidden = !r.paused;
    toast(r.paused ? "一時停止しました。" : "一時停止を解除しました。");
  });
});
$("#verify").addEventListener("click", async () => {
  await guarded(async () => {
    const r = await api("/api/verify", {});
    $("#verify-result").className = "label " + (r.ok ? "ok" : "bad");
    $("#verify-result").textContent = r.ok ? "(INTACT) 改ざんはありません" : `(ALERT) ${r.message}`;
  });
});
$("#backup").addEventListener("click", async () => {
  await guarded(async () => { const r = await api("/api/backup", {}); toast(`${r.backup} を作りました。`); loadSafety(); });
});

let planToken = null;
$("#erase-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const ids = (sel) => $(sel).value.split(/\s+/).filter(Boolean);
  await guarded(async () => {
    const r = await api("/api/erase/plan", {
      source_ids: ids("#erase-sources"), node_ids: ids("#erase-nodes"),
      since: $("#erase-since").value || null, until: $("#erase-until").value || null,
    });
    planToken = r.plan_token;
    const rows = [
      ...r.sources.map((s, i) => el("li", {}, el("span", { class: "n" }, `SRC. ${pad(i + 1)}`), el("span", { class: "title" }, s.title),
        el("span", { class: "meta" }, `ORIGINAL // ${stamp(s.created_at, false)}`))),
      ...r.nodes.map((n, i) => el("li", {}, el("span", { class: "n" }, `MEM. ${pad(i + 1)}`), el("span", {}, n.label),
        el("span", { class: "meta" }, KIND_EN[n.kind] || n.kind))),
    ];
    $("#erase-plan-list").replaceChildren(...(rows.length ? rows : [el("li", { class: "muted" }, "(NONE) 該当するものはありません")]));
    $("#erase-plan").hidden = false;
    $("#erase-phrase").value = "";
    $("#erase-result").textContent = "";
  });
});
$("#erase-confirm").addEventListener("submit", async (e) => {
  e.preventDefault();
  await guarded(async () => {
    const r = await api("/api/erase", { plan_token: planToken, confirm: $("#erase-phrase").value });
    $("#erase-plan").hidden = true;
    $("#erase-sources").value = "";
    $("#erase-nodes").value = "";
    $("#erase-result").textContent = `原文 ${r.erased_sources} 件、記憶 ${r.erased_nodes} 件を消去しました。${r.notice}`;
    G.graph = null;
    loadSafety();
    loadMeta();
  });
});
loaders.safety = loadSafety;

// ---- start ---------------------------------------------------------------------------

loadMeta();
showTab("graph");
