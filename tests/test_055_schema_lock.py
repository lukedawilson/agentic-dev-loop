"""055 Task 11: every bundled profile plus recipe renders a loadable pipeline."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentic_dev_loop.core.config import load_config
from agentic_dev_loop.core.profiles import bundled_profile_names
from agentic_dev_loop.engine.onboarding.init import run_init


def _fixture(tmp_path: Path, profile: str) -> Path:
    if profile == "python":
        (tmp_path / "pyproject.toml").write_text(
            "[project]\nname = 'fixture'\nversion = '0.1.0'\n",
            encoding="utf-8")
        (tmp_path / "README.md").write_text("# Fixture\n", encoding="utf-8")
    else:
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "App.sln").write_text("\n", encoding="utf-8")
        (tmp_path / "frontend").mkdir()
        (tmp_path / "frontend" / "package.json").write_text(
            '{"name": "frontend"}\n', encoding="utf-8")
        (tmp_path / "tools").mkdir()
        (tmp_path / "tools" / "run-conformance.sh").write_text(
            "#!/bin/sh\nexit 0\n", encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text("# Rules\n", encoding="utf-8")
    return tmp_path


@pytest.mark.parametrize("profile", bundled_profile_names())
def test_bundled_profile_renders_a_loadable_pipeline(tmp_path: Path,
                                                     profile: str):
    root = _fixture(tmp_path, profile)
    assert run_init(["--profile", profile, "--yes", "--dir", str(root)]) == 0
    cfg = load_config(root / "agentic-dev-loop" / "pipeline.yaml")
    assert cfg.profile == profile
    assert cfg.steps
    assert cfg.isolation is not None
    assert cfg.isolation.image
