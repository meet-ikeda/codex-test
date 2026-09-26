"""Human CLI. Only a human (you) can supersede, retract, or accept AI proposals."""

from __future__ import annotations

import argparse
import json
import sys

from .store import MemoryStore, NotFound, Record


def _fmt(store: MemoryStore, rec: Record) -> str:
    who = "👤" if rec.author == "human" else "🤖"
    status = store.status(rec.id)
    head = f"#{rec.id} {who} {rec.type} [{status}] {rec.created_at[:19].replace('T', ' ')}"
    if rec.ref is not None:
        head += f" → #{rec.ref}"
    if rec.tags:
        head += "  " + " ".join(f"#{t}" for t in rec.tags)
    body = "\n".join("    " + line for line in rec.content.splitlines()) or "    (no content)"
    return f"{head}\n{body}"


def _print(store: MemoryStore, records: list[Record]) -> None:
    if not records:
        print("(nothing)")
    for r in records:
        print(_fmt(store, r))


def _tags(value: str | None) -> list[str] | None:
    return None if value is None else [t for t in value.split(",") if t.strip()]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="exobrain", description="Append-only external-brain memory")
    p.add_argument("--db", help="database path (default: $EXOBRAIN_DB or ~/.exobrain/memory.db)")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("add", help="add a memory")
    s.add_argument("content", nargs="?", help="text (reads stdin if omitted)")
    s.add_argument("-t", "--tags", help="comma-separated tags")

    s = sub.add_parser("list", help="list current memories")
    s.add_argument("query", nargs="?", default="")
    s.add_argument("-t", "--tag")
    s.add_argument("--author", choices=["human", "ai"])
    s.add_argument("-n", "--limit", type=int, default=50)

    s = sub.add_parser("show", help="show a record and its full history")
    s.add_argument("id", type=int)

    s = sub.add_parser("revise", help="write a new version of a memory (old one is kept)")
    s.add_argument("id", type=int)
    s.add_argument("content", nargs="?", help="text (reads stdin if omitted)")
    s.add_argument("-t", "--tags", help="replace tags (comma-separated)")

    s = sub.add_parser("retract", help="withdraw a memory (kept in history)")
    s.add_argument("id", type=int)
    s.add_argument("-r", "--reason", default="")

    sub.add_parser("proposals", help="list pending AI revision proposals")

    s = sub.add_parser("accept", help="accept an AI proposal")
    s.add_argument("id", type=int)

    s = sub.add_parser("reject", help="reject an AI proposal")
    s.add_argument("id", type=int)
    s.add_argument("-r", "--reason", default="rejected")

    sub.add_parser("verify", help="check the hash chain")
    sub.add_parser("export", help="dump every record as JSON Lines")

    a = p.parse_args(argv)
    store = MemoryStore(a.db)
    try:
        if a.cmd == "add":
            _print(store, [store.remember(a.content or sys.stdin.read().strip(), "human", _tags(a.tags))])
        elif a.cmd == "list":
            _print(store, store.current(a.query or None, tag=a.tag, author=a.author, limit=a.limit))
        elif a.cmd == "show":
            _print(store, store.history(a.id))
        elif a.cmd == "revise":
            _print(store, [store.supersede(a.id, a.content or sys.stdin.read().strip(), _tags(a.tags))])
        elif a.cmd == "retract":
            if store.get(a.id).type != "memory":
                raise ValueError("use `reject` for proposals")
            _print(store, [store.retract(a.id, a.reason)])
        elif a.cmd == "proposals":
            for prop in store.pending_proposals():
                print(_fmt(store, store.get(prop.ref)))
                print("  proposed:")
                print(_fmt(store, prop))
                print()
            if not store.pending_proposals():
                print("(no pending proposals)")
        elif a.cmd == "accept":
            _print(store, [store.accept_proposal(a.id)])
        elif a.cmd == "reject":
            if a.id not in {p.id for p in store.pending_proposals()}:
                raise ValueError(f"#{a.id} is not a pending proposal")
            _print(store, [store.retract(a.id, a.reason)])
        elif a.cmd == "verify":
            ok, msg = store.verify()
            print(msg)
            return 0 if ok else 1
        elif a.cmd == "export":
            for r in store.all_records():
                print(json.dumps(r.to_dict(), ensure_ascii=False))
    except (NotFound, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
