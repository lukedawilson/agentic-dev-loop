"""055 Task 8: CLI dispatch for init and doctor."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentic_dev_loop.engine import cli
from agentic_dev_loop.engine.cli import main


def test_doctor_help_exits_zero(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["doctor", "--help"])
    assert exc.value.code == 0
    assert "usage" in capsys.readouterr().out.lower()


def test_init_help_exits_zero(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["init", "--help"])
    assert exc.value.code == 0
    assert "usage" in capsys.readouterr().out.lower()


def test_doctor_json_fast_returns_int(monkeypatch, capsys):
    from agentic_dev_loop.engine.onboarding import checks

    monkeypatch.setattr(checks.HostProbes, "latest_release",
                        lambda self: None)
    monkeypatch.setattr(checks.HostProbes, "docker_present",
                        lambda self: True)
    monkeypatch.setattr(checks.HostProbes, "daemon_info",
                        lambda self: (True, "1.45", "linux", ""))
    code = main(["doctor", "--json", "--fast"])
    assert isinstance(code, int)
    capsys.readouterr()


def test_unknown_subcommand_falls_through_to_probe(monkeypatch):
    seen = {}

    def fake_registry(path):
        seen["path"] = path
        raise RuntimeError("probe reached")

    monkeypatch.setattr(cli, "_registry_for", fake_registry)
    with pytest.raises(RuntimeError, match="probe reached"):
        main(["doctorx"])
    assert seen["path"] == Path("doctorx")
