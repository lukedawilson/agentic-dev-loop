"""Unit tests for the host-side container inspection report (042 D5/D6).

Every test drives the injected CommandRunner (the same seam the isolation
tests use); no daemon is required. The report must stay read-only and must
never mark an unregistered name as reaped."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from agentic_dev_loop.engine.containers import containers_report
from agentic_dev_loop.engine.isolation import (CONTAINER_REGISTRY, CmdResult,
                                               process_start,
                                               reap_orphaned_containers)


class FakeRunner:
    """Scripted CommandRunner - the unit-test seam for the daemon CLI."""

    def __init__(self, *script):
        self.calls: list[list[str]] = []
        self.script = list(script)

    def run(self, argv, *, timeout=None, check=False):
        self.calls.append(argv)
        if not self.script:
            return CmdResult(exit_code=0, stdout="", stderr="")
        item = self.script[0]
        if len(self.script) > 1:
            self.script.pop(0)
        return item(argv) if callable(item) else item


def ok(stdout=""):
    return CmdResult(exit_code=0, stdout=stdout, stderr="")


def _write(state_dir, entries):
    path = Path(state_dir) / CONTAINER_REGISTRY
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for entry in entries:
            fh.write(json.dumps(entry) + "\n")


def _entry(name, pid, process_start):
    return {"run_id": "r", "name": name, "pid": pid,
            "process_start": process_start, "created_at": "now"}


class TestContainersReport:
    def test_live_owner(self, tmp_path):
        name = f"{'a' * 32}-exec"
        _write(tmp_path / "state", [_entry(name, os.getpid(),
                                           process_start(os.getpid()))])
        runner = FakeRunner(ok(name + "\n"), ok(""))
        report = containers_report(tmp_path / "state", runner)
        assert report["containers"][0]["verdict"] == "live"
        assert report["containers"][0]["name"] == name
        assert report["unregistered"] == []

    def test_orphaned_owner(self, tmp_path):
        proc = subprocess.Popen([sys.executable, "-c", "pass"])
        proc.wait()
        name = f"{'b' * 32}-exec"
        _write(tmp_path / "state", [_entry(name, proc.pid, None)])
        runner = FakeRunner(ok(name + "\n"), ok(""))
        report = containers_report(tmp_path / "state", runner)
        assert report["containers"][0]["verdict"] == "orphaned"

    def test_stale_entry(self, tmp_path):
        name = f"{'c' * 32}-exec"
        _write(tmp_path / "state", [_entry(name, os.getpid(),
                                           process_start(os.getpid()))])
        runner = FakeRunner(ok(""), ok(""))
        report = containers_report(tmp_path / "state", runner)
        assert report["containers"][0]["verdict"] == "stale-entry"

    def test_unregistered_sweep_is_list_only(self, tmp_path):
        name = f"{'d' * 32}-exec"
        net = f"{'e' * 32}-net"
        runner = FakeRunner(ok(name + "\n"), ok(net + "\n"))
        report = containers_report(tmp_path / "state", runner)
        assert report["containers"] == []
        assert report["unregistered"] == [
            {"name": name, "kind": "container", "verdict": "unregistered"},
            {"name": net, "kind": "network", "verdict": "unregistered"},
        ]

    def test_json_shape_has_stable_keys(self, tmp_path):
        name = f"{'f' * 32}-exec"
        _write(tmp_path / "state", [_entry(name, os.getpid(),
                                           process_start(os.getpid()))])
        runner = FakeRunner(ok(name + "\n"), ok(""))
        report = containers_report(tmp_path / "state", runner)
        encoded = json.dumps(report, sort_keys=True)
        decoded = json.loads(encoded)
        assert set(decoded) == {"containers", "unregistered"}
        assert set(decoded["containers"][0]) == {
            "name", "run_id", "kind", "pid", "process_start", "created_at",
            "verdict",
        }
        assert decoded["containers"][0]["verdict"] == "live"

    def test_report_does_not_modify_registry(self, tmp_path):
        name = f"{'a' * 32}-exec"
        entry = _entry(name, os.getpid(), process_start(os.getpid()))
        _write(tmp_path / "state", [entry])
        runner = FakeRunner(ok(name + "\n"), ok(""))
        containers_report(tmp_path / "state", runner)
        path = tmp_path / "state" / CONTAINER_REGISTRY
        assert json.loads(path.read_text()) == entry


class TestReapReturnValue:
    def test_reap_returns_removed_names(self, tmp_path):
        proc = subprocess.Popen([sys.executable, "-c", "pass"])
        proc.wait()
        name = f"{'a' * 32}-exec"
        _write(tmp_path / "state", [_entry(name, proc.pid, None)])
        runner = FakeRunner(ok(""))
        assert reap_orphaned_containers(tmp_path / "state", runner) == [name]
        assert runner.calls == [["docker", "rm", "-f", name]]

    def test_reap_live_owner_returns_empty(self, tmp_path):
        name = f"{'b' * 32}-exec"
        _write(tmp_path / "state", [_entry(name, os.getpid(),
                                           process_start(os.getpid()))])
        runner = FakeRunner()
        assert reap_orphaned_containers(tmp_path / "state", runner) == []
        assert runner.calls == []
