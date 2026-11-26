"""Unified command line entry: demo, serve, search, rebuild, eval, dashboard, ingest."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile


def find_root(explicit: str | None) -> Path:
    """Locate a repository root that owns corpus/manifest.json."""
    if explicit:
        root = Path(explicit).expanduser().resolve()
    else:
        candidates = [Path.cwd(), *Path.cwd().parents]
        root = next((path for path in candidates if (path / "corpus" / "manifest.json").exists()), None)
        if root is None:
            raise SystemExit("Cannot locate corpus/manifest.json; run `toolgate ingest --from <notes> --root <kb>` or pass --root <kb>.")
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
    for path in sorted(docs_dir.rglob("*.md")):
        raw = path.read_bytes()
        documents.append({
            "doc_id": "/".join(slug(part) for part in path.relative_to(docs_dir).with_suffix("").parts),
            "path": "docs/" + path.relative_to(docs_dir).as_posix(),
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
    resolved_docs = docs_dir.resolve()
    if source == resolved_docs or source.is_relative_to(resolved_docs) or resolved_docs.is_relative_to(source):
        raise SystemExit("Source and root/docs must not be equal or contain each other.")
    markdown = sorted(source.rglob("*.md") if args.recursive else source.glob("*.md"))
    incoming = {path.relative_to(source): path.read_bytes() for path in markdown}
    if not incoming:
        raise SystemExit(f"No Markdown files found under {source}")
    conflicts = [str(relative) for relative, raw in incoming.items()
                 if (docs_dir / relative).exists() and (docs_dir / relative).read_bytes() != raw]
    if conflicts and not args.overwrite:
        raise SystemExit("Conflicting files (use --overwrite):\n" + "\n".join(conflicts))
    root.mkdir(parents=True, exist_ok=True)
    from .kb.index import KnowledgeIndex
    # Stage a complete generation on the same filesystem before publishing anything.
    with tempfile.TemporaryDirectory(prefix=".ingest-", dir=root) as directory:
        stage = Path(directory)
        staged_docs = stage / "docs"
        if docs_dir.exists():
            shutil.copytree(docs_dir, staged_docs)
        else:
            staged_docs.mkdir()
        if args.clean:
            for path in staged_docs.rglob("*.md"):
                path.unlink()
        copied = 0
        for relative, raw in incoming.items():
            target = staged_docs / relative
            if not target.exists() or target.read_bytes() != raw:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(raw)
                copied += 1
        manifest = build_manifest(staged_docs, f"Imported from {source}")
        (stage / "corpus").mkdir()
        (stage / "corpus/manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        old_db = root / "out/kb.sqlite3"
        if old_db.exists():
            with KnowledgeIndex(root) as old, KnowledgeIndex(stage) as new:
                old.connection.backup(new.connection)
        with KnowledgeIndex(stage) as index:
            result = index.rebuild()
        published = []
        try:
            for relative in ("docs", "corpus/manifest.json", "out/kb.sqlite3"):
                target = root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                backup = stage / "backup" / relative
                existed = target.exists()
                if existed:
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    target.replace(backup)
                published.append((target, backup, existed))
                (stage / relative).replace(target)
        except BaseException:
            for target, backup, existed in reversed(published):
                if target.is_dir():
                    shutil.rmtree(target)
                elif target.exists():
                    target.unlink()
                if existed:
                    backup.replace(target)
            raise
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
    from .audit import AuditLog
    from .mcp_server import Server, demo_registry, run_demo
    root = find_root(args.root)
    run_demo(Server(demo_registry(root), AuditLog(root / "out/trace.jsonl")), message="hello toolgate")


def command_serve(args: argparse.Namespace) -> None:
    from .mcp_server import Server, demo_registry
    root = find_root(args.root)
    from .audit import AuditLog
    from .kb.index import KnowledgeIndex
    if not (root / "out/kb.sqlite3").is_file():
        raise SystemExit("Missing knowledge index; run `toolgate ingest` or `toolgate rebuild` first.")
    with KnowledgeIndex(root) as index:
        if not index.stats()["documents"]:
            raise SystemExit("Empty knowledge index; run `toolgate ingest` or `toolgate rebuild` first.")
    server = Server(demo_registry(root), AuditLog(args.trace or root / "out/trace.jsonl"))
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
    parser.add_argument("--root", dest="global_root")
    commands = parser.add_subparsers(dest="command", required=True)

    demo = commands.add_parser("demo", help="Run four JSON-RPC calls against the demo registry")
    demo.add_argument("--root")
    demo.set_defaults(func=command_demo)

    serve = commands.add_parser("serve", help="Serve MCP-style JSON-RPC over stdio")
    serve.add_argument("--root")
    serve.add_argument("--trace")
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
    ingest.add_argument("--overwrite", action="store_true")
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
    if args.global_root:
        args.root = args.global_root
    args.func(args)


if __name__ == "__main__":
    main()
