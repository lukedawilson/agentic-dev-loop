"""055 Task 8: CLI dispatch for init, doctor, and upgrade."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentic_dev_loop.engine import cli
from agentic_dev_loop.engine.cli import main


def test_unknown_subcommand_falls_through_to_probe(monkeypatch):
    seen = {}

    def fake_registry(path):
        seen["path"] = path
        raise RuntimeError("probe reached")

    monkeypatch.setattr(cli, "_registry_for", fake_registry)
    with pytest.raises(RuntimeError, match="probe reached"):
        main(["doctorx"])
    assert seen["path"] == Path("doctorx")
