/* exobrain screen. All user/AI text is inserted with textContent (never innerHTML). */
"use strict";

const TOKEN = document.body.dataset.token;
const KIND_JA = { episode: "出来事", semantic: "知識", procedural: "ルール", concept: "概念", case: "事例" };
const KIND_EN = { episode: "Episode", semantic: "Knowledge", procedural: "Rule", concept: "Concept", case: "Case" };
const STAGE_JA = { tentative: "仮のルール（オーナーの確認待ち）", confirmed: "本決まりのルール" };
const SOURCE_KIND = { memo: "Your note", daily_report: "AI report", dream: "Dream journal", ai_daily: "AI daily log",
  remember_note: "#remember note", explicit: "Remember this", good: "/good" };
const SOURCE_JA = { memo: "メモ", daily_report: "AI の報告（旧）", dream: "夢日記", ai_daily: "AI 日報",
  remember_note: "#remember", explicit: "覚えておいて", good: "/good" };
const PROMOTED_JA = { summarized: "要約", grown: "事例から", explicit: "明示", demand: "前にも言った", repetition: "反復", association: "連想",
  reconsolidation: "書き換えの経緯として" };
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
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
    else if (k === "style") { // CSP forbids style attributes; the CSSOM is allowed
      for (const decl of String(v).split(";")) {
        const i = decl.indexOf(":");
        if (i > 0) node.style.setProperty(decl.slice(0, i).trim(), decl.slice(i + 1).trim());
      }
    }
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
function when(iso, withTime = true) {
  if (!iso) return "—";
  const d = new Date(iso);
  const day = `${d.getDate()} ${MONTHS[d.getMonth()]} ${d.getFullYear()}`;
  return withTime ? `${day}, ${pad(d.getHours(), 2)}:${pad(d.getMinutes(), 2)}` : day;
}
const who = (createdBy) => (!createdBy ? "—" : createdBy.startsWith("ai:") ? createdBy.slice(3)
  : createdBy === "sleep" ? "Sleep" : createdBy === "human" ? "You" : createdBy);
const cap = (text, cls = "cap") => el("span", { class: cls }, text);
const glyph = (kind) => el("i", { class: `glyph glyph-${kind}` });

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
  frag.append(cap(SOURCE_KIND[s.kind] || s.kind));
  frag.append(el("h2", {}, s.title));
  frag.append(el("div", { class: "meta-line" },
    cap(s.ai_name || (s.author === "human" ? "You" : "—")),
    cap(when(s.created_at)),
    cap(`${chars.toLocaleString()} characters`),
    s.intact === false ? cap("Modified — 原文が変更されています", "cap bad") : null));
  frag.append(el("pre", {}, s.body || "（消去済み）"));
  frag.append(el("button", { class: "link cap", on: { click: () => addToErase("sources", s.id) } }, "消去の候補に入れる"));
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
    $("#m-nodes").textContent = pad(st.episode + st.semantic + st.procedural + (st.case || 0));
    $("#m-edges").textContent = pad(st.edges);
    $("#m-sources").textContent = pad(st.sources);
    $("#m-sleep").textContent = z.last_sleep ? when(z.last_sleep) : "—";
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

// ---- graph: the galaxy ------------------------------------------------------------

const G = { galaxy: null, data: null, byId: new Map(), adj: new Map(), hovered: null, selected: null, matches: null, hubs: [] };
window.__exobrain = G; // for tests and debugging

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

/* ForceAtlas2 pulls memories toward their concepts and their strongly learned links, so topics
   gather into nebulae (weak, incidental links are drawn but do not shape the layout: measured, they
   blur the clusters). A gentle twist then turns the layout into spiral arms; it is continuous, so
   neighbors stay neighbors. */
