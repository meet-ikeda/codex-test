"""Compare embedding models on the fixed question set (docs/eval-questions-v2.md).

Usage: EXOBRAIN_HOME=<a test home holding the S1-S6 sources> python scripts/eval_embedding.py <model or lexical> [threshold] [-v]
"""
import re, sys, json
from exobrain.config import load_settings
from exobrain.brain import Brain
from exobrain.embed import make_embedder
from exobrain.hippocampus import search, encode_pending
import exobrain.hippocampus as H

rows = [l for l in open(__import__("pathlib").Path(__file__).parent.parent / "docs" / "eval-questions-v2.md", encoding="utf-8")
        if re.match(r"\| Q\d\d ", l)]
qs = []
for l in rows:
    c = [x.strip() for x in l.strip().strip("|").split("|")]
    qs.append({"id": c[0], "kind": c[1], "q": c[2], "req": re.findall(r"S\d", c[4])})
mode = sys.argv[1]  # model name or "lexical"
th = float(sys.argv[2]) if len(sys.argv) > 2 else H.MIN_SEMANTIC
H.MIN_SEMANTIC = th
st = load_settings()
b = Brain(st)
b.embedder = None if mode == "lexical" else make_embedder(mode, st.ollama_url)
if b.embedder:
    while encode_pending(b): pass
title_of = {r["id"]: r["title"] for r in b._conn.execute("SELECT id, title FROM sources")}
got = need = irrelevant = 0
fp = []
detail = []
for q in qs:
    hits = search(b, q["q"], limit=5)
    labels = [title_of[h.source_id][:2] for h in hits]
    if not q["req"]:
        ok = not hits
        if not ok: fp.append(q["id"])
        detail.append((q["id"], "記録なし", "OK" if ok else f"誤って {labels}"))
        continue
    found = [s for s in q["req"] if s in labels]
    got += len(found); need += len(q["req"])
    irrelevant += sum(1 for l in labels if l not in q["req"])
    detail.append((q["id"], q["kind"], f"{len(found)}/{len(q['req'])} {labels}"))
print(json.dumps({"mode": mode, "threshold": th, "required_found": f"{got}/{need}",
                  "irrelevant_in_top5": irrelevant, "no_record_false_hits": fp}, ensure_ascii=False))
if "-v" in sys.argv:
    for d in detail: print(" ", *d)
