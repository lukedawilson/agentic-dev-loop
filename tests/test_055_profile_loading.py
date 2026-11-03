"""055 Task 2: load-time profile resolution in core.config.load_config."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentic_dev_loop.core.config import load_config


def _write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "pipeline.yaml"
    path.write_text(body, encoding="utf-8")
    return path


BASE = """
name: fixture
deps_policy: open
llm: {model: openai/x, api_key_env: KEY}
agents_dir: agents
steps:
  - {name: dev, agent: dev, on_pass: success}
"""


def test_profile_python_resolves_image_and_cpus(tmp_path: Path):
    path = _write(tmp_path, "profile: python\n" + BASE)
    cfg = load_config(path)
    assert cfg.profile == "python"
    assert cfg.isolation is not None
    assert cfg.isolation.cpus == 2
    image = cfg.isolation.image or ""
    assert image.endswith(
        "sha256:890105209c127446caf20ce3dc05f0c0f5527e9d679e18cc0075e6b0ee917434")


def test_pipeline_isolation_overrides_profile_scalars(tmp_path: Path):
    path = _write(
        tmp_path,
        "profile: python\n" + BASE + "isolation: {cpus: 8}\n",
    )
    cfg = load_config(path)
    assert cfg.isolation.cpus == 8
    assert cfg.isolation.image is not None


def test_profile_caches_union_profile_first(tmp_path: Path):
    path = _write(
        tmp_path,
        "profile: dotnet-node\n" + BASE +
        "caches: [qa/.browsers, .nuget-packages]\n",
    )
    cfg = load_config(path)
    assert cfg.caches == [".nuget-packages", "qa/.browsers"]


def test_unknown_profile_fails_with_pipeline_path(tmp_path: Path):
    path = _write(tmp_path, "profile: nope\n" + BASE)
    with pytest.raises(Exception) as exc:
        load_config(path)
    message = str(exc.value)
    assert f"pipeline {path}:" in message
    assert "unknown profile 'nope'" in message


def test_pipeline_without_profile_unchanged(tmp_path: Path):
    path = _write(tmp_path, BASE)
    cfg = load_config(path)
    assert cfg.profile is None
    assert cfg.isolation is None
    assert cfg.caches == []