function layout(nodes, edges) {
  const g = new graphology.Graph({ type: "undirected" });
  nodes.forEach((n, i) => {
    const a = hashAngle(n.id), r = 10 + (i % 97);
    g.addNode(n.id, { x: Math.cos(a) * r, y: Math.sin(a) * r });
  });
  for (const e of edges) {
    if (e.s === e.t || g.hasEdge(e.s, e.t) || (e.kind !== "about" && e.w < 0.3)) continue;
    g.addEdge(e.s, e.t, { weight: e.w });
  }
  if (g.order > 1) {
    const inferred = graphologyLibrary.layoutForceAtlas2.inferSettings(g);
    const iterations = g.order < 800 ? 400 : g.order < 3000 ? 160 : 80;
    graphologyLibrary.layoutForceAtlas2.assign(g, {
      iterations, getEdgeWeight: "weight",
      settings: { ...inferred, linLogMode: false, outboundAttractionDistribution: true, gravity: 0.05,
        scalingRatio: 4, barnesHutOptimize: g.order > 500, edgeWeightInfluence: 1 },
    });
  }
  let cx = 0, cy = 0;
  g.forEachNode((_, a) => { cx += a.x; cy += a.y; });
  cx /= Math.max(1, g.order); cy /= Math.max(1, g.order);
  let rmax = 1e-6;
  g.forEachNode((_, a) => { rmax = Math.max(rmax, Math.hypot(a.x - cx, a.y - cy)); });
  const out = new Map();
  g.forEachNode((id, a) => {
    const dx = a.x - cx, dy = a.y - cy, r = Math.hypot(dx, dy);
    const t = 1.1 * (r / rmax);
    out.set(id, { x: dx * Math.cos(t) - dy * Math.sin(t), y: dx * Math.sin(t) + dy * Math.cos(t) });
  });
  return out;
}

function ensureGalaxy() {
  if (G.galaxy) return G.galaxy;
  try {
    G.galaxy = new Galaxy($("#graph"), {
      onHover: (id) => { G.hovered = id; applyEmphasis(); showCaption(); },
      onSelect: (id) => (id ? selectNode(id) : clearSelection()),
      onView: () => { placeLabels(); showCaption(); },
    });
  } catch (e) {
    toast(e.message, true);
  }
  return G.galaxy;
}

async function loadGraph() {
  await guarded(async () => {
    const data = await api(`/api/graph?${graphQuery()}`);
    G.data = data;
    G.byId = new Map(data.nodes.map((n) => [n.id, n]));
    G.adj = new Map(data.nodes.map((n) => [n.id, []]));
    for (const e of data.edges) {
      if (G.adj.has(e.s) && G.adj.has(e.t)) { G.adj.get(e.s).push(e); G.adj.get(e.t).push(e); }
    }
    updateOrigins(data.origins);
    $("#g-changed").disabled = !data.has_changes;
    const elements = data.nodes.filter((n) => n.kind !== "concept");
    const concepts = data.nodes.length - elements.length;
    $("#graph-empty").hidden = elements.length > 0;
    $("#graph-stats").dataset.base = `${pad(elements.length)} memories — ${pad(concepts)} concepts — ${pad(data.edges.length)} links`
      + (data.truncated ? " — 多いため、よく使われる記憶から表示" : "");
    showStats();
    renderTable(elements);
    $("#graph-busy").hidden = false;
    await new Promise((r) => setTimeout(r, 20));
    const pos = layout(data.nodes, data.edges);
    $("#graph-busy").hidden = true;
    const galaxy = ensureGalaxy();
    if (!galaxy) return;
    galaxy.setData(data.nodes.map((n) => ({ ...n, ...pos.get(n.id) })), data.edges);
    // Constellation names: the most connected concepts.
    G.hubs = data.nodes.filter((n) => n.kind === "concept")
      .sort((a, b) => G.adj.get(b.id).length - G.adj.get(a.id).length)
      .slice(0, Math.min(12, Math.ceil(data.nodes.length / 5))).map((n) => n.id);
    G.selected = null;
    $("#detail").hidden = true;
    $("#tab-graph").classList.remove("detail-open");
    applySearch();
  });
}

function applyEmphasis() {
  const galaxy = G.galaxy;
  if (!galaxy) return;
  const focus = G.hovered || G.selected;
  let emphasis = null, edges = [];
  if (focus && G.byId.has(focus)) {
    emphasis = new Map([[focus, 2]]);
    edges = G.adj.get(focus) || [];
    for (const e of edges) emphasis.set(e.s === focus ? e.t : e.s, 1);
  } else if (G.matches) {
    emphasis = new Map([...G.matches].map((id) => [id, 2]));
  } else if ($("#g-changed").checked) {
    emphasis = new Map(G.data.nodes.filter((n) => n.changed).map((n) => [n.id, 2]));
  }
  galaxy.setEmphasis(emphasis, edges);
  buildLabels(focus);
}

