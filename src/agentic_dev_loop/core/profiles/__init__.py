"""Toolchain profiles: bundled data plus the load-time merge into a pipeline.

A profile supplies the environment facts every app on a toolchain shares
(isolation image, caches, premise files, gitignore lines, doctor probes).
Profiles never carry commands, gates, or steps - those live in
engine-resident recipes. The subpackage is intentionally dependency-light
(stdlib + pydantic + PyYAML) so ``core`` can resolve a profile at config
load time without importing the engine.
"""

from __future__ import annotations

import re
from importlib import resources
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "PremiseFile",
    "Profile",
    "ProfileError",
    "ProfileIsolation",
    "bundled_profile_names",
    "check_engine_min",
    "load_profile",
    "merge_profile",
    "parse_version",
]

_VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:(a|b|rc)(\d+))?$")
_RANK = {"a": 0, "b": 1, "rc": 2}
_FINAL = 3


class ProfileError(ValueError):
    """Any profile load, parse, or compatibility failure."""


class PremiseFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    content: str


class ProfileIsolation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    image: str
    seccomp_profile: str | None = None
    cpus: int


class Profile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    engine_min: str
    description: str = ""
    isolation: ProfileIsolation
    caches: list[str] = Field(default_factory=list)
    premise_files: list[PremiseFile] = Field(default_factory=list)
    gitignore_additions: list[str] = Field(default_factory=list)
    doctor_checks: list = Field(default_factory=list)


def parse_version(text: str) -> tuple[int, int, int, int, int]:
    """Parse ``X.Y.Z[a|b|rcN]`` into a directly comparable 5-tuple.

    Rank ordering is alpha < beta < rc < final; the prerelease number is 0
    for a final release so ``1.0.0rc9 < 1.0.0``.
    """
    match = _VERSION_RE.match(text.strip())
    if match is None:
        raise ProfileError(f"unparseable version: {text!r}")
    major, minor, patch, pre, num = match.groups()
    if pre is None:
        return (int(major), int(minor), int(patch), _FINAL, 0)
    return (int(major), int(minor), int(patch), _RANK[pre], int(num))


def check_engine_min(engine_min: str, installed: str) -> None:
    """Raise unless ``installed`` satisfies ``engine_min``.

    The fix line names the upgrade command: it installs the latest release,
    which always satisfies the floor.
    """
    if parse_version(installed) < parse_version(engine_min):
        raise ProfileError(
            f"profile requires engine >= {engine_min}; installed {installed}. "
            "Upgrade: run agentic-dev-loop upgrade")


def _data_dir():
    return resources.files(__package__).joinpath("data")


def bundled_profile_names() -> list[str]:
    return sorted(
        entry.name[: -len(".yaml")]
        for entry in _data_dir().iterdir()
        if entry.name.endswith(".yaml")
    )


def _read_yaml(text: str, source: str) -> dict:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ProfileError(f"profile {source!r}: invalid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise ProfileError(f"profile {source!r} must be a mapping")
    return data


def _validate(data: dict, source: str) -> Profile:
    try:
        return Profile.model_validate(data)
    except Exception as exc:
        raise ProfileError(f"profile {source!r}: {exc}") from exc


def load_profile(reference: str, installed_version: str) -> Profile:
    """Load a bundled profile by name or a consumer profile by path.

    Existence decides: a filesystem path that exists is read as a consumer
    profile; anything else must name a bundled profile.
    """
    candidate = Path(reference)
    if candidate.is_file():
        data = _read_yaml(candidate.read_text(encoding="utf-8"), reference)
        profile = _validate(data, reference)
    else:
        names = bundled_profile_names()
        if reference not in names:
            raise ProfileError(
                f"unknown profile '{reference}'; bundled profiles: "
                + ", ".join(names))
        entry = _data_dir().joinpath(f"{reference}.yaml")
        data = _read_yaml(entry.read_text(encoding="utf-8"), reference)
        if data.get("name") != reference:
            raise ProfileError(
                f"profile {reference!r}: declared name {data.get('name')!r} "
                f"does not match its filename")
        profile = _validate(data, reference)
    check_engine_min(profile.engine_min, installed_version)
    return profile


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def merge_profile(data: dict, profile: Profile) -> dict:
    """Merge a profile into a pipeline dict; the pipeline wins.

    Isolation merges key-wise (profile under, pipeline over). Caches union
    profile-first, dedupe-preserving-order. Every other key is untouched.
    """
    merged = dict(data)
    profile_iso = profile.isolation.model_dump(exclude_none=True)
    pipeline_iso = merged.get("isolation") or {}
    if not isinstance(pipeline_iso, dict):
        pipeline_iso = {}
    merged["isolation"] = {**profile_iso, **pipeline_iso}
    caches = _dedupe(list(profile.caches) + list(merged.get("caches") or []))
    merged["caches"] = caches
    return merged
