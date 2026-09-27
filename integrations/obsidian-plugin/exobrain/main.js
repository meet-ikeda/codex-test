/* exobrain — send a note to the receiving box, the way a post office counter does:
   check what it is (own note / interview / client, and whether it is confidential), send it,
   stamp a receipt on the note, and next time send only what was added since the stamp. */
"use strict";

const { Plugin, Modal, Notice, Setting, PluginSettingTab, FileSystemAdapter } = require("obsidian");
const fs = require("fs");
const path = require("path");

const STAMP = "exobrain受領";
const TYPE_KEY = "exobrain種類"; // remembered on the note so the counter is pre-filled next time
const SUBJECT_KEY = "exobrain相手";
const TYPES = { own: "自分のメモ", interview: "取材", client: "クライアント" };
const TAG_TO_TYPE = { "#取材": "interview", "#クライアント": "client" };

function defaultInbox(vaultPath) {
  // The vault lives in Google Drive (…/マイドライブ/my_brain_outside); exobrain sits next to it.
  return path.join(path.dirname(vaultPath), "exobrain", "受け取り箱", "Obsidian");
}

function stripFrontmatter(text) {
  const m = text.match(/^---\r?\n[\s\S]*?\r?\n---\r?\n?/);
  return m ? text.slice(m[0].length) : text;
}

/* Lines of `now` that are not in `before` (LCS on lines), grouped into hunks,
   each with its nearest heading for context. */
