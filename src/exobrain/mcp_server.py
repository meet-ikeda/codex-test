"""MCP server: the AI's only door into the memory store.

The AI can append new memories, search, read history and *propose* revisions.
There is deliberately no tool that supersedes, retracts or deletes anything —
those actions live only in the human CLI (`exobrain`).
"""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from .store import MemoryStore, NotFound, Record

INSTRUCTIONS = """\
This is the user's external brain: a long-term, append-only memory.
- Call `recall` before answering questions about the user's past, preferences, decisions or projects.
- Memories with author="human" are the user's own words and are authoritative.
  Memories with author="ai" were written by an assistant and may be wrong.
- Use `remember` to save new, durable information the user tells you.
- You cannot edit or delete memories. If one looks outdated or wrong, call
  `propose_revision`; the user decides whether to accept it.
"""


def _view(store: MemoryStore, rec: Record) -> dict[str, Any]:
    d = {
        "id": rec.id,
        "created_at": rec.created_at,
        "author": rec.author,
        "type": rec.type,
        "content": rec.content,
        "tags": rec.tags,
        "status": store.status(rec.id),
    }
    if rec.ref is not None:
        d["ref"] = rec.ref
    return d


def build_server(store: MemoryStore) -> MCPServer:
    server = MCPServer(name="exobrain", instructions=INSTRUCTIONS)
    read_only = ToolAnnotations(read_only_hint=True)
    append_only = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False)

    @server.tool(annotations=append_only)
    def remember(content: str, tags: list[str] | None = None) -> dict[str, Any]:
        """Save a new memory. It is recorded as author="ai" and can never be changed afterwards by the AI."""
        return _view(store, store.remember(content, author="ai", tags=tags))

    @server.tool(annotations=read_only)
    def recall(query: str = "", tag: str | None = None, limit: int = 20) -> dict[str, Any]:
        """Search current memories (newest first). Every whitespace-separated term in `query` must match.
        Also returns open revision proposals for the matched memories."""
        hits = store.current(query=query or None, tag=tag, limit=max(1, min(limit, 100)))
        ids = {r.id for r in hits}
        proposals = [p for p in store.pending_proposals() if p.ref in ids]
        return {
            "memories": [_view(store, r) for r in hits],
            "pending_proposals": [_view(store, p) for p in proposals],
        }

    @server.tool(annotations=read_only)
    def history(memory_id: int) -> dict[str, Any]:
        """Show every version of a memory, plus proposals and retractions, oldest first."""
        try:
            return {"records": [_view(store, r) for r in store.history(memory_id)]}
        except NotFound as e:
            return {"error": str(e)}

    @server.tool(annotations=append_only)
    def propose_revision(memory_id: int, content: str) -> dict[str, Any]:
        """Suggest a corrected version of a memory. The original stays current until the user accepts."""
        try:
            return _view(store, store.propose_revision(memory_id, content, author="ai"))
        except (NotFound, ValueError) as e:
            return {"error": str(e)}

    @server.tool(annotations=read_only)
    def verify_integrity() -> dict[str, Any]:
        """Check that the hash chain is intact (nothing was altered outside the app)."""
        ok, message = store.verify()
        return {"ok": ok, "message": message}

    return server


def main() -> None:
    build_server(MemoryStore()).run("stdio")


if __name__ == "__main__":
    main()
