"""Stands in for `claude -p` in tests.

Checks the arguments exobrain passes, starts the MCP server from --mcp-config
over real stdio, and does the sleep work the way the prompt asks.
"""

import json
import sys

import anyio
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


def parse(argv):
    args = {"allowed": None, "config": None, "strict": False, "prompt": None}
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "-p":
            args["prompt"], i = argv[i + 1], i + 2
        elif a == "--mcp-config":
            args["config"], i = argv[i + 1], i + 2
        elif a == "--allowedTools":
            args["allowed"], i = argv[i + 1], i + 2
        elif a == "--output-format":
            args["format"], i = argv[i + 1], i + 2
        elif a == "--strict-mcp-config":
            args["strict"], i = True, i + 1
        else:
            i += 1
    return args


def answer(item):
    t = item["type"]
    if t == "promote":
        n, first = next((n, l) for n, l in item["lines"] if l.strip())
        evidence = item.get("evidence")
        evidence_lines = [evidence["lines"][0][0]] * 2 if evidence else None
        return {"item_id": item["item_id"], "atoms": [
            {"kind": "semantic", "text": first.lstrip("# ").strip(), "derivation": "verbatim", "lines": [n, n],
             "concepts": ["睡眠テスト"], "confidence": 0.9,
             **({"evidence_lines": evidence_lines} if evidence_lines else {})}]}
    if t == "write_daily":
        return {"item_id": item["item_id"], "events": ["テストの会話をした"], "decisions": ["テストで決めた"]}
    if t == "reconcile":
        return {"item_id": item["item_id"], "action": "supersede", "keep_id": item["b"]["id"]}
    if t == "verify_links":
        return {"item_id": item["item_id"], "keep": [[l["src"], l["dst"]] for l in item["links"]], "drop": []}
    if t == "shelve":
        return {"item_id": item["item_id"], "assignments": {s["source_id"]: ["テスト棚"] for s in item["sources"]}}
    raise AssertionError(t)


async def main():
    args = parse(sys.argv[1:])
    assert args["strict"] and args["allowed"] == "mcp__exobrain-sleep" and "sleep_next_batch" in args["prompt"]
    server = json.load(open(args["config"], encoding="utf-8"))["mcpServers"]["exobrain-sleep"]
    params = StdioServerParameters(command=server["command"], args=server["args"], env=server["env"])
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            tools = {t.name for t in (await s.list_tools()).tools}
            assert tools == {"sleep_next_batch", "sleep_apply", "sleep_finish", "open_source"}, tools
            while True:
                batch = (await s.call_tool("sleep_next_batch", {})).structured_content
                if batch["done"]:
                    break
                res = await s.call_tool("sleep_apply", {"batch_id": batch["batch_id"],
                                                        "results": [answer(i) for i in batch["items"]]})
                assert not res.is_error, res.content
            await s.call_tool("sleep_finish", {"summary": "テストの夢を見ました。"})
    assert args.get("format") == "json"
    print(json.dumps({"type": "result", "num_turns": 7, "duration_ms": 12000, "total_cost_usd": 0.12,
                      "usage": {"input_tokens": 1000, "output_tokens": 200, "cache_read_input_tokens": 5000,
                                "cache_creation_input_tokens": 300}}))


anyio.run(main)
