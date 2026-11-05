"""Unit 10 runtime protocol and the step-runner translation table."""

import pytest
from openhands.sdk import LLM

from agentic_dev_loop.core.config import PipelineConfig
from agentic_dev_loop.engine.registry import default_registry
from agentic_dev_loop.engine.runtimes.protocol import (
    NUDGE_TEMPLATE, NudgePolicy, RuntimeResult, RuntimeStatus)
from agentic_dev_loop.engine.stages import Runtime


def make_cfg(steps, **over):
    base = {"name": "t", "llm": {"model": "m", "api_key_env": "K"},
            "agents_dir": "agents", "lockfiles": ["uv.lock"], "steps": steps}
    base.update(over)
    return PipelineConfig.model_validate(base)


STEPS = [{
    "name": "qa", "agent": "qa", "artifact": "qa-report.md",
    "verdicts": {"pass": "PASS", "fail": "FAIL"},
    "max_iterations": 100, "finalize_nudge_turns": 10,
    "on_pass": "success",
}]


def make_runtime(tmp_path, fake_sandbox, steps=None):
    state_dir = tmp_path / ".pr"
    state_dir.mkdir(exist_ok=True)
    return Runtime(
        cfg=make_cfg(steps or STEPS), registry=default_registry(),
        llm=LLM(model="openai/test-model", api_key="sk-test"),
        agents_dir=tmp_path / "agents", state_dir=state_dir,
        workspace=tmp_path, sandbox=fake_sandbox,
        secrets={"ref": "supersecret"})


class FakeRuntime:
    def __init__(self, result=None, exc=None):
        self.result = result
        self.exc = exc
        self.calls = []
        self.closed = False

    def run(self, message, *, nudge_policy=None):
        self.calls.append((message, nudge_policy))
        if self.exc is not None:
            raise self.exc
        return self.result

    def close(self):
        self.closed = True


def _inject(monkeypatch, fake, captured=None):
    def build(cfg, ctx):
        if captured is not None:
            captured["ctx"] = ctx
        return fake

    monkeypatch.setattr(
        "agentic_dev_loop.engine.runtimes.factory.build_runtime", build)


def test_truncated_is_derived_from_status():
    assert RuntimeResult(RuntimeStatus.FINISHED).truncated is False
    for status in (RuntimeStatus.ERROR, RuntimeStatus.STUCK,
                   RuntimeStatus.TRUNCATED):
        assert RuntimeResult(status).truncated is True


def test_nudge_policy_renders_the_035_template():
    policy = NudgePolicy.for_turns(10)
    assert policy.turns_before_end == 10
    assert policy.message == NUDGE_TEMPLATE.format(n=10)
    assert policy.message == "10 turns remain: produce your final response now"


def test_finished_maps_to_ok(tmp_path, fake_sandbox, monkeypatch):
    runtime = make_runtime(tmp_path, fake_sandbox)
    fake = FakeRuntime(RuntimeResult(RuntimeStatus.FINISHED, "done"))
    _inject(monkeypatch, fake)
    result = runtime.run_agent("qa", "task", step=runtime.cfg.steps[0])
    assert result.ok and not result.truncated
    assert result.response == "done"
    assert fake.closed


def test_error_stuck_and_budget_map_to_truncated(tmp_path, fake_sandbox,
                                                 monkeypatch):
    for status in (RuntimeStatus.ERROR, RuntimeStatus.STUCK,
                   RuntimeStatus.TRUNCATED):
        runtime = make_runtime(tmp_path, fake_sandbox)
        fake = FakeRuntime(RuntimeResult(status, "partial"))
        _inject(monkeypatch, fake)
        result = runtime.run_agent("qa", "task", step=runtime.cfg.steps[0])
        assert result.ok and result.truncated
        assert result.response == "partial"


def test_capture_error_rides_the_unclear_retry_channel(tmp_path, fake_sandbox,
                                                       monkeypatch):
    runtime = make_runtime(tmp_path, fake_sandbox)
    fake = FakeRuntime(RuntimeResult(RuntimeStatus.FINISHED, None,
                                     capture_error="empty_response"))
    _inject(monkeypatch, fake)
    result = runtime.run_agent("qa", "task", step=runtime.cfg.steps[0])
    assert result.ok and result.capture_error == "empty_response"


def test_runtime_exception_is_ok_false_with_last_error(tmp_path, fake_sandbox,
                                                       monkeypatch):
    runtime = make_runtime(tmp_path, fake_sandbox)
    _inject(monkeypatch, FakeRuntime(exc=RuntimeError("boom")))
    result = runtime.run_agent("qa", "task", step=runtime.cfg.steps[0])
    assert result.ok is False
    assert "boom" in (runtime.state_dir / "last-error.md").read_text()


def test_nudge_policy_is_built_from_the_step(tmp_path, fake_sandbox,
                                             monkeypatch):
    runtime = make_runtime(tmp_path, fake_sandbox)
    fake = FakeRuntime(RuntimeResult(RuntimeStatus.FINISHED, "done"))
    _inject(monkeypatch, fake)
    runtime.run_agent("qa", "task", step=runtime.cfg.steps[0])
    message, policy = fake.calls[0]
    assert message == "task"
    assert policy is not None and policy.turns_before_end == 10


def test_context_is_resolved_before_driver_construction(tmp_path, fake_sandbox,
                                                        monkeypatch):
    runtime = make_runtime(tmp_path, fake_sandbox)
    captured = {}
    _inject(monkeypatch, FakeRuntime(RuntimeResult(RuntimeStatus.FINISHED)),
            captured)
    runtime.run_agent("qa", "task", step=runtime.cfg.steps[0])
    ctx = captured["ctx"]
    assert ctx.workspace == runtime.workspace
    assert ctx.sandbox is fake_sandbox
    assert ctx.step is runtime.cfg.steps[0]
    assert ctx.max_iterations == 500
    assert ctx.secrets == {"ref": "supersecret"}


def test_runtime_closed_even_when_run_raises(tmp_path, fake_sandbox,
                                             monkeypatch):
    runtime = make_runtime(tmp_path, fake_sandbox)
    fake = FakeRuntime(exc=RuntimeError("boom"))
    _inject(monkeypatch, fake)
    runtime.run_agent("qa", "task", step=runtime.cfg.steps[0])
    assert fake.closed


def test_driver_extra_redactions_reach_last_error(tmp_path, fake_sandbox,
                                                  monkeypatch):
    runtime = make_runtime(tmp_path, fake_sandbox)

    class LeakyRuntime(FakeRuntime):
        extra_redactions = ("driver-secret",)

        def run(self, message, *, nudge_policy=None):
            raise RuntimeError("failed with driver-secret in the trace")

    _inject(monkeypatch, LeakyRuntime())
    result = runtime.run_agent("qa", "task", step=runtime.cfg.steps[0])
    assert result.ok is False
    text = (runtime.state_dir / "last-error.md").read_text()
    assert "driver-secret" not in text
    assert "failed with" in text
