"""Register your own tool and drive it through the JSON-RPC server.

Run from the repository root:  python3 examples/custom_tool.py
"""

import json
from pathlib import Path
import sys
import tempfile

# Make the repository importable when this file is executed directly.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.audit import AuditLog
from src.mcp_server import Server
from src.registry import Registry, Tool


def word_count(text: str) -> dict:
    return {"words": len(text.split()), "chars": len(text)}


registry = Registry()
registry.register(Tool(
    name="word_count",
    version="1.0",
    description="Count whitespace-separated words and characters",
    input_schema={
        "type": "object",
        "required": ["text"],
        "properties": {"text": {"type": "string", "maxLength": 5000}},
    },
    deprecated=False,
    handler=word_count,
))

server = Server(registry, AuditLog(Path(tempfile.gettempdir()) / "toolgate-example-trace.jsonl"))

requests = [
    {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
    {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
     "params": {"name": "word_count", "arguments": {"text": "agent tool contracts"}}},
    # Missing the required "text" field: the registry rejects it before any handler runs.
    {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
     "params": {"name": "word_count", "arguments": {}}},
]

for request in requests:
    print(json.dumps(server.handle(request), ensure_ascii=False, indent=2))
