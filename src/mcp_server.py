"""MCP-style JSON-RPC 2.0, one JSON message per stdio line."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sys
from time import perf_counter

from .audit import AuditLog
from .kb.index import KnowledgeIndex
from .registry import Registry, Tool


def kb_search(query: str, limit: int = 3, mode: str = "passages", max_per_doc: int = 2,
              root: Path | None = None) -> dict:
    if mode not in ("passages", "documents") or max_per_doc < 1:
        raise ValueError("Invalid retrieval mode or max_per_doc")
    with KnowledgeIndex(**({"root": root} if root is not None else {})) as index:
        if not index.stats()["documents"]:
            index.rebuild()
        candidates = index.search(query, limit=index.stats()["blocks"], mode="hybrid", max_per_doc=None)
    results = []
    counts = {}
    for row in candidates:
        doc_id = row["doc_id"]
        count = counts.get(doc_id, 0)
        if count >= (1 if mode == "documents" else max_per_doc):
            continue
        counts[doc_id] = count + 1
        if mode == "documents":
            passages = [item for item in candidates if item["doc_id"] == doc_id]
            row = {**row, "summary": "\n\n".join(item["text"] for item in passages[:2]),
                   "best_passage": dict(row), "passages": passages,
                   "matched_passages": len(passages)}
        results.append(row)
        if len(results) == limit:
            break
    return {"query": query, "mode": mode, "results": results}


def demo_registry(root: Path | None = None) -> Registry:
    registry = Registry()
    registry.kb_root = root if root is not None else Path(__file__).resolve().parents[1]

    def search(query: str, limit: int = 3, mode: str = "passages", max_per_doc: int = 2) -> dict:
        return kb_search(query, limit, mode, max_per_doc, root=root)

    registry.register(Tool("kb_search", "1.0", "Search the local FTS + vector index with RRF and reranking",
                           {"type": "object", "additionalProperties": False,
                            "required": ["query"], "properties": {
                               "query": {"type": "string", "maxLength": 1000},
                               "limit": {"type": "integer", "minimum": 1},
                               "mode": {"enum": ["passages", "documents"]},
                               "max_per_doc": {"type": "integer", "minimum": 1}}}, False,
                           search))
    registry.register(Tool("echo", "1.0", "Return the supplied message",
                           {"type": "object", "required": ["message"], "properties": {
                               "message": {"type": "string", "maxLength": 2000}}}, False,
                           lambda message: {"message": message}))
    registry.register(Tool("clock", "1.0", "Return the current UTC time",
                           {"type": "object", "properties": {}}, False,
                           lambda: {"utc": datetime.now(timezone.utc).isoformat()}))
    return registry


def validate_json(value: object, depth: int = 0) -> None:
    if depth > 64:
        raise ValueError("Excessive nesting")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("Non-finite number")
    if isinstance(value, str):
        value.encode("utf-8")
    elif isinstance(value, dict):
        for key, item in value.items():
            validate_json(key, depth + 1)
            validate_json(item, depth + 1)
    elif isinstance(value, list):
        for item in value:
            validate_json(item, depth + 1)


def rpc_error(request_id: object, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": request_id,
            "error": {"code": code, "message": message}}


class Server:
    def __init__(self, registry: Registry, audit: AuditLog | None = None) -> None:
        self.registry = registry
        self.audit = audit if audit is not None else AuditLog()

    def handle_line(self, line: str) -> dict | None:
        try:
            request = json.loads(line, parse_constant=self._reject_constant)
            validate_json(request)
        except (ValueError, RecursionError):
            return rpc_error(None, -32700, "Parse error")
        return self.handle(request)

    @staticmethod
    def _reject_constant(value: str) -> None:
        raise ValueError(f"Invalid JSON constant: {value}")

    def handle(self, request: object) -> dict | None:
        if not isinstance(request, dict):
            return rpc_error(None, -32600, "Invalid Request")
        try:
            validate_json(request)
        except (ValueError, RecursionError, TypeError):
            return rpc_error(None, -32600, "Invalid Request: invalid Unicode, non-finite number or excessive nesting")
        request_id = request.get("id")
        if (request.get("jsonrpc") != "2.0" or not isinstance(request.get("method"), str)
                or (request_id is not None and type(request_id) not in (str, int, float))):
            return rpc_error(None, -32600, "Invalid Request")
        method = request["method"]
        params = request.get("params", {})
        if method == "tools/call":
            response = self._call(request_id, params)
        elif method not in ("initialize", "tools/list"):
            response = rpc_error(request_id, -32601, "Method not found")
        elif not isinstance(params, dict):
            response = rpc_error(request_id, -32602, "Params must be an object")
        elif method == "initialize":
            response = {"jsonrpc": "2.0", "id": request_id, "result": {
                "protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
                "serverInfo": {"name": "agent-toolgate", "version": "0.1.0"}}}
        else:
            deprecated = params.get("deprecated")
            if deprecated is not None and type(deprecated) is not bool:
                response = rpc_error(request_id, -32602, "deprecated must be boolean")
            else:
                declarations = self.registry.list_tools(deprecated)
                tools = []
                for item in declarations:
                    item["inputSchema"] = item.pop("input_schema")
                    tools.append(item)
                response = {"jsonrpc": "2.0", "id": request_id, "result": {"tools": tools}}
        return response if "id" in request else None

    def _call(self, request_id: object, params: object) -> dict:
        start = perf_counter()
        valid_params = isinstance(params, dict)
        name = params.get("name") if valid_params else None
        arguments = params.get("arguments", {}) if valid_params else params
        if not valid_params or not isinstance(name, str) or not name or not isinstance(arguments, dict):
            response = rpc_error(request_id, -32602, "Expected name and object arguments")
            error_code = -32602
            audit_response = response["error"]
        else:
            result = self.registry.call(name, arguments)
            payload = result["result"] if result["ok"] else result["error"]
            response = {"jsonrpc": "2.0", "id": request_id, "result": {
                "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, allow_nan=False)}],
                "isError": not result["ok"]}}
            error_code = None if result["ok"] else result["error"]["code"]
            audit_response = result
        try:
            manifest = getattr(self.registry, "kb_root", Path(__file__).resolve().parents[1]) / "corpus/manifest.json"
            kb_version = hashlib.sha256(manifest.read_bytes()).hexdigest() if manifest.exists() else None
            self.audit.record(name if isinstance(name, str) else None, arguments,
                              "success" if error_code is None else "error",
                              (perf_counter() - start) * 1000, error_code,
                              response=audit_response,
                              request_id=request_id, kb_version=kb_version)
        except Exception:
            response.get("result", response)["degraded"] = True
            print("WARNING: audit write failed; response degraded", file=sys.stderr)
        return response


def run_demo(server: Server, message: str = "hello harness") -> None:
    requests = [("initialize", {}), ("tools/list", {}),
                ("tools/call", {"name": "echo", "arguments": {"message": message}}),
                ("tools/call", {"name": "kb_search", "arguments": {"query": "混合检索 重排"}})]
    failed = False
    for index, (method, params) in enumerate(requests, 1):
        response = server.handle({"jsonrpc": "2.0", "id": index, "method": method, "params": params})
        print(json.dumps({"method": method, "response": response}, ensure_ascii=False))
        if "error" in response or response.get("result", {}).get("isError", False):
            failed = True
    if failed:
        raise SystemExit("Demo failed: one or more requests returned an error.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--demo", action="store_true")
    args = parser.parse_args()
    server = Server(demo_registry())
    if args.demo:
        run_demo(server)
    else:
        for line in sys.stdin:
            response = server.handle_line(line)
            if response is not None:
                print(json.dumps(response, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