/* Labels: constellation names at rest; the focus and its strongest links when something is chosen. */
function buildLabels(focus) {
  const box = $("#g-labels");
  const items = [];
  if (focus && G.byId.has(focus)) {
    const nbrs = [...(G.adj.get(focus) || [])].sort((a, b) => b.w - a.w).slice(0, 12)
      .map((e) => (e.s === focus ? e.t : e.s));
    for (const id of nbrs) items.push([id, "g-label"]);
    if (focus !== G.hovered) items.push([focus, "g-label strong"]);
  } else if (G.matches) {
    for (const id of [...G.matches].slice(0, 24)) items.push([id, "g-label strong"]);
  } else {
    for (const id of [...G.hubs].reverse()) items.push([id, "g-label hub"]);
  }
  box.replaceChildren(...items.map(([id, cls]) => {
    const n = G.byId.get(id);
    const e = el("span", { class: cls }, n ? n.label : id);
    e.dataset.id = id;
    return e;
  }));
  placeLabels();
}

/* Place labels in priority order (strongest first) and hide any that would overlap one already placed. */
function placeLabels() {
  if (!G.galaxy) return;
  const w = $("#graph").clientWidth, h = $("#graph").clientHeight;
  const placed = [];
  for (const e of [...$("#g-labels").children].reverse()) {
    const p = G.galaxy.project(e.dataset.id);
    let visible = p && p.x > -20 && p.y > -20 && p.x < w + 20 && p.y < h + 20;
    if (visible) {
      e.hidden = false;
      e.style.left = `${p.x}px`;
      e.style.top = `${p.y}px`;
      const r = e.getBoundingClientRect();
      const box = { l: r.left - 4, t: r.top - 3, r: r.right + 4, b: r.bottom + 3 };
      visible = !placed.some((o) => box.l < o.r && box.r > o.l && box.t < o.b && box.b > o.t);
      if (visible) placed.push(box);
    }
    e.hidden = !visible;
  }
}

function showCaption() {
  const box = $("#g-caption");
  const id = G.hovered;
  const n = id && G.byId.get(id);
  if (!n || !G.galaxy) { box.hidden = true; return; }
  const p = G.galaxy.project(id);
  box.replaceChildren(
    el("div", { class: "cap" }, n.kind === "concept" ? `Concept — 概念` : `No. ${pad(n.no, 4)} — ${KIND_EN[n.kind]}`),
    el("div", { class: "t" }, n.label));
  box.style.left = `${p.x}px`;
  box.style.top = `${p.y}px`;
  box.hidden = false;
}

function applySearch() {
  const q = $("#g-search").value.trim().toLowerCase();
  G.matches = null;
  if (q && G.data) {
    G.selected = null;
    $("#detail").hidden = true;
    $("#tab-graph").classList.remove("detail-open");
    G.matches = new Set(G.data.nodes.filter((n) => (n.label || "").toLowerCase().includes(q)).map((n) => n.id));
  }
  applyEmphasis();
  $("#graph-stats").dataset.search = G.matches ? `「${q}」 ${pad(G.matches.size)} found — Enter で移動` : "";
  showStats();
}

function showStats() {
  const f = $("#graph-stats");
  f.textContent = [f.dataset.base, f.dataset.search].filter(Boolean).join("        ");
}

function focusNode(id) {
  if (G.galaxy) G.galaxy.focusOn(id);
  selectNode(id);
}

function clearSelection() {
  G.selected = null;
  $("#detail").hidden = true;
  $("#tab-graph").classList.remove("detail-open");
  applyEmphasis();
}

async function selectNode(id) {
  G.selected = id;
  G.matches = null;
  applyEmphasis();
  await guarded(async () => renderDetail(await api(`/api/node/${encodeURIComponent(id)}`)));
}

