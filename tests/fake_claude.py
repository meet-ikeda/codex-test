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
        elif a == "--strict-mcp-config":
            args["strict"], i = True, i + 1
        else:
            i += 1
    return args


def answer(item):
    t = item["type"]
    if t == "decompose":
        first = item["text"].strip().splitlines()[0].lstrip("# ").strip()
        return {"item_id": item["item_id"], "elements": [
            {"kind": "procedural", "text": first, "concepts": ["睡眠テスト"], "importance": 0.8}]}
    if t == "consolidate":
        return {"item_id": item["item_id"], "elements": [
            {"kind": "semantic", "text": f"{item['concept']} の出来事が {item['episode_count']} 件ある",
             "concepts": [item["concept"]]}]}
    if t == "reconcile":
        return {"item_id": item["item_id"], "action": "supersede", "keep_id": item["b"]["id"]}
    if t == "verify_links":
        return {"item_id": item["item_id"], "keep": [[l["src"], l["dst"]] for l in item["links"]], "drop": []}
    if t == "shelve":
        return {"item_id": item["item_id"], "assignments": {s["source_id"]: ["テスト棚"] for s in item["sources"]}}
    raise AssertionError(t)


async def main():
    args = parse(sys.argv[1:])
    assert args["strict"] and args["allowed"] == "mcp__exobrain-sleep__" and "sleep_next_batch" in args["prompt"]
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
    print("fake claude done")


anyio.run(main)
