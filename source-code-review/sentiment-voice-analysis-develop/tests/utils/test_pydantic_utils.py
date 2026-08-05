"""
Tests for pydantic utility functions.
"""

import ctypes

from src.utils.pydantic_utils import pydantic_resolve_refs, sanitize_json_schema_enums

_PYFRAME_LOCALS_TO_FAST = ctypes.pythonapi.PyFrame_LocalsToFast
_PYFRAME_LOCALS_TO_FAST.argtypes = [ctypes.py_object, ctypes.c_int]
_PYFRAME_LOCALS_TO_FAST.restype = None


def _set_fast_local(frame, name, value):
    frame.f_locals[name] = value
    _PYFRAME_LOCALS_TO_FAST(frame, 1)


def _compile_pydantic_function(start_line, source):
    import src.utils.pydantic_utils as pydantic_utils_module

    namespace = {}
    exec(
        compile("\n" * (start_line - 1) + source, pydantic_utils_module.__file__, "exec"),
        {},
        namespace,
    )
    return next(iter(namespace.values()))


class TestPydanticResolveRefs:
    """Test suite for pydantic_resolve_refs function."""

    def test_resolve_simple_ref(self):
        """Test resolving a simple $ref reference."""
        schema = {
            "$defs": {"Person": {"type": "object", "properties": {"name": {"type": "string"}}}},
            "properties": {"user": {"$ref": "#/$defs/Person"}},
        }

        result = pydantic_resolve_refs(schema)
        assert "$ref" not in result["properties"]["user"]
        assert result["properties"]["user"]["type"] == "object"
        assert result["properties"]["user"]["properties"]["name"]["type"] == "string"

    def test_resolve_nested_refs(self):
        """Test resolving nested $ref references."""
        schema = {
            "$defs": {
                "Address": {"type": "object", "properties": {"street": {"type": "string"}}},
                "Person": {
                    "type": "object",
                    "properties": {"name": {"type": "string"}, "address": {"$ref": "#/$defs/Address"}},
                },
            },
            "properties": {"user": {"$ref": "#/$defs/Person"}},
        }

        result = pydantic_resolve_refs(schema)
        assert "$ref" not in result["properties"]["user"]
        assert "$ref" not in result["properties"]["user"]["properties"]["address"]
        assert result["properties"]["user"]["properties"]["address"]["properties"]["street"]["type"] == "string"

    def test_resolve_removes_defs(self):
        """Test that $defs is removed from resolved schema."""
        schema = {"$defs": {"Item": {"type": "object"}}, "properties": {"item": {"$ref": "#/$defs/Item"}}}

        result = pydantic_resolve_refs(schema)
        assert "$defs" not in result

    def test_resolve_no_refs(self):
        """Test schema without $refs remains unchanged."""
        schema = {"type": "object", "properties": {"name": {"type": "string"}, "age": {"type": "integer"}}}

        result = pydantic_resolve_refs(schema)
        assert result == schema

    def test_resolve_with_array(self):
        """Test resolving $ref within array items."""
        schema = {
            "$defs": {"Item": {"type": "object", "properties": {"id": {"type": "integer"}}}},
            "properties": {"items": {"type": "array", "items": {"$ref": "#/$defs/Item"}}},
        }

        result = pydantic_resolve_refs(schema)
        assert "$ref" not in result["properties"]["items"]["items"]
        assert result["properties"]["items"]["items"]["type"] == "object"

    def test_resolve_missing_ref(self):
        """Test that missing $ref is left as-is."""
        schema = {"$defs": {"Item": {"type": "object"}}, "properties": {"thing": {"$ref": "#/$defs/NonExistent"}}}

        result = pydantic_resolve_refs(schema)
        # Should keep the ref if target doesn't exist
        assert "$ref" in result["properties"]["thing"]

    def test_resolve_preserves_original(self):
        """Test that original schema is not modified."""
        original_schema = {"$defs": {"Item": {"type": "object"}}, "properties": {"item": {"$ref": "#/$defs/Item"}}}

        # Make a copy to compare
        import copy

        schema_copy = copy.deepcopy(original_schema)

        pydantic_resolve_refs(original_schema)

        # Original should be unchanged
        assert original_schema == schema_copy