function renderDetail({ node, neighbors, source, shelves, revisions = [], quotes = [], derived_from = [], strength = null }) {
  const kase = node.case || null;
  const parts = [
    el("button", { class: "close", on: { click: clearSelection } }, "Close ×"),
    el("p", { class: "room" }, node.kind === "concept" ? "Concept" : KIND_EN[node.kind]),
    el("div", { class: "no" }, node.kind === "concept" ? node.label : `No. ${pad(node.no, 4)}`),
  ];
  if (node.kind !== "concept") {
    parts.push(el("p", { class: "body" }, node.body || node.label));
    parts.push(el("p", { class: "credit" },
      el("b", {}, `${KIND_EN[node.kind]} — ${KIND_JA[node.kind]}`), el("br"),
      `${who(node.created_by)}, ${when(node.created_at)}`,
      node.pinned ? [el("br"), "毎回必ず思い出すルール（Pinned）"] : null,
      node.status === "dormant" ? [el("br"), "眠っている記憶（Dormant）"] : null,
      node.status === "superseded" ? [el("br"), "置き換え済み（Superseded）"] : null,
      node.status === "retired" ? [el("br"), "外したルール・作り直し前の記憶（Retired）"] : null,
      node.kind === "procedural" && node.status === "active"
        ? [el("br"), STAGE_JA[node.stage] || "未整理のルール（以前の入れ方で入ったもの）"] : null,
      node.scope ? [el("br"), `${node.scope} の中だけ（Scope）`] : null));
    if (kase) {
      const rows = [["状況", kase.situation], ["判断", kase.decision], ["理由", kase.reason || "（オーナーは言っていない）"],
        ["反応", kase.reaction || "—"], ["時期", kase.when]];
      parts.push(el("div", { class: "section-title" }, cap("Case — 事例")));
      for (const [k, v] of rows) parts.push(el("p", { class: "hint" }, `${k}: ${v || "—"}`));
    }
    if (node.kind === "procedural" && node.stage === "tentative" && node.status === "active") {
      const answer = (verdict) => guarded(async () => {
        const r = await api("/api/rule/review", { id: node.id, verdict });
        toast(r.message_to_user);
        await selectNode(node.id);
      });
      parts.push(el("p", {},
        el("button", { class: "link cap", on: { click: () => answer("yes") } }, "そう（ルールにする）"), " / ",
        el("button", { class: "link cap", on: { click: () => answer("no") } }, "違う（外す）")));
    }
    const facts = [["Recalled", pad(node.access_count, 2)], ["Strength", strength != null ? strength.toFixed(2) : node.base_strength.toFixed(1)],
      ["Repeated", pad(Math.max(0, (node.occurrences || 1) - 1), 2)],
      ["Importance", node.importance.toFixed(1)], ["Corrected", pad(node.corrections, 2)],
      ["Links", pad(neighbors.length, 2)], ["Last recall", node.last_activated_at ? when(node.last_activated_at, false) : "—"]];
    parts.push(el("div", { class: "facts" }, facts.map(([k, v]) => el("div", {}, cap(k), el("span", {}, v)))));
  }
  if (source) {
    parts.push(el("div", { class: "section-title" }, cap("Original — もとになった原文"), cap(when(source.created_at, false))));
    parts.push(el("button", { class: "link source-link", on: { click: () => openSource(source.id) } }, source.title));
    if (shelves.length) parts.push(el("p", { class: "hint" }, `Shelves — ${shelves.join(" / ")}`));
  }
  if (quotes.length) {
    parts.push(el("div", { class: "section-title" }, cap("Evidence — 根拠の引用"), cap(pad(quotes.length, 2))));
    for (const q of quotes) parts.push(el("blockquote", { class: "quote" }, q.quote,
      el("button", { class: "link cap", on: { click: () => openSource(q.source_id) } }, `${when(q.created_at, false)} — ${q.title}`)));
  }
  if (derived_from.length) { // a rule's receipts (spec v0.8 §6.2)
    const support = derived_from.filter((d) => d.link === "derived_from"), against = derived_from.filter((d) => d.link !== "derived_from");
    parts.push(el("div", { class: "section-title" }, cap("Evidence — 根拠の事例"),
      cap(`根拠 ${support.length} 件${against.length ? `・合わない ${against.length} 件` : ""}`)));
    for (const d of [...support, ...against]) {
      parts.push(el("div", { class: "nbr", on: { click: () => focusNode(d.id) } },
        glyph("case"), el("span", { class: "t" }, (d.link === "contradicted_by" ? "合わなかった: " : "") + d.body)));
      if (d.quote) parts.push(el("blockquote", { class: "quote" }, d.quote, el("span", { class: "cap" }, ` — ${when(d.created_at, false)}`)));
    }
  }
  if (revisions.length) {
    parts.push(el("div", { class: "section-title" }, cap("Rewritten — 書き換えの履歴"), cap(pad(revisions.length, 2))));
    for (const r of revisions) parts.push(el("div", { class: "revision" },
      cap(`${when(r.at)} — ${who(r.actor)}`), el("p", { class: "old" }, r.old_body), el("p", {}, `→ ${r.new_body}`),
      el("p", { class: "hint" }, `理由: ${r.reason}`)));
  }
  parts.push(el("div", { class: "section-title" }, cap("Links — つながり"), cap(pad(neighbors.length, 2))));
  const maxW = Math.max(0.01, ...neighbors.map((n) => n.w));
  for (const n of neighbors) {
    const bar = el("span", { class: "wbar" });
    bar.style.width = `${Math.round(4 + 32 * (n.w / maxW))}px`;
    parts.push(el("div", { class: "nbr", on: { click: () => focusNode(n.id) } }, glyph(n.kind), bar,
      el("span", { class: "t" }, n.label), el("span", { class: "w" }, n.w.toFixed(2))));
  }
  if (node.kind !== "concept") {
    parts.push(el("p", {}, el("button", { class: "link cap", on: { click: () => addToErase("nodes", node.id) } }, "この記憶を消去の候補に入れる")));
  }
  const d = $("#detail");
  d.replaceChildren(...parts);
  d.hidden = false;
  $("#tab-graph").classList.add("detail-open"); // the search and filters step aside
  d.scrollTop = 0;
}

