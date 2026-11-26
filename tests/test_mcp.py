import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from src.audit import AuditLog
from src.mcp_server import Server, demo_registry
from src.registry import Tool


class MCPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "nested" / "trace.jsonl"
        self.server = Server(demo_registry(), AuditLog(self.path))

    def request(self, method, params=None, request_id=1):
        return self.server.handle_line(json.dumps({"jsonrpc": "2.0", "id": request_id,
                                                  "method": method, "params": params or {}}))

    def records(self):
        return [json.loads(line) for line in self.path.read_text().splitlines()]

    def test_initialize(self):
        response = self.request("initialize", request_id="init")
        self.assertEqual(response["id"], "init")
        self.assertEqual(response["result"]["capabilities"], {"tools": {}})

    def test_list(self):
        tools = self.request("tools/list")["result"]["tools"]
        self.assertEqual({t["name"] for t in tools}, {"kb_search", "echo", "clock"})
        self.assertTrue(all("inputSchema" in t and "handler" not in t for t in tools))

    def test_call_and_audit_privacy(self):
        response = self.request("tools/call", {"name": "echo", "arguments": {"message": "private text"}})
        self.assertEqual(response["result"], {"ok": True, "result": {"message": "private text"}})
        record = self.records()[0]
        self.assertEqual(set(record), {"trace_id", "tool", "args_digest", "status", "duration_ms", "error_code",
                                       "prompt_tokens", "completion_tokens", "usage_estimated"})
        self.assertIs(record["usage_estimated"], True)
        self.assertEqual(record["status"], "success")
        self.assertIsNone(record["error_code"])
        self.assertGreaterEqual(record["duration_ms"], 0)
        self.assertEqual(len(record["trace_id"]), 32)
        self.assertEqual(len(record["args_digest"]), 64)
        self.assertNotIn("private text", self.path.read_text())

    def test_parse_error_and_invalid_json_constants(self):
        for line in ('{', '{"x": NaN}', '{"x": Infinity}'):
            self.assertEqual(self.server.handle_line(line)["error"]["code"], -32700)

    def test_method_not_found(self):
        self.assertEqual(self.request("missing")["error"]["code"], -32601)

    def test_invalid_request(self):
        for request in ([], None, {"method": "initialize"},
                        {"jsonrpc": "2.0", "method": "initialize", "id": True}):
            self.assertEqual(self.server.handle(request)["error"]["code"], -32600)

    def test_invalid_params_are_audited(self):
        for params in ([], {"name": 3}, {"name": "echo", "arguments": []}):
            response = self.server.handle({"jsonrpc": "2.0", "id": 1,
                                           "method": "tools/call", "params": params})
            self.assertEqual(response["error"]["code"], -32602)
        self.assertEqual(len(self.records()), 3)
        self.assertTrue(all(r["error_code"] == -32602 for r in self.records()))
        self.assertEqual(self.request("tools/list", {"deprecated": "yes"})["error"]["code"], -32602)

    def test_tool_errors_are_structured_and_audited(self):
        for name, arguments, expected in (("missing", {}, "UNKNOWN_TOOL"),
                                           ("echo", {}, "SCHEMA_VIOLATION")):
            response = self.request("tools/call", {"name": name, "arguments": arguments})
            self.assertEqual(response["result"]["error"]["code"], expected)
            self.assertEqual(self.records()[-1]["error_code"], expected)
            self.assertEqual(self.records()[-1]["status"], "error")

    def test_handler_error_and_deprecated_audit(self):
        def broken():
            raise ValueError("secret")
        for name, deprecated, handler, code in (("old", True, lambda: None, "TOOL_DEPRECATED"),
                                                ("broken", False, broken, "HANDLER_ERROR")):
            self.server.registry.register(Tool(name, "1", "Test", {"type": "object"}, deprecated, handler))
            self.request("tools/call", {"name": name})
            self.assertEqual(self.records()[-1]["error_code"], code)

    def test_notification_has_no_response_but_is_audited(self):
        self.assertIsNone(self.server.handle({"jsonrpc": "2.0", "method": "tools/call",
                                              "params": {"name": "clock"}}))
        self.assertEqual(self.records()[0]["tool"], "clock")

    def test_digest_is_canonical_and_append_preserves_records(self):
        audit = self.server.audit
        first = audit.record("tool", {"b": 2, "a": "文"}, "success", 0.5, None)
        second = audit.record("tool", {"a": "文", "b": 2}, "success", 0.5, None)
        expected = hashlib.sha256('{"a":"文","b":2}'.encode()).hexdigest()
        self.assertEqual(first["args_digest"], expected)
        self.assertEqual(first["args_digest"], second["args_digest"])
        self.assertNotEqual(first["trace_id"], second["trace_id"])
        self.assertEqual(len(self.records()), 2)

    def test_demo_tools_are_callable(self):
        query = "混合检索 重排"
        kb = self.request("tools/call", {"name": "kb_search", "arguments": {"query": query}})
        self.assertTrue(kb["result"]["ok"])
        self.assertNotIn("status", kb["result"]["result"])
        cli = subprocess.run([sys.executable, "-S", "-m", "src.kb.cli", "search", query],
                             capture_output=True, text=True, check=True,
                             cwd=Path(__file__).resolve().parents[1], timeout=10)
        results = kb["result"]["result"]["results"]
        self.assertEqual(len(results), 3)
        self.assertEqual([row["doc_id"] for row in results],
                         [row["doc_id"] for row in json.loads(cli.stdout)])
        clock = self.request("tools/call", {"name": "clock"})
        self.assertTrue(clock["result"]["result"]["utc"].endswith("+00:00"))

    def test_real_stdio_recovers_after_parse_error(self):
        payload = '{bad\n' + json.dumps({"jsonrpc": "2.0", "id": "next", "method": "tools/list"}) + '\n'
        completed = subprocess.run([sys.executable, "-m", "src.mcp_server"],
                                   input=payload, capture_output=True, text=True,
                                   cwd=Path(__file__).resolve().parents[1], timeout=10)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")
        responses = [json.loads(line) for line in completed.stdout.splitlines()]
        self.assertEqual(len(responses), 2)
        self.assertEqual(responses[0]["error"]["code"], -32700)
        self.assertEqual(responses[1]["id"], "next")
        self.assertEqual(len(responses[1]["result"]["tools"]), 3)


if __name__ == "__main__":
    unittest.main()
