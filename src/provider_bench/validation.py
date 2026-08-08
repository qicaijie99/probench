from __future__ import annotations

from typing import Any

from jsonschema import Draft202012Validator, SchemaError


def validate_schema_definition(schema: dict[str, Any]) -> None:
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        raise ValueError(f"invalid JSON Schema: {exc.message}") from exc


def validate_json_schema(instance: Any, schema: dict[str, Any]) -> list[str]:
    """Return concise JSON Schema violations without throwing on response data."""
    try:
        validate_schema_definition(schema)
        validator = Draft202012Validator(schema)
    except ValueError as exc:
        return [str(exc)]
    errors = sorted(validator.iter_errors(instance), key=lambda error: list(error.absolute_path))
    messages = []
    for error in errors:
        path = ".".join(str(part) for part in error.absolute_path) or "$"
        messages.append(f"{path}: {error.message}")
    return messages
