"""MCP-style JSON-RPC 2.0, one JSON message per stdio line."""

import argparse
from datetime import datetime, timezone
import json
import sys
from time import perf_counter

from .audit import AuditLog
from .kb.index import KnowledgeIndex
from .registry import Registry, Tool


def kb_search(query: str, limit: int = 3) -> dict:
    with KnowledgeIndex() as index:
        if not index.stats()["documents"]:
            index.rebuild()
        results = index.search(query, limit=limit, mode="hybrid")
    return {"query": query, "results": [
        {key: row[key] for key in ("doc_id", "heading_path", "score", "score_detail")}
        for row in results]}


def demo_registry() -> Registry:
    registry = Registry()
    registry.register(Tool("kb_search", "1.0", "Search the local FTS + vector index with RRF and reranking",
                           {"type": "object", "required": ["query"], "properties": {
                               "query": {"type": "string", "maxLength": 1000},
                               "limit": {"type": "integer", "minimum": 1}}}, False, kb_search))
    registry.register(Tool("echo", "1.0", "Return the supplied message",
                           {"type": "object", "required": ["message"], "properties": {
                               "message": {"type": "string", "maxLength": 2000}}}, False,
                           lambda message: {"message": message}))
    registry.register(Tool("clock", "1.0", "Return the current UTC time",
                           {"type": "object", "properties": {}}, False,
                           lambda: {"utc": datetime.now(timezone.utc).isoformat()}))
    return registry


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
        except (ValueError, RecursionError):
            return rpc_error(None, -32700, "Parse error")
        return self.handle(request)

    @staticmethod
    def _reject_constant(value: str) -> None:
        raise ValueError(f"Invalid JSON constant: {value}")

    def handle(self, request: object) -> dict | None:
        if not isinstance(request, dict):
            return rpc_error(None, -32600, "Invalid Request")
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
        else:
            result = self.registry.call(name, arguments)
            response = {"jsonrpc": "2.0", "id": request_id, "result": result}
            error_code = None if result["ok"] else result["error"]["code"]
        self.audit.record(name if isinstance(name, str) else None, arguments,
                          "success" if error_code is None else "error",
                          (perf_counter() - start) * 1000, error_code,
                          response=response.get("result", response.get("error")))
        return response


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--demo", action="store_true")
    args = parser.parse_args()
    server = Server(demo_registry())
    if args.demo:
        requests = [("initialize", {}), ("tools/list", {}),
                    ("tools/call", {"name": "echo", "arguments": {"message": "hello harness"}}),
                    ("tools/call", {"name": "kb_search", "arguments": {"query": "混合检索 重排"}})]
        for index, (method, params) in enumerate(requests, 1):
            response = server.handle({"jsonrpc": "2.0", "id": index,
                                      "method": method, "params": params})
            print(json.dumps({"method": method, "response": response}, ensure_ascii=False))
    else:
        for line in sys.stdin:
            response = server.handle_line(line)
            if response is not None:
                print(json.dumps(response, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