function renderTable(elements) {
  const body = $("#graph-table tbody");
  body.replaceChildren(...elements.map((n) => el("tr", { on: { click: () => { $("#g-table").checked = false; toggleTable(); focusNode(n.id); } } },
    el("td", { class: "num" }, pad(n.no, 4)),
    el("td", {}, glyph(n.kind), KIND_EN[n.kind]),
    el("td", {}, n.label), el("td", { class: "num" }, who(n.created_by)), el("td", { class: "num" }, when(n.created_at, false)))));
}

function updateOrigins(origins) {
  const sel = $("#g-origin");
  const current = sel.value;
  [...sel.querySelectorAll("option[data-ai]")].forEach((o) => o.remove());
  for (const o of origins) sel.append(el("option", { value: o, "data-ai": "1" }, o));
  sel.value = current;
}

function toggleTable() {
  $("#graph-table").hidden = !$("#g-table").checked;
}

$("#g-search").addEventListener("input", applySearch);
$("#g-search").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && G.matches && G.matches.size) focusNode([...G.matches][0]);
});
document.querySelectorAll(".kinds input, #g-days, #g-origin, #g-dormant").forEach((i) => i.addEventListener("change", loadGraph));
$("#g-changed").addEventListener("change", applyEmphasis);
$("#g-table").addEventListener("change", toggleTable);
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && G.selected) clearSelection(); });
loaders.graph = () => { if (!G.data) loadGraph(); };

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
      const d = new Date(s.created_at);
      (months[`${MONTHS[d.getMonth()]} ${d.getFullYear()}`] ||= []).push(s);
      const o = s.kind === "memo" ? "あなたのメモ" : s.kind === "dream" ? "夢日記" : (s.ai_name || "不明");
      (origins[o] ||= []).push(s);
    }
    $("#s-months").replaceChildren(...Object.entries(months).map(([m, l]) => item(m, l)));
    $("#s-origins").replaceChildren(...Object.entries(origins).sort().map(([o, l]) => item(o, l)));
    showList("All originals — すべての原文", sources);
  });
}

function markActive(li) {
  document.querySelectorAll(".shelf-nav li.active").forEach((x) => x.classList.remove("active"));
  li.classList.add("active");
}

function indexItem(i, s, extra = null) {
  return el("li", { class: "clickable", on: { click: () => readSource(s.source_id || s.id) } },
    el("span", { class: "n" }, `No. ${pad(i + 1)}`),
    el("span", { class: "title" }, s.title),
    el("span", { class: "meta" }, `${SOURCE_KIND[s.kind] || s.kind} — ${s.ai_name || (s.author === "human" ? "You" : "—")} — ${when(s.created_at, false)}`),
    extra);
}

function showList(title, list, passages = false) {
  $("#s-list-title").textContent = title;
  $("#s-list").replaceChildren(...list.map((s, i) => indexItem(i, s, passages ? el("span", { class: "passage" }, s.passage) : null)));
  if (!list.length) $("#s-list").append(el("li", { class: "muted" }, "見つかりませんでした"));
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
    if (!q) return showList("All originals — すべての原文", S.data ? S.data.sources : []);
    await guarded(async () => {
      const r = await api(`/api/search?q=${encodeURIComponent(q)}`);
      showList(`「${q}」 — ${pad(r.bookshelf.length, 2)} results`, r.bookshelf, true);
    });
  }, 250);
});
loaders.shelf = loadShelves;

