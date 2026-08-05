import copy
from typing import Any


def pydantic_resolve_refs(schema: dict) -> dict:
    """
    Resolve $ref references in a Pydantic JSON schema
    Parameters:
        schema (dict): The JSON schema with potential $ref references.
    Returns:
        dict: The JSON schema with all $ref references resolved.
    """
    schema = copy.deepcopy(schema)
    defs = schema.get("$defs", {})

    def _resolve(node):
        if isinstance(node, dict):
            # If this node is a $ref, inline the referenced definition
            if "$ref" in node:
                ref = node["$ref"]
                if isinstance(ref, str) and ref.startswith("#/$defs/"):
                    key = ref.split("/")[-1]
                    target = defs.get(key)
                    if target is None:
                        return node
                    return _resolve(copy.deepcopy(target))
                return node

            # Otherwise recursively resolve children
            return {k: _resolve(v) for k, v in node.items() if k != "$defs"}
        if isinstance(node, list):
            return [_resolve(i) for i in node]
        return node

    resolved = _resolve(schema)
    if isinstance(resolved, dict) and "$defs" in resolved:
        resolved.pop("$defs", None)
    return resolved


def sanitize_json_schema_enums(schema: Any) -> Any:
    """
    Sanitize JSON schema to ensure all enum values are strings and convert 'const' to 'enum'.
    Google Vertex AI requires enum values to be strings and doesn't support 'const' field.
    Parameters:
        schema (Any): The JSON schema/dict to sanitize.
    Returns:
        Any: The sanitized schema.
    """
    if isinstance(schema, dict):
        new_schema = {}
        for k, v in schema.items():
            if k == "type":
                v = v.upper() if isinstance(v, str) else v

            if k == "enum" and isinstance(v, list):
                # Convert all enum values to string
                new_schema[k] = [str(item) for item in v]
            elif k == "const":
                # Convert 'const' to single-value 'enum' (Vertex AI doesn't support 'const')
                new_schema["enum"] = [str(v)]
            else:
                new_schema[k] = sanitize_json_schema_enums(v)
        return new_schema
    if isinstance(schema, list):
        return [sanitize_json_schema_enums(item) for item in schema]
    return schema