function addedParts(before, now) {
  const a = before.split("\n"), b = now.split("\n");
  if (a.length * b.length > 4e6) return now; // too big to compare: send the whole note
  const L = Array.from({ length: a.length + 1 }, () => new Uint16Array(b.length + 1));
  for (let i = a.length - 1; i >= 0; i--)
    for (let j = b.length - 1; j >= 0; j--)
      L[i][j] = a[i] === b[j] ? L[i + 1][j + 1] + 1 : Math.max(L[i + 1][j], L[i][j + 1]);
  const kept = new Set();
  for (let i = 0, j = 0; i < a.length && j < b.length;) {
    if (a[i] === b[j]) { kept.add(j); i++; j++; }
    else if (L[i + 1][j] >= L[i][j + 1]) i++;
    else j++;
  }
  const out = [];
  let lastHeading = null, inHunk = false;
  b.forEach((line, j) => {
    if (/^#{1,6}\s/.test(line)) lastHeading = line;
    if (!kept.has(j) && line.trim()) {
      if (!inHunk) {
        if (out.length) out.push("");
        if (lastHeading && lastHeading !== line) out.push(lastHeading);
        inHunk = true;
      }
      out.push(line);
    } else inHunk = false;
  });
  return out.join("\n").trim();
}

function yaml(v) { return JSON.stringify(v == null ? "" : String(v)); }
function stamp(d) {
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

class Counter extends Modal {
  constructor(plugin, file, text, fm, tags) {
    super(plugin.app);
    this.plugin = plugin; this.file = file; this.text = text; this.fm = fm || {};
    const fromTag = Object.entries(TAG_TO_TYPE).find(([t]) => tags.includes(t));
    const saved = Object.entries(TYPES).find(([, ja]) => ja === this.fm[TYPE_KEY]);
    this.type = fromTag ? fromTag[1] : saved ? saved[0] : "own";
    this.subject = this.fm[SUBJECT_KEY] || "";
    this.confidential = tags.includes("#機密");
  }

  onOpen() {
    const { contentEl } = this;
    contentEl.empty();
    contentEl.createEl("h3", { text: "exobrain 受付" });
    contentEl.createEl("p", { text: this.file.basename, cls: "setting-item-description" });

    const body = stripFrontmatter(this.text);
    const last = this.plugin.data.sent[this.file.path];
    const part = last ? addedParts(last.body, body) : body.trim();
    const what = !last ? "全文（初めて預けます）" : part ? `前回（${last.at}）からの差分 ${part.split("\n").length} 行` : "前回から変わっていません";
    contentEl.createEl("p", { text: `送るもの: ${what}` });

    new Setting(contentEl).setName("このメモは").setDesc("取材メモの体験は取材相手のもの、クライアントの話はクライアントのものとして覚えます")
      .addDropdown((d) => { for (const [k, v] of Object.entries(TYPES)) d.addOption(k, v); d.setValue(this.type).onChange((v) => { this.type = v; this.onOpen(); }); });
    if (this.type !== "own")
      new Setting(contentEl).setName(this.type === "interview" ? "取材相手" : "クライアント").setDesc("名前か役割（例: 佐藤さん（入社3年目）/ A社 人事部）")
        .addText((t) => t.setValue(this.subject).onChange((v) => { this.subject = v.trim(); }));
    new Setting(contentEl).setName("機密").setDesc("本棚にだけ置き、大脳皮質には移しません（Google ドライブの写しにも出ません）")
      .addToggle((t) => t.setValue(this.confidential).onChange((v) => { this.confidential = v; }));

    const buttons = new Setting(contentEl);
    buttons.addButton((b) => b.setButtonText("やめる").onClick(() => this.close()));
    buttons.addButton((b) => b.setButtonText("預ける").setCta().setDisabled(!part).onClick(async () => {
      if (this.type !== "own" && !this.subject) { new Notice(`${TYPES[this.type]}の相手を入れてください`); return; }
      try {
        await this.plugin.send(this.file, body, part, !!last, { type: this.type, subject: this.subject, confidential: this.confidential });
        this.close();
      } catch (e) { new Notice(`送れませんでした: ${e.message}`, 8000); }
    }));
  }

  onClose() { this.contentEl.empty(); }
}

class ExobrainSettings extends PluginSettingTab {
  constructor(app, plugin) { super(app, plugin); this.plugin = plugin; }
  display() {
    const { containerEl } = this;
    containerEl.empty();
    new Setting(containerEl).setName("預かりBOX のフォルダ").setDesc("exobrain の「受け取り箱/Obsidian」")
      .addText((t) => t.setValue(this.plugin.data.inbox).onChange(async (v) => { this.plugin.data.inbox = v.trim(); await this.plugin.saveData(this.plugin.data); }));
  }
}

module.exports = class Exobrain extends Plugin {
  async onload() {
    if (!(this.app.vault.adapter instanceof FileSystemAdapter)) { new Notice("exobrain はデスクトップ専用です"); return; }
    const vaultPath = this.app.vault.adapter.getBasePath();
    this.data = Object.assign({ inbox: defaultInbox(vaultPath), sent: {} }, await this.loadData());
    this.addRibbonIcon("brain", "exobrain に預ける", () => this.open());
    this.addCommand({ id: "send-note", name: "このノートを exobrain に預ける", callback: () => this.open() });
    this.addSettingTab(new ExobrainSettings(this.app, this));
  }

  async open() {
    const file = this.app.workspace.getActiveFile();
    if (!file || file.extension !== "md") { new Notice("預けるノートを開いてから押してください"); return; }
    const text = await this.app.vault.read(file);
    const cache = this.app.metadataCache.getFileCache(file) || {};
    const tags = (cache.tags || []).map((t) => t.tag).concat(((cache.frontmatter || {}).tags || []).map((t) => "#" + String(t).replace(/^#/, "")));
    new Counter(this, file, text, cache.frontmatter, tags).open();
  }

  async send(file, body, part, isDiff, info) {
    const now = new Date();
    const partLabel = isDiff ? `差分（${this.data.sent[file.path].at} 以降）` : "全文";
    const head = ["---", "exobrain_kind: obsidian_note", `exobrain_source_file: ${yaml(file.path)}`,
      `exobrain_note_type: ${yaml(info.type === "own" ? "" : info.type)}`, `exobrain_subject: ${yaml(info.subject)}`,
      `exobrain_confidential: ${info.confidential ? "true" : "false"}`, `exobrain_part: ${yaml(partLabel)}`,
      `exobrain_sent_at: ${yaml(now.toISOString())}`, "---", ""];
    fs.mkdirSync(this.data.inbox, { recursive: true });
    const safe = file.basename.replace(/[\\/:*?"<>|]/g, "_").slice(0, 40);
    const name = `${stamp(now).replace(/[-: ]/g, "")}_${safe}${isDiff ? "_差分" : ""}.md`;
    const target = path.join(this.data.inbox, name);
    fs.writeFileSync(target + ".tmp", head.join("\n") + part + "\n", "utf8"); // exobrain ignores .tmp
    fs.renameSync(target + ".tmp", target);
    // The receipt: on the note, and in the plugin's memory of what was sent.
    await this.app.fileManager.processFrontMatter(file, (fm) => {
      fm[STAMP] = `${stamp(now)} ${partLabel.startsWith("差分") ? "差分" : "全文"}`;
      fm[TYPE_KEY] = TYPES[info.type];
      if (info.subject) fm[SUBJECT_KEY] = info.subject; else delete fm[SUBJECT_KEY];
    });
    this.data.sent[file.path] = { at: stamp(now), body };
    await this.saveData(this.data);
    new Notice(`exobrain の預かりBOX に送りました（${partLabel}）${info.confidential ? "。機密なので本棚にだけ置きます" : ""}`);
  }
};