// ---- brain: receiving box → hippocampus → cortex, bookshelf -----------------------

const li = (attrs, ...children) => el("li", attrs, ...children);
const t = (text) => el("span", { class: "t" }, text);
const m = (text) => el("span", { class: "m" }, text);

async function loadBrain() {
  await guarded(async () => {
    const b = await api("/api/brain");
    // A. receiving box
    // A. receiving box: files not yet read, and what arrived and waits to be screened (spec v0.8 §3.4)
    const arrived = b.arrived || [];
    $("#c-inbox").textContent = pad(b.inbox.length + arrived.length, 2);
    const nextScreen = () => { const h = new Date().getHours(); return h < 12 ? "12 時ごろ" : h < 16 ? "16 時ごろ" : "今夜の睡眠"; };
    const items = [...b.inbox.map((x) => li({},
      t(x.name), x.needs_check ? m(`確認が必要: ${x.reason || ""}`) : m("まもなく取り込みます"))),
    ...arrived.map((x) => li({ class: "clickable", on: { click: () => openSource(x.id) } },
      t(x.title), m(`${SOURCE_JA[x.kind] || x.kind} · ${x.writer} · 精査待ち（次は${nextScreen()}）`)))];
    $("#l-inbox").replaceChildren(...(items.length ? items :
      [li({ class: "empty" }, "空です。届いたものは、1日3回（12時・16時ごろと睡眠のとき）精査して、注意が向いたものだけ海馬へ移します。")]));
    $("#l-inbox").querySelectorAll(".m").forEach((n) => { if (n.textContent.startsWith("確認")) n.classList.add("bad"); });
    // B. hippocampus
    $("#c-hippo").textContent = pad(b.hippocampus.length, 2);
    $("#l-hippo").replaceChildren(...(b.hippocampus.length ? b.hippocampus.map((h) => {
      const left = Math.max(0, Math.min(1, h.days_left / h.days_total));
      const sigs = h.signals.map((s) => el("span", { class: "sig" }, PROMOTED_JA[s] || s));
      if (h.memories) sigs.push(el("span", { class: "sig soft" }, `記憶 ${h.memories}`));
      const sc = h.screened || {};
      if (sc.chatter) sigs.push(el("span", { class: "sig soft" }, `雑談 ${sc.chatter}`));
      if (sc.repeat) sigs.push(el("span", { class: "sig soft" }, `既知 ${sc.repeat}`));
      if (sc.drop) sigs.push(el("span", { class: "sig soft" }, `外した ${sc.drop}`));
      return li({ class: "clickable", on: { click: () => openSource(h.id) } },
        t(h.title), m(`${SOURCE_JA[h.kind] || h.kind} · ${h.writer} · あと ${h.days_left} 日`),
        sigs.length ? el("span", { class: "m" }, ...sigs) : null,
        el("div", { class: "fade", title: `消えるまで あと ${h.days_left} 日` }, el("i", { style: `width:${left * 100}%` })));
    }) : [li({ class: "empty" }, "いま海馬にある情報はありません。")]));
    // C. cortex: three boxes, links cross them freely
    const k = b.cortex.kinds;
    $("#c-cortex").textContent = pad(k.procedural + k.semantic + k.episode, 2);
    const score = (n) => n.importance * (n.strength ?? n.base_strength);
    const maxS = Math.max(0.01, ...b.cortex.memories.map(score));
    const EMPTY = { procedural: "まだありません。注意されたこと・好み・やり方がここに入ります。",
      semantic: "まだありません。決定や事実がここに入ります。", episode: "まだありません。出来事や、記憶を書き換えた経緯がここに入ります。" };
    for (const kind of ["procedural", "semantic", "episode"]) {
      const mem = b.cortex.memories.filter((n) => n.kind === kind || (kind === "episode" && n.kind === "case"));
      $(`#n-${kind}`).textContent = ` ${k[kind]}`;
      $(`#l-${kind}`).replaceChildren(...(mem.length ? mem.map((n) => {
        const ABOUT = { client: "クライアント", interviewee: "取材相手", other: "他の人" };
        const bits = [n.kind === "case" ? "事例" : null,
          n.kind === "procedural" ? (n.stage === "tentative" ? "仮のルール" : n.stage === "confirmed" ? null : "未整理") : null,
          n.scope ? `${n.scope} の中だけ` : null,
          n.about && n.about !== "owner" ? `${ABOUT[n.about] || n.about}${n.subject ? `（${n.subject}）` : ""}の話` : null,
          n.promoted_by ? `${PROMOTED_JA[n.promoted_by] || n.promoted_by}で記憶` : null,
          n.goods ? `褒められた ${n.goods}` : null, n.corrections ? `注意された ${n.corrections}` : null,
          n.occurrences > 1 ? `再登場 ${n.occurrences}` : null, n.revisions ? `書き換え ${n.revisions}` : null,
          n.pinned ? "固定" : null].filter(Boolean);
        return li({ class: "clickable", style: `opacity:${(0.35 + 0.65 * score(n) / maxS).toFixed(2)}`,
          on: { click: () => { showTab("graph"); setTimeout(() => selectNode(n.id), 400); } } }, t(n.body), m(bits.join(" · ")));
      }) : [li({ class: "empty" }, EMPTY[kind])]));
    }
    // D. bookshelf
    const shelf = Object.entries(b.bookshelf);
    $("#c-shelf").textContent = pad(shelf.reduce((a, [, n]) => a + n, 0), 2);
    $("#l-shelf").replaceChildren(...shelf.sort((a, b2) => b2[1] - a[1]).map(([kind, n]) => li({}, t(`${SOURCE_JA[kind] || kind}  ${n}`))));
    $("#flow-foot").textContent = `この7日間: 届いた資料 ${b.flow.received} · 大脳皮質に移った記憶 ${b.flow.promoted} · 海馬から薄れた情報（累計） ${b.flow.faded}`;
  });
}
$("#to-shelf").addEventListener("click", () => showTab("shelf"));
loaders.brain = loadBrain;