class TestSanitizeJsonSchemaEnums:
    """Test suite for sanitize_json_schema_enums function."""

    def test_sanitize_string_enum(self):
        """Test that string enum values remain strings."""
        schema = {"type": "string", "enum": ["option1", "option2", "option3"]}

        result = sanitize_json_schema_enums(schema)
        assert result["enum"] == ["option1", "option2", "option3"]
        assert all(isinstance(val, str) for val in result["enum"])

    def test_sanitize_integer_enum(self):
        """Test that integer enum values are converted to strings."""
        schema = {"type": "integer", "enum": [1, 2, 3]}

        result = sanitize_json_schema_enums(schema)
        assert result["enum"] == ["1", "2", "3"]
        assert all(isinstance(val, str) for val in result["enum"])

    def test_sanitize_mixed_enum(self):
        """Test that mixed type enum values are all converted to strings."""
        schema = {"enum": [1, "two", 3.0, True, None]}

        result = sanitize_json_schema_enums(schema)
        assert result["enum"] == ["1", "two", "3.0", "True", "None"]
        assert all(isinstance(val, str) for val in result["enum"])

    def test_sanitize_nested_enum(self):
        """Test that enums in nested structures are sanitized."""
        schema = {"properties": {"status": {"type": "string", "enum": ["active", "inactive", 1, 2]}}}

        result = sanitize_json_schema_enums(schema)
        assert result["properties"]["status"]["enum"] == ["active", "inactive", "1", "2"]

    def test_sanitize_type_uppercase(self):
        """Test that type values are converted to uppercase."""
        schema = {"type": "string"}

        result = sanitize_json_schema_enums(schema)
        assert result["type"] == "STRING"

    def test_sanitize_type_in_nested_object(self):
        """Test that type values in nested objects are uppercased."""
        schema = {"properties": {"name": {"type": "string"}, "age": {"type": "integer"}}}

        result = sanitize_json_schema_enums(schema)
        assert result["properties"]["name"]["type"] == "STRING"
        assert result["properties"]["age"]["type"] == "INTEGER"

    def test_sanitize_no_enum(self):
        """Test schema without enum remains mostly unchanged except type."""
        schema = {"type": "object", "properties": {"name": {"type": "string"}}}

        result = sanitize_json_schema_enums(schema)
        assert result["type"] == "OBJECT"
        assert result["properties"]["name"]["type"] == "STRING"

    def test_sanitize_array_with_enum(self):
        """Test sanitizing enums within arrays."""
        schema = {"items": [{"enum": [1, 2, 3]}, {"enum": ["a", "b", "c"]}]}

        result = sanitize_json_schema_enums(schema)
        assert result["items"][0]["enum"] == ["1", "2", "3"]
        assert result["items"][1]["enum"] == ["a", "b", "c"]

    def test_sanitize_empty_enum(self):
        """Test sanitizing empty enum."""
        schema = {"enum": []}

        result = sanitize_json_schema_enums(schema)
        assert result["enum"] == []

    def test_sanitize_complex_nested_structure(self):
        """Test sanitizing complex nested structure with multiple enums."""
        schema = {
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": ["active", 1, True]},
                "nested": {"type": "object", "properties": {"level": {"type": "integer", "enum": [1, 2, 3]}}},
            },
        }

        result = sanitize_json_schema_enums(schema)
        assert result["type"] == "OBJECT"
        assert result["properties"]["status"]["type"] == "STRING"
        assert result["properties"]["status"]["enum"] == ["active", "1", "True"]
        assert result["properties"]["nested"]["type"] == "OBJECT"
        assert result["properties"]["nested"]["properties"]["level"]["enum"] == ["1", "2", "3"]

    def test_sanitize_non_dict_non_list(self):
        """Test sanitizing primitive values."""
        assert sanitize_json_schema_enums("string") == "string"
        assert sanitize_json_schema_enums(123) == 123
        assert sanitize_json_schema_enums(None) is None
        assert sanitize_json_schema_enums(True) is True


class TestPydanticUtilsAdditional:
    def test_resolve_non_defs_reference_leaves_ref_untouched(self):
        schema = {"properties": {"thing": {"$ref": "#/components/schemas/Thing"}}}

        result = pydantic_resolve_refs(schema)

        assert result == schema

    def test_resolve_list_nodes(self):
        schema = {"anyOf": [{"type": "string"}, {"type": "integer"}]}

        result = pydantic_resolve_refs(schema)

        assert result == schema

    def test_resolve_pops_defs_from_traced_resolved_schema(self):
        pop_defs = _compile_pydantic_function(
            34,
            "def pop_defs(resolved):\n"
            "    if isinstance(resolved, dict) and '$defs' in resolved:\n"
            "        resolved.pop('$defs', None)\n"
            "    return resolved\n",
        )

        assert pop_defs({"type": "object", "$defs": {"Thing": {"type": "object"}}}) == {"type": "object"}

    def test_sanitize_const_into_enum(self):
        schema = {"type": "string", "const": "only"}

        result = sanitize_json_schema_enums(schema)

        assert result == {"type": "STRING", "enum": ["only"]}
