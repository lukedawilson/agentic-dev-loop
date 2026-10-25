"""Marked real-Docker integration tests for the host boundary (020): a
consumer action's code runs inside the sandbox and cannot touch the host.
Skip with an explicit reason when no daemon or integration image is present;
never silently skipped in CI."""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from openhands.sdk import LLM

from agentic_dev_loop.core.config import IsolationConfig, PipelineConfig
from agentic_dev_loop.engine.isolation import (
    DockerBackend, SandboxError, SubprocessRunner, probe_docker,
    resolve_effective_profile,
)
from agentic_dev_loop.engine.registry import Registry
from agentic_dev_loop.engine.stages import Runtime, make_action_node
from agentic_dev_loop.engine.step_workspace import StepWorkspace

pytestmark = pytest.mark.docker

TEST_IMAGE = "python:3.12"


def _docker_available() -> str | None:
    if shutil.which("docker") is None:
        return "docker CLI not on PATH"
    try:
        probe_docker()
    except SandboxError as e:
        return f"docker daemon unavailable: {e}"
    return None


@pytest.fixture
def backend(tmp_path):
    reason = _docker_available()
    if reason is not None:
        pytest.skip(reason)
    inspect = SubprocessRunner().run(
        ["docker", "image", "inspect", TEST_IMAGE, "--format", "{{.Id}}"],
        timeout=30)
    if inspect.exit_code != 0:
        pytest.skip(f"integration image {TEST_IMAGE!r} not present locally")
    ws = tmp_path / "ws"
    ws.mkdir()
    # unit-5: every step resolves a mount set, so enforcement needs the
    # materialised .git-free export (AE-SBX-017 otherwise). Wire the export
    # the way startup_sandbox does (mirror test_isolation_integration's
    # _export_backend).
    export = tmp_path / "state" / "exports" / "r1"
    ws_obj = StepWorkspace.materialise(ws, [], "linux", export)
    profile = resolve_effective_profile(IsolationConfig(image=TEST_IMAGE))
    b = DockerBackend(image=TEST_IMAGE, workspace=str(ws),
                      iso_state_dir=str(tmp_path / "state"), profile=profile,
                      export_root=str(export),
                      cache_mounts=ws_obj.mount_points()[1:])
    b.ensure_image()
    b.create_execution_container()
    yield b, ws, export
    b.close()


def _runtime(backend, ws, export, script, tmp_path):
    reg = Registry()
    reg.add_sandbox_action("probe", script)
    cfg = PipelineConfig.model_validate({
        "name": "probe",
        "llm": {"model": "openai/test-model", "api_key_env": "K"},
        "agents_dir": "agents",
        "lockfiles": ["uv.lock"],
        # The workspace is a declared-rw mount (BR-1.2): the consumer can
        # write inside the sandbox, and sync-back (BR-4) carries it to ws.
        "steps": [{"name": "probe", "action": "probe", "on_pass": "success",
                   "mounts": [{"path": ".", "mode": "rw"}]}],
    })
    runtime = Runtime(cfg=cfg, registry=reg,
                      llm=LLM(model="openai/test-model", api_key="sk-test"),
                      agents_dir=tmp_path / "agents", state_dir=tmp_path / "state",
                      workspace=ws, sandbox=backend, sandbox_cwd=Path(export))
    return runtime, cfg.steps[0]


class TestConsumerActionSentinel:
    def test_action_code_cannot_create_host_file(self, backend, tmp_path):
        b, ws, export = backend
        sentinel = tmp_path / "host-sentinel.txt"
        # The marker is written first: it proves the consumer code really ran
        # inside the sandbox, so this test can never pass vacuously when the
        # action dies pre-exec. Then the escape attempt.
        script = (f'python -c "open(\\"ran-in-sandbox.txt\\", \\"w\\").write(\\"ran\\"); '
                  f'open({str(sentinel)!r}, \\"w\\").write(\\"pwned\\")"')
        runtime, step = _runtime(b, ws, export, script, tmp_path)
        make_action_node(runtime, step)({"subject": "s", "attempt": 1,
                                         "step_verdicts": {}})
        # The escape attempt targets a path that exists on the host but is not
        # the export mount: the container never sees it, so it fails inside
        # the container (or lands in its own filesystem), never on the host.
        assert (ws / "ran-in-sandbox.txt").read_text() == "ran"
        assert not sentinel.exists()

    def test_action_can_still_write_inside_workspace(self, backend, tmp_path):
        b, ws, export = backend
        script = 'python -c "open(\\"in-sandbox.txt\\", \\"w\\").write(\\"ok\\")"'
        runtime, step = _runtime(b, ws, export, script, tmp_path)
        update = make_action_node(runtime, step)({"subject": "s", "attempt": 1,
                                                  "step_verdicts": {}})
        # The action genuinely executed (verdict pass) and its declared-rw
        # write synced back to the real workspace (BR-4).
        assert update["step_verdicts"]["probe"] == "pass"
        assert (ws / "in-sandbox.txt").read_text() == "ok"
