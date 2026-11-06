"""Host-side secret store (unit 019): provider registry, run-start resolution,
the ``.env*`` ban, and output scrubbing.

Secrets are declared in the pipeline yaml as refs only - never values. At run
start the engine resolves every declared ref host-side (``env`` provider
first) and injects each value into a declaring step's exec env only. The
``.env*`` ban keeps secret-bearing files out of the workspace, and the
scrubber keeps resolved values out of every engine-written output path.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Protocol

from agentic_dev_loop.core.config import PipelineConfig, SecretSpec, StepConfig

REDACTED = "[REDACTED]"
ENV_FILE_WHITELIST = frozenset({".env.example", ".env.template", ".env.sample"})


class SecretResolutionError(RuntimeError):
    """A loud fail-fast secret failure naming the ref, step(s), and provider."""


class SecretProvider(Protocol):
    """A provider resolves locator keys to values, host-side, once per run.
    ``selectors`` optionally narrows a locator (the file provider's JSON key)."""

    name: str

    def resolve(self, keys: list[str],
                selectors: Mapping[str, str | None] | None = None
                ) -> dict[str, str]:
        """Resolve each locator key; a missing key raises loudly."""
        ...


class EnvProvider:
    """Resolve secrets from host environment variables (the shipped default).
    ``key`` is both the host variable to read and the step env name to inject."""

    name = "env"

    def __init__(self, environ: Mapping[str, str] | None = None):
        self._environ = os.environ if environ is None else environ

    def resolve(self, keys: list[str],
                selectors: Mapping[str, str | None] | None = None
                ) -> dict[str, str]:
        missing = sorted(k for k in keys if k not in self._environ)
        if missing:
            raise SecretResolutionError(
                "environment variable(s) not set: " + ", ".join(missing))
        return {k: self._environ[k] for k in keys}


class FileProvider:
    """Resolve secrets from a host file (058). ``key`` is the file path and
    ``json_key`` selects one string field; no selector means the whole file
    content (surrounding whitespace stripped). Values never enter yaml."""

    name = "file"

    def resolve(self, keys: list[str],
                selectors: Mapping[str, str | None] | None = None
                ) -> dict[str, str]:
        selected = selectors or {}
        return {k: self._read(Path(k).expanduser(), selected.get(k))
                for k in keys}

    @staticmethod
    def _read(path: Path, json_key: str | None) -> str:
        if not path.is_file():
            raise SecretResolutionError(
                f"secret file not found: {path} - run "
                "`agentic-dev-loop copilot-login` to create it")
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as e:
            raise SecretResolutionError(
                f"secret file unreadable: {path}: {e} - run "
                "`agentic-dev-loop copilot-login` to recreate it") from e
        if json_key is None:
            return text.strip()
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as e:
            raise SecretResolutionError(
                f"secret file {path} is not valid JSON: {e}") from e
        value = payload.get(json_key) if isinstance(payload, dict) else None
        if not isinstance(value, str) or not value:
            raise SecretResolutionError(
                f"secret file {path} has no non-empty string {json_key!r}")
        return value


PROVIDERS: dict[str, SecretProvider] = {
    "env": EnvProvider(),
    "file": FileProvider(),
}


def resolve_secret_spec(spec, environ: Mapping[str, str] | None = None) -> str:
    """Resolve one ``SecretSpec`` (including a file provider's ``json_key``).
    Used by build_llm, which holds an LlmConfig rather than a pipeline config."""
    provider = (EnvProvider(environ) if spec.from_ == "env"
                else PROVIDERS[spec.from_])
    return provider.resolve([spec.key], {spec.key: spec.json_key})[spec.key]


def resolve_secrets(cfg: PipelineConfig,
                    environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """Resolve every declared ref host-side once (run start). Returns
    ``{ref_name: value}``. An unresolvable ref or unknown provider fails fast,
    naming the ref, the declaring step(s), and the provider error."""
    if environ is not None:
        providers = {"env": EnvProvider(environ), "file": FileProvider()}
    else:
        providers = PROVIDERS
    resolved: dict[str, str] = {}
    for ref, spec in cfg.secrets.items():
        provider = providers.get(spec.from_)
        if provider is None:
            raise SecretResolutionError(
                f"secret ref {ref!r}: unknown provider {spec.from_!r}")
        try:
            resolved[ref] = provider.resolve(
                [spec.key], {spec.key: spec.json_key})[spec.key]
        except SecretResolutionError as e:
            steps = [s.name for s in cfg.steps if ref in s.secrets]
            raise SecretResolutionError(
                f"secret ref {ref!r} (provider {spec.from_!r}, key {spec.key!r})"
                f" used by step(s) {steps or ['<none>']}: {e}") from e
    # 058 (D3): a file-backed llm key is read by build_llm rather than a step
    # ref, so resolve it here as well to join the run's redaction set.
    for name, spec in _llm_secret_specs(cfg).items():
        provider = providers.get(spec.from_)
        if provider is None:
            raise SecretResolutionError(
                f"llm api_key_secret {name!r}: unknown provider "
                f"{spec.from_!r}")
        try:
            resolved[f"llm-api-key:{name}"] = provider.resolve(
                [spec.key], {spec.key: spec.json_key})[spec.key]
        except SecretResolutionError as e:
            raise SecretResolutionError(
                f"llm api_key_secret ({name}, provider {spec.from_!r}): {e}"
            ) from e
    return resolved


def _llm_secret_specs(cfg: PipelineConfig) -> dict[str, SecretSpec]:
    """The file-backed key specs build_llm will use: the default llm plus
    every profile a step references (054 D7's eager-build set)."""
    specs: dict[str, SecretSpec] = {}
    if cfg.llm.api_key_secret is not None:
        specs["llm"] = cfg.llm.api_key_secret
    for step in cfg.steps:
        if step.llm is not None:
            spec = cfg.llms[step.llm].api_key_secret
            if spec is not None:
                specs[f"llms.{step.llm}"] = spec
    return specs


def step_secret_env(cfg: PipelineConfig, step: StepConfig,
                    resolved: Mapping[str, str]) -> dict[str, str]:
    """The ``{env_name: value}`` mapping to inject for one step: only the refs
    it declares, keyed by each ref's ``key`` (the env name the step reads)."""
    return {cfg.secrets[ref].key: resolved[ref] for ref in step.secrets}


def redact_values(text: str, values: Iterable[str]) -> str:
    """Replace every resolved secret value in ``text`` with ``[REDACTED]``.
    Longest-first so a value that is a substring of another is fully removed."""
    if not text:
        return text
    secrets = sorted({v for v in values if v}, key=len, reverse=True)
    for value in secrets:
        text = text.replace(value, REDACTED)
    return text


def scan_env_files(workspace: Path | str) -> None:
    """The ``.env*`` ban: any ``.env*`` file at the workspace root - other than
    the convention fixtures (``.env.example`` / ``.env.template`` /
    ``.env.sample``) - fails fast, naming the files and the remedy. Applies
    regardless of ``network:`` flags: the workspace must simply not hold
    secrets."""
    root = Path(workspace)
    hits = sorted(
        p.name for p in root.glob(".env*")
        if p.is_file() and p.name not in ENV_FILE_WHITELIST
    )
    if hits:
        raise SecretResolutionError(
            "secret-bearing file(s) found in the workspace root: "
            + ", ".join(hits)
            + " - move these values into `secrets:` refs / host env and "
            "delete the files (`.env.example`, `.env.template`, `.env.sample` "
            "are allowed fixtures)")
