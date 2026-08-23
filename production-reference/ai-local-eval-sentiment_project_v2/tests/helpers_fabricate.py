"""Fabricate schema-valid payloads for pydantic models, for offline tests.

Not a test module (no ``test_`` prefix): shared by the prompt-sectioning and
frame-builder tests, both of which need a fully valid ``ModelResponse``-shaped
payload without calling any model.
"""

from __future__ import annotations

import typing
from types import UnionType
from typing import Any, Literal, get_args, get_origin

from pydantic import BaseModel


def value_for(annotation: Any) -> Any:
    """A schema-valid value for one annotation: first Literal, None for optionals."""
    origin = get_origin(annotation)
    if origin is Literal:
        return get_args(annotation)[0]
    if origin is list:
        args = get_args(annotation)
        return [value_for(args[0])] if args else []
    if origin in (UnionType, typing.Union):
        args = get_args(annotation)
        if type(None) in args:
            return None
        return value_for(args[0])
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return fabricate_payload(annotation)
    if annotation is str:
        return "x"
    if annotation is bool:
        return True
    if annotation is int:
        return 1
    if annotation is float:
        return 1.0
    raise TypeError(f"no fabrication rule for annotation {annotation!r}")


def fabricate_payload(model_cls: type[BaseModel]) -> dict[str, Any]:
    """A dict that validates against ``model_cls``, keyed by alias where one exists."""
    return {
        (info.alias or name): value_for(info.annotation)
        for name, info in model_cls.model_fields.items()
    }
