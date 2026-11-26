"""Unified command line entry: demo, serve, search, rebuild, eval, dashboard, ingest."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys


def find_root(explicit: str | None) -> Path:
    """Locate a repository root that owns corpus/manifest.json."""
    if explicit:
        root = Path(explicit).expanduser().resolve()
    else:
        candidates = [Path.cwd(), *Path.cwd().parents, Path(__file__).resolve().parents[1]]
        root = next((path for path in candidates if (path / "corpus" / "manifest.json").exists()), None)
        if root is None:
            raise SystemExit("Cannot locate corpus/manifest.json; pass --root <repo dir>.")
        root = Path(root).resolve()
    if not (root / "corpus" / "manifest.json").exists():
        raise SystemExit(f"No corpus/manifest.json under {root}; run `toolgate ingest` first.")
    return root


def slug(stem: str) -> str:
    text = re.sub(r"[^\w\u4e00-\u9fff-]+", "-", stem).strip("-").lower()
    return text or "doc-" + hashlib.sha1(stem.encode("utf-8")).hexdigest()[:8]


def title_of(raw: bytes, fallback: str) -> str:
    for line in raw.decode("utf-8", "replace").splitlines():
        line = line.strip()
        if line.startswith("# "):
            return line[2:].strip() or fallback
        if line:
            break
    return fallback


def build_manifest(docs_dir: Path, description: str) -> dict:
    documents = []
    for path in sorted(docs_dir.glob("*.md")):
        raw = path.read_bytes()
        documents.append({
            "doc_id": slug(path.stem),
            "path": f"docs/{path.name}",
            "title": title_of(raw, path.stem),
            "tags": [],
            "mtime_ns": path.stat().st_mtime_ns,
            "sha256": hashlib.sha256(raw).hexdigest(),
        })
    ids = [entry["doc_id"] for entry in documents]
    if len(ids) != len(set(ids)):
        raise SystemExit("Duplicate doc_id after slugging; rename colliding files.")
    return {"version": 1, "description": description, "documents": documents}


def command_ingest(args: argparse.Namespace) -> None:
    source = Path(args.source).expanduser().resolve()
    if not source.is_dir():
        raise SystemExit(f"Not a directory: {source}")
    root = Path(args.root).expanduser().resolve()
    docs_dir = root / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)
    if args.clean:
        for path in docs_dir.glob("*.md"):
            path.unlink()
    markdown = sorted(source.rglob("*.md") if args.recursive else source.glob("*.md"))
    copied = 0
    for path in markdown:
        target = docs_dir / path.name
        if target.exists() and target.resolve() == path.resolve():
            continue
        shutil.copy2(path, target)
        copied += 1
    if not any(docs_dir.glob("*.md")):
        raise SystemExit(f"No Markdown files found under {source}")
    manifest = build_manifest(docs_dir, f"Imported from {source}")
    manifest_path = root / "corpus"
    manifest_path.mkdir(parents=True, exist_ok=True)
    (manifest_path / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    from .kb.index import KnowledgeIndex
    with KnowledgeIndex(root=root) as index:
        result = index.rebuild()
    print(json.dumps({"root": str(root), "copied": copied, **result}, ensure_ascii=False, indent=2))


def command_search(args: argparse.Namespace) -> None:
    root = find_root(args.root)
    from .kb.index import KnowledgeIndex
    with KnowledgeIndex(root=root) as index:
        if args.rebuild:
            index.rebuild()
        if not index.stats()["documents"]:
            raise SystemExit("Index is empty; run `toolgate rebuild` or `toolgate ingest`.")
        result = index.search(args.query, args.limit, args.mode)
    print(json.dumps(result, ensure_ascii=False, indent=2))


def command_rebuild(args: argparse.Namespace) -> None:
    root = find_root(args.root)
    from .kb.index import KnowledgeIndex
    with KnowledgeIndex(root=root) as index:
        print(json.dumps(index.rebuild(), ensure_ascii=False, indent=2))


def command_demo(args: argparse.Namespace) -> None:
    from .mcp_server import Server, demo_registry
    server = Server(demo_registry())
    requests = [("initialize", {}), ("tools/list", {}),
                ("tools/call", {"name": "echo", "arguments": {"message": "hello toolgate"}}),
                ("tools/call", {"name": "kb_search", "arguments": {"query": "混合检索 重排"}})]
    for index, (method, params) in enumerate(requests, 1):
        response = server.handle({"jsonrpc": "2.0", "id": index, "method": method, "params": params})
        print(json.dumps({"method": method, "response": response}, ensure_ascii=False))


def command_serve(args: argparse.Namespace) -> None:
    from .mcp_server import Server, demo_registry
    server = Server(demo_registry())
    for line in sys.stdin:
        response = server.handle_line(line)
        if response is not None:
            print(json.dumps(response, ensure_ascii=False), flush=True)


def run_in_repo(root: Path, argv: list[str]) -> None:
    """Evaluation and observability scripts live outside the package; run them from the repo."""
    if not (root / "src").is_dir():
        raise SystemExit("This subcommand needs the repository layout; run it inside the cloned repo.")
    completed = subprocess.run([sys.executable, *argv], cwd=root)
    raise SystemExit(completed.returncode)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="toolgate", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    demo = commands.add_parser("demo", help="Run four JSON-RPC calls against the demo registry")
    demo.set_defaults(func=command_demo)

    serve = commands.add_parser("serve", help="Serve MCP-style JSON-RPC over stdio")
    serve.set_defaults(func=command_serve)

    search = commands.add_parser("search", help="Query the hybrid index")
    search.add_argument("query")
    search.add_argument("--limit", type=int, default=3)
    search.add_argument("--mode", choices=("hybrid", "baseline"), default="hybrid")
    search.add_argument("--root")
    search.add_argument("--rebuild", action="store_true")
    search.set_defaults(func=command_search)

    rebuild = commands.add_parser("rebuild", help="Rebuild the index incrementally")
    rebuild.add_argument("--root")
    rebuild.set_defaults(func=command_rebuild)

    ingest = commands.add_parser("ingest", help="Build a searchable index from your own Markdown")
    ingest.add_argument("--from", dest="source", required=True, help="Directory containing .md files")
    ingest.add_argument("--root", default="~/.toolgate", help="Where to place docs/, corpus/ and out/")
    ingest.add_argument("--recursive", action="store_true")
    ingest.add_argument("--clean", action="store_true", help="Delete previously imported Markdown first")
    ingest.set_defaults(func=command_ingest)

    evaluate = commands.add_parser("eval", help="Run the labelled evaluation (needs repo layout)")
    evaluate.add_argument("--mode", choices=("baseline", "hybrid"), default="hybrid")
    evaluate.add_argument("--root")
    evaluate.set_defaults(func=lambda args: run_in_repo(find_root(args.root),
                                                        ["eval/run_eval.py", "--" + args.mode]))

    dashboard = commands.add_parser("dashboard", help="Summarize out/trace.jsonl (needs repo layout)")
    dashboard.add_argument("--root")
    dashboard.set_defaults(func=lambda args: run_in_repo(find_root(args.root), ["-m", "src.observability"]))

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
