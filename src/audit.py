"""Append-only call metadata; raw arguments are never persisted."""

import hashlib
import json
from pathlib import Path
from uuid import uuid4


DEFAULT_TRACE = Path(__file__).resolve().parent.parent / "out" / "trace.jsonl"


class AuditLog:
    def __init__(self, path: str | Path = DEFAULT_TRACE) -> None:
        self.path = Path(path)

    def record(self, tool: str | None, arguments: object, status: str,
               duration_ms: float, error_code: str | int | None, *, response: object = None) -> dict:
        canonical = json.dumps(arguments, sort_keys=True, ensure_ascii=False,
                               separators=(",", ":"), allow_nan=False)
        output = json.dumps(response, sort_keys=True, ensure_ascii=False,
                            separators=(",", ":"), allow_nan=False)
        # Offline proxy only: ceil(JSON Unicode characters / 4), not model usage.
        entry = {"trace_id": uuid4().hex, "tool": tool,
                 "args_digest": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
                 "status": status, "duration_ms": round(duration_ms, 3),
                 "error_code": error_code,
                 "prompt_tokens": (len(canonical) + 3) // 4,
                 "completion_tokens": (len(output) + 3) // 4,
                 "usage_estimated": True}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return entry
