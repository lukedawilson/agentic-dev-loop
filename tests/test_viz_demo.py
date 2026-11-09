"""Tests for viz_demo.py's fabricated run.

The scripted scenarios must publish events for every step of the shipped
pipeline (brief, deps, dev, restore, test, review, qa, commit, success) so
the demo eyeballs the real topology, not a stale subset (the 012-class
demo-drift bug). ``run_scenario`` never sleeps here: the module's
``time.sleep`` is patched away so the whole scripted run is synchronous.
"""

import queue
from pathlib import Path

from agentic_dev_loop import viz
from agentic_dev_loop.core.config import load_config

from tests.smoke import viz_demo

SELF_PIPELINE = Path(__file__).parent.parent / "agentic-dev-loop" / "pipeline.yaml"


class TestDemoScenario:
    """The fabricated run covers the full pipeline chain."""

    @staticmethod
    def _collect(monkeypatch, scenario, **kwargs):
        monkeypatch.setattr(viz_demo.time, "sleep", lambda _s: None)
        bus = viz.VizBus()
        sub = bus.subscribe()
        viz_demo.run_scenario(bus, scenario, **kwargs)
        events = []
        while True:
            try:
                events.append(sub.get_nowait())
            except queue.Empty:
                break
        return events

    @staticmethod
    def _named(events, etype):
        return [e["node"] for e in events if e["type"] == etype]

    def test_success_covers_every_topology_node(self, monkeypatch):
        topology = viz_demo.real_topology()
        events = self._collect(monkeypatch, "success")
        assert set(self._named(events, "node_started")) == set(topology["nodes"])
        assert set(self._named(events, "node_finished")) == set(topology["nodes"])
        # no phantom nodes (the old "bump") and no misplaced attribution
        known = set(topology["nodes"])
        for event in events:
            if event["type"] in ("node_started", "node_finished", "heartbeat"):
                assert event["node"] in known
            elif event["type"] == "console":
                assert event["node"] is None or event["node"] in known

    def test_success_retry_rewinds_to_dev(self, monkeypatch):
        events = self._collect(monkeypatch, "success")
        started = self._named(events, "node_started")
        for name in ("brief", "deps", "commit", "success"):
            assert started.count(name) == 1
        for name in ("dev", "restore", "test", "review", "qa"):
            assert started.count(name) == 2
        order = [(e["type"], e["node"]) for e in events
                 if e["type"] in ("node_started", "node_finished")]
        i = order.index(("node_finished", "qa"))
        assert order[i + 1] == ("node_started", "dev")

    def test_failure_stops_before_commit(self, monkeypatch):
        events = self._collect(monkeypatch, "failure")
        started = self._named(events, "node_started")
        assert started == ["brief", "deps", "dev", "restore", "test",
                           "review", "qa"]
        qa_finish = [e for e in events
                     if e["type"] == "node_finished" and e["node"] == "qa"]
        assert len(qa_finish) == 1 and qa_finish[0]["ok"] is False
        run_finished = [e for e in events if e["type"] == "run_finished"]
        assert len(run_finished) == 1
        assert run_finished[0]["success"] is False
        assert run_finished[0]["attempts"] == 1

    def test_command_steps_show_command_consoles(self, monkeypatch):
        events = self._collect(monkeypatch, "success")
        commands = {e["text"] for e in events
                    if e["type"] == "console"
                    and e["node"] in ("deps", "restore", "test")
                    and e["text"].startswith("$ ")}
        assert len(commands) >= 3
        assert any("pip install" in c for c in commands)
        assert any("pip_audit" in c for c in commands)
        assert any("pytest" in c for c in commands)

    def test_verdicts_accumulate_then_reset_per_attempt(self, monkeypatch):
        events = self._collect(monkeypatch, "success")
        snapshots = [e["step_verdicts"] for e in events
                     if e["type"] == "state" and "step_verdicts" in e]
        expected = [
            {"brief": "pass"},
            {"brief": "pass", "deps": "pass"},
            {"brief": "pass", "deps": "pass", "dev": "pass"},
            {"brief": "pass", "deps": "pass", "dev": "pass", "restore": "pass"},
            {"brief": "pass", "deps": "pass", "dev": "pass", "restore": "pass",
             "test": "pass"},
            {"brief": "pass", "deps": "pass", "dev": "pass", "restore": "pass",
             "test": "pass", "review": "pass"},
            {"brief": "pass", "deps": "pass", "dev": "pass", "restore": "pass",
             "test": "pass", "review": "pass", "qa": "fail"},
            {},
            {"dev": "pass"},
            {"dev": "pass", "restore": "pass"},
            {"dev": "pass", "restore": "pass", "test": "pass"},
            {"dev": "pass", "restore": "pass", "test": "pass", "review": "pass"},
            {"dev": "pass", "restore": "pass", "test": "pass", "review": "pass",
             "qa": "pass"},
            {"dev": "pass", "restore": "pass", "test": "pass", "review": "pass",
             "qa": "pass", "commit": "pass"},
        ]
        assert snapshots == expected
        attempts = [e["attempt"] for e in events
                    if e["type"] == "state" and "attempt" in e]
        assert attempts == [2]

    def test_run_started_uses_the_shipped_max_attempts(self, monkeypatch):
        events = self._collect(monkeypatch, "success")
        run_started = [e for e in events if e["type"] == "run_started"]
        assert len(run_started) == 1
        assert run_started[0]["max_attempts"] == \
            load_config(SELF_PIPELINE).max_attempts
