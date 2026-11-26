import unittest

from src.registry import Registry, Tool


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.registry = Registry()
        self.schema = {"type": "object", "required": ["name", "count"], "properties": {
            "name": {"type": "string", "pattern": "^[a-z]+$", "maxLength": 5},
            "count": {"type": "integer", "minimum": 1, "maximum": 10},
            "ratio": {"type": "number"}, "enabled": {"type": "boolean"},
            "tags": {"type": "array", "items": {"type": "string"}},
            "mode": {"enum": ["fast", "safe"]},
            "options": {"type": "object", "required": ["key"],
                        "properties": {"key": {"type": "string"}}}}}
        self.registry.register(Tool("sample", "1.0", "Test tool", self.schema, False,
                                    lambda **kwargs: kwargs))

    def call(self, **changes):
        return self.registry.call("sample", {"name": "valid", "count": 2, **changes})

    def assert_error(self, result, code, path=None):
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], code)
        self.assertEqual(set(result["error"]), {"code", "message", "field", "path"})
        if path:
            self.assertEqual(result["error"]["path"], path)

    def test_valid(self):
        result = self.call(ratio=1.5, enabled=True, tags=["a"], mode="safe", options={"key": "v"})
        self.assertTrue(result["ok"])
        self.assertEqual(result["result"]["count"], 2)

    def test_required(self):
        result = self.registry.call("sample", {"name": "valid"})
        self.assert_error(result, "SCHEMA_VIOLATION", "$.count")
        self.assertEqual(result["error"]["field"], "count")

    def test_type_and_boolean_is_not_integer(self):
        for count in ("2", True, 2.0, None):
            with self.subTest(count=count):
                self.assert_error(self.call(count=count), "SCHEMA_VIOLATION", "$.count")

    def test_numeric_bounds_inclusive(self):
        for count in (0, 11):
            self.assert_error(self.call(count=count), "SCHEMA_VIOLATION")
        for count in (1, 10):
            self.assertTrue(self.call(count=count)["ok"])

    def test_pattern(self):
        self.assert_error(self.call(name="BAD"), "SCHEMA_VIOLATION", "$.name")

    def test_max_length(self):
        self.assert_error(self.call(name="abcdef"), "SCHEMA_VIOLATION", "$.name")

    def test_enum(self):
        self.assert_error(self.call(mode="other"), "SCHEMA_VIOLATION", "$.mode")

    def test_remaining_types(self):
        for field, value in (("ratio", True), ("enabled", 1), ("tags", {}), ("options", [])):
            with self.subTest(field=field):
                self.assert_error(self.call(**{field: value}), "SCHEMA_VIOLATION", f"$.{field}")

    def test_nested_path(self):
        self.assert_error(self.call(options={}), "SCHEMA_VIOLATION", "$.options.key")
        self.assert_error(self.call(tags=["ok", 1]), "SCHEMA_VIOLATION", "$.tags[1]")

    def test_additional_properties_rejected_before_handler_when_disabled(self):
        schema = {"type": "object", "additionalProperties": False, "properties": {
            "options": {"type": "object", "additionalProperties": False,
                        "properties": {"key": {"type": "string"}}}}}
        self.registry.register(Tool("strict", "1", "Strict inputs", schema, False,
                                    lambda **kwargs: self.fail("Invalid handler ran")))
        for arguments, path in (({"root": "other"}, "$.root"),
                                ({"options": {"root": "other"}}, "$.options.root")):
            with self.subTest(path=path):
                self.assert_error(self.registry.call("strict", arguments), "SCHEMA_VIOLATION", path)

    def test_additional_properties_remain_allowed_by_default(self):
        result = self.call(extra="preserved")
        self.assertTrue(result["ok"])
        self.assertEqual(result["result"]["extra"], "preserved")

    def test_unknown_tool(self):
        self.assert_error(self.registry.call("absent", {}), "UNKNOWN_TOOL")

    def test_invalid_arguments(self):
        self.assert_error(self.registry.call("sample", []), "INVALID_ARGUMENT")
        self.assert_error(self.registry.call([], {}), "INVALID_ARGUMENT")

    def test_deprecated_and_filter(self):
        self.registry.register(Tool("old", "0.1", "Old", {"type": "object"}, True,
                                    lambda: self.fail("Deprecated handler ran")))
        self.assert_error(self.registry.call("old", {}), "TOOL_DEPRECATED")
        self.assertEqual(len(self.registry.list_tools()), 2)
        self.assertEqual([t["name"] for t in self.registry.list_tools(True)], ["old"])
        self.assertEqual([t["name"] for t in self.registry.list_tools(False)], ["sample"])

    def test_handler_error_sanitized(self):
        def broken():
            raise RuntimeError("private-input")
        self.registry.register(Tool("broken", "1", "Broken", {"type": "object"}, False, broken))
        result = self.registry.call("broken", {})
        self.assert_error(result, "HANDLER_ERROR")
        self.assertNotIn("private-input", str(result))

    def test_non_json_handler_result(self):
        self.registry.register(Tool("bad", "1", "Bad output", {"type": "object"}, False, lambda: {1, 2}))
        self.assert_error(self.registry.call("bad", {}), "HANDLER_ERROR")

    def test_lookup_schema_isolation_and_duplicates(self):
        self.schema["required"].append("unexpected")
        tool = self.registry.get("sample")
        self.assertEqual(tool.version, "1.0")
        tool.input_schema["required"].append("mutated")
        self.assertTrue(self.call()["ok"])
        self.assertIsNone(self.registry.get("absent"))
        with self.assertRaises(ValueError):
            self.registry.register(tool)


if __name__ == "__main__":
    unittest.main()
