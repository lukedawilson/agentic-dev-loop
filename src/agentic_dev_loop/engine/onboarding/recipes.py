"""Per-toolchain recipes: gate shape and command vocabulary.

A recipe is engine-resident data. ``init`` resolves its ``__TOKEN__``
placeholders from detection and questions and renders a pipeline that passes
``load_config`` validation by construction.
"""

from __future__ import annotations

import re
from importlib import resources
from pathlib import Path

import yaml

__all__ = [
    "RecipeError",
    "bundled_recipe_names",
    "load_recipe",
    "undeclared_tokens",
]

_TOKEN_RE = re.compile(r"__[A-Z][A-Z0-9_]*__")


class RecipeError(ValueError):
    """Any recipe load or shape failure."""


def _recipes_dir():
    return resources.files(__package__).joinpath("recipes")


def bundled_recipe_names() -> list[str]:
    return sorted(
        entry.name[: -len(".yaml")]
        for entry in _recipes_dir().iterdir()
        if entry.name.endswith(".yaml")
    )


def load_recipe(name: str) -> dict:
    """Load a bundled recipe by name or a consumer recipe by path.

    Existence decides: a filesystem path that exists is read as a consumer
    recipe; anything else must name a bundled recipe.
    """
    candidate = Path(name)
    if candidate.is_file():
        source = name
        try:
            doc = yaml.safe_load(candidate.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise RecipeError(f"recipe {source!r}: invalid YAML: {exc}") from exc
    else:
        names = bundled_recipe_names()
        if name not in names:
            raise RecipeError(
                f"unknown recipe '{name}'; bundled recipes: " + ", ".join(names))
        source = name
        entry = _recipes_dir().joinpath(f"{name}.yaml")
        try:
            doc = yaml.safe_load(entry.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise RecipeError(f"recipe {source!r}: invalid YAML: {exc}") from exc
    if not isinstance(doc, dict):
        raise RecipeError(f"recipe {source!r} must be a mapping")
    if not isinstance(doc.get("steps"), list) or not doc["steps"]:
        raise RecipeError(
            f"recipe {source!r} must declare a non-empty steps list")
    if not isinstance(doc.get("placeholders", {}), dict):
        raise RecipeError(f"recipe {source!r}: placeholders must be a mapping")
    return doc


def _walk_tokens(node) -> set[str]:
    found: set[str] = set()
    if isinstance(node, dict):
        for key, value in node.items():
            found |= _walk_tokens(key)
            found |= _walk_tokens(value)
    elif isinstance(node, (list, tuple)):
        for item in node:
            found |= _walk_tokens(item)
    elif isinstance(node, str):
        found |= set(_TOKEN_RE.findall(node))
    return found


def undeclared_tokens(doc: dict) -> set[str]:
    """Tokens used anywhere in the recipe but absent from ``placeholders``."""
    declared = set(doc.get("placeholders") or {})
    return _walk_tokens(doc) - declared