// ---- sleep -------------------------------------------------------------------------

let sleepPoll;
async function loadSleep() {
  await guarded(async () => {
    const s = await api("/api/sleep");
    const status = $("#sleep-status");
    if (s.running) status.replaceChildren(cap("Asleep — 睡眠中"), `Dreaming since ${when(s.started_at)}`);
    else if (s.error) status.replaceChildren(cap("Error", "cap bad"), s.error);
    else status.replaceChildren(cap(s.due ? "Time to sleep — そろそろ眠る時間です" : "Awake — 前回の睡眠"), s.last_sleep ? when(s.last_sleep) : "Never slept.");
    if (s.result && s.result.note) status.append(el("span", { class: "hint" }, s.result.note));
    $("#sleep-ai").disabled = s.running || !s.claude_found;
    $("#sleep-claude").textContent = s.claude_found ? "タイマーを待たずに、いま AI（Claude Code）で記憶を整理します。日報づくり・昇格・整理まで、夜の睡眠と同じことをします（Claude の利用枠を使います）。"
      : "Claude Code が見つからないため、AI による整理はできません（AI なしの整理は使えます）。";
    const hhmm = s.timer ? `${pad(s.timer.hour, 2)}:${pad(s.timer.minute, 2)}` : null;
    $("#timer-status").textContent = hhmm ? `Every day at ${hhmm}` : "Timer off — タイマーは切れています";
    if (hhmm) $("#timer-time").value = hhmm;
    $("#usage").replaceChildren(...(s.usage || []).map((u, i) => el("li", {},
      el("span", { class: "n" }, `No. ${pad(i + 1)}`),
      el("span", {}, `${when(u.at)} — 入力 ${(u.input_tokens + u.cache_read_tokens + u.cache_write_tokens).toLocaleString()}・出力 ${u.output_tokens.toLocaleString()} トークン`
        + (u.cost_usd != null ? `（目安 $${u.cost_usd.toFixed(2)}）` : "") + (u.seconds ? `・${Math.round(u.seconds / 60)} 分` : "")))));
    if (!(s.usage || []).length) $("#usage").append(el("li", { class: "muted" }, "まだ記録はありません（次の AI による睡眠から記録します）"));
    $("#dreams").replaceChildren(...s.dreams.map((d, i) => el("li", { class: "clickable", on: { click: () => openSource(d.id) } },
      el("span", { class: "n" }, `No. ${pad(s.dreams.length - i)}`), el("span", { class: "title" }, d.title),
      el("span", { class: "meta" }, `Dream journal — ${when(d.created_at)}`))));
    if (!s.dreams.length) $("#dreams").append(el("li", { class: "muted" }, "まだ夢日記はありません"));
    clearTimeout(sleepPoll);
    if (s.running) sleepPoll = setTimeout(loadSleep, 3000);
    else if (s.result) { G.data = null; loadMeta(); }
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
loaders.sleep = () => { loadSleep(); loadThreads(); };

let thTimer;
async function loadThreads() {
  await guarded(async () => {
    const q = $("#th-search").value.trim();
    const r = await api(`/api/threads${q ? `?q=${encodeURIComponent(q)}` : ""}`);
    $("#threads").replaceChildren(...r.threads.slice(0, 40).map((th, i) => {
      const btn = el("button", { class: "link cap", on: { click: async () => {
        await guarded(async () => {
          await api("/api/backfill", { keys: [th.key], cancel: th.queued });
          toast(th.queued ? "取り込み待ちから外しました。" : `「${th.title}」を預かりました。次の睡眠でまとめて預け入れにします。`);
          loadThreads();
        });
      } } }, th.queued ? "取り込み待ち — 外す" : th.past_left ? "預ける" : "");
      return el("li", {}, el("span", { class: "n" }, `No. ${pad(i + 1)}`),
        el("span", { class: "row" }, el("span", { class: "title" }, th.title),
          th.past_left || th.queued ? btn : el("span", { class: "hint" }, "預ける過去分はありません")),
        el("span", { class: "meta" }, `${th.ai} — ${when(th.first, false)} 〜 ${when(th.last, false)} — 過去分 ${th.past_left} 発言`));
    }));
    if (!r.threads.length) $("#threads").append(el("li", { class: "muted" }, "見つかりません"));
  });
}
$("#th-search").addEventListener("input", () => { clearTimeout(thTimer); thTimer = setTimeout(loadThreads, 300); });

// ---- safeguards --------------------------------------------------------------------

async function loadSafety() {
  await guarded(async () => {
    const s = await api("/api/safety");
    $("#pause").checked = s.paused;
    $("#paused-badge").hidden = !s.paused;
    $("#confirm-phrase").textContent = s.confirm_phrase;
    $("#backups").replaceChildren(...s.backups.map((name, i) => el("li", {},
      el("span", { class: "n" }, `No. ${pad(i + 1)}`),
      el("span", { class: "row" }, el("span", {}, name),
        el("button", { class: "link cap", on: { click: () => restore(name) } }, "この時点に戻す")))));
    if (!s.backups.length) $("#backups").append(el("li", { class: "muted" }, "まだバックアップはありません"));
  });
}

async function restore(name) {
  if (!confirm(`${name} の時点に脳を戻します。いまの脳は控えとして残します。よろしいですか？`)) return;
  await guarded(async () => {
    const r = await api("/api/restore", { name });
    toast(r.verified ? "戻しました。改ざんチェックも正常です。" : `戻しましたが、チェックで問題が見つかりました: ${r.message}`, !r.verified);
    G.data = null;
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
    $("#verify-result").className = "cap " + (r.ok ? "ok" : "bad");
    $("#verify-result").textContent = r.ok ? "Intact — 改ざんはありません" : `Alert — ${r.message}`;
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
      ...r.sources.map((s, i) => el("li", {}, el("span", { class: "n" }, `Orig. ${pad(i + 1)}`), el("span", { class: "title" }, s.title),
        el("span", { class: "meta" }, `Original — ${when(s.created_at, false)}`))),
      ...r.nodes.map((n, i) => el("li", {}, el("span", { class: "n" }, `Mem. ${pad(i + 1)}`), el("span", {}, n.label),
        el("span", { class: "meta" }, KIND_EN[n.kind] || n.kind))),
    ];
    $("#erase-plan-list").replaceChildren(...(rows.length ? rows : [el("li", { class: "muted" }, "該当するものはありません")]));
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
    G.data = null;
    loadSafety();
    loadMeta();
  });
});
loaders.safety = loadSafety;

// ---- start ---------------------------------------------------------------------------

$("#brand").addEventListener("click", () => window.exobrainIntro && window.exobrainIntro());

loadMeta();
showTab("brain");
