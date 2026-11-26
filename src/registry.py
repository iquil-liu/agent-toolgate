"""Tool declarations and a deliberately small JSON Schema validator."""

from copy import deepcopy
from dataclasses import dataclass
import json
import math
import re
from typing import Any, Callable


def failure(code: str, message: str, path: str = "$", field: str = "") -> dict:
    return {"ok": False, "error": {"code": code, "message": message,
                                    "field": field, "path": path}}


@dataclass(frozen=True)
class Tool:
    name: str
    version: str
    description: str
    input_schema: dict
    deprecated: bool
    handler: Callable[..., Any]

    def declaration(self) -> dict:
        return {"name": self.name, "version": self.version,
                "description": self.description,
                "input_schema": deepcopy(self.input_schema),
                "deprecated": self.deprecated}


def _validate(value: Any, schema: dict, path: str = "$", field: str = "") -> dict | None:
    kind = schema.get("type")
    matches = {
        "object": isinstance(value, dict),
        "string": isinstance(value, str),
        "integer": type(value) is int,
        "number": type(value) in (int, float),
        "boolean": type(value) is bool,
        "array": isinstance(value, list),
    }
    if kind is not None and not matches[kind]:
        return failure("SCHEMA_VIOLATION", f"Expected {kind}", path, field)
    if type(value) is float and not math.isfinite(value):
        return failure("SCHEMA_VIOLATION", "Expected a finite number", path, field)
    if "enum" in schema and not any(
        value == item and (isinstance(value, bool) == isinstance(item, bool))
        for item in schema["enum"]
    ):
        return failure("SCHEMA_VIOLATION", "Value is not in enum", path, field)
    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                return failure("SCHEMA_VIOLATION", "Required field is missing",
                               f"{path}.{key}", key)
        for key, child_schema in schema.get("properties", {}).items():
            if key in value:
                error = _validate(value[key], child_schema, f"{path}.{key}", key)
                if error:
                    return error
    if isinstance(value, list) and "items" in schema:
        for index, item in enumerate(value):
            error = _validate(item, schema["items"], f"{path}[{index}]", field)
            if error:
                return error
    if isinstance(value, str):
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            return failure("SCHEMA_VIOLATION", "String exceeds maxLength", path, field)
        if "pattern" in schema and re.search(schema["pattern"], value) is None:
            return failure("SCHEMA_VIOLATION", "String does not match pattern", path, field)
    if type(value) in (int, float):
        for bound, violates in (("minimum", lambda x: value < x),
                                ("maximum", lambda x: value > x)):
            if bound in schema and violates(schema[bound]):
                return failure("SCHEMA_VIOLATION", f"Number violates {bound}", path, field)
    return None


class Registry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Duplicate tool: {tool.name}")
        if not tool.name or not tool.version or not callable(tool.handler):
            raise ValueError("Tool requires name, version and callable handler")
        if tool.input_schema.get("type") != "object":
            raise ValueError("Tool input schema must have type object")
        # Own the schema so callers cannot mutate a registered contract.
        self._tools[tool.name] = deepcopy(tool)

    def get(self, name: str) -> Tool | None:
        tool = self._tools.get(name)
        return deepcopy(tool) if tool else None

    def list_tools(self, deprecated: bool | None = None) -> list[dict]:
        return [tool.declaration() for tool in self._tools.values()
                if deprecated is None or tool.deprecated == deprecated]

    def call(self, name: str, arguments: Any) -> dict:
        if not isinstance(name, str) or not name:
            return failure("INVALID_ARGUMENT", "Tool name must be a nonempty string", "$.name", "name")
        tool = self._tools.get(name)
        if tool is None:
            return failure("UNKNOWN_TOOL", "Tool is not registered", "$.name", "name")
        if tool.deprecated:
            return failure("TOOL_DEPRECATED", "Tool is deprecated", "$.name", "name")
        if not isinstance(arguments, dict):
            return failure("INVALID_ARGUMENT", "Arguments must be an object", "$.arguments", "arguments")
        error = _validate(arguments, tool.input_schema)
        if error:
            return error
        try:
            result = tool.handler(**arguments)
            json.dumps(result, allow_nan=False)
            return {"ok": True, "result": result}
        except Exception:
            # Exception messages can contain credentials or raw arguments.
            return failure("HANDLER_ERROR", "Tool handler failed")
