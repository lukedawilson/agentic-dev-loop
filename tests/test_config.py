from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from agentic_dev_loop.core.config import Route, default_state_dir, load_config

# The plan's reference pipeline (consumer-equivalent) (009, YAML schema section),
# verbatim except for two substitutions: 012 replaced the agent step named
# `test` (artifact ci-fix.md, PASS/FAIL) with the mechanical command step
# named `test` - same position, same on_fail wiring; and 009 dropped the
# port_sweep step (its sweep moved into engine startup, no longer a pipeline
# step). This is the canonical pin: the documented schema must load.
FULL_YAML = """\
name: reference-construction

extensions:
  - agentic_dev_loop.engine.extensions.aidlc:AidlcExtension

additional_files:
  - coding-standards-ddd.yaml

llm:
  model: openai/deepseek-v4-pro
  base_url: https://api.deepseek.com
  api_key_env: OPENAI_API_KEY

state_dir: .pr
max_attempts: 3
agents_dir: sdk_agents
deps_policy: open

steps:
  - name: dev
    agent: dev
    produces: implementation-summary.md
    on_pass: test

  - name: test
    command: python -m pytest
    timeout: 600
    on_pass: review
    on_fail: { goto: dev, retry: true }

  - name: review
    agent: review
    artifact: review-findings.md
    verdicts: { pass: APPROVED, fail: NEEDS CHANGES }
    on_pass: qa
    on_fail: { goto: dev, retry: true }

  - name: qa
    agent: qa
    artifact: qa-report.md
    verdicts: { pass: PASS, fail: FAIL }
    max_iterations: 200
    on_pass: commit
    on_fail: { goto: dev, retry: true }

  - name: commit
    action: commit
    params: { message: "feat: construct {subject} (agent dev loop)" }
    on_pass: success
"""


def _base() -> dict:
    return {
        "name": "test-pipeline",
        "llm": {"model": "m", "api_key_env": "K"},
        "agents_dir": "sdk_agents",
        "lockfiles": ["uv.lock"],
        "steps": [{"name": "dev", "agent": "dev", "on_pass": "success"}],
    }


def _load(tmp_path: Path, data):
    p = tmp_path / "pipeline.yaml"
    p.write_text(yaml.safe_dump(data))
    return load_config(p)


def test_full_schema_example_round_trips(tmp_path):
    p = tmp_path / "pipeline.yaml"
    p.write_text(FULL_YAML)
    cfg = load_config(p)

    assert cfg.name == "reference-construction"
    assert cfg.extensions == ["agentic_dev_loop.engine.extensions.aidlc:AidlcExtension"]
    assert cfg.additional_files == ["coding-standards-ddd.yaml"]
    assert cfg.state_dir == ".pr"
    assert cfg.max_attempts == 3
    assert cfg.agents_dir == "sdk_agents"

    assert cfg.llm.model == "openai/deepseek-v4-pro"
    assert cfg.llm.base_url == "https://api.deepseek.com"
    assert cfg.llm.api_key_env == "OPENAI_API_KEY"
    assert cfg.llm.timeout is None  # opinionated no-timeout default

    dev, test, review, qa, commit = cfg.steps
    assert dev.agent == "dev"
    assert dev.produces == ["implementation-summary.md"]
    assert dev.on_pass == "test"
    assert dev.on_fail is None

    assert test.agent is None
    assert test.command == "python -m pytest"
    assert test.timeout == 600
    assert test.on_fail == Route(goto="dev", retry=True)

    assert review.verdicts.fail == "NEEDS CHANGES"

    assert qa.max_iterations == 200

    assert commit.action == "commit"
    assert commit.params["message"] == "feat: construct {subject} (agent dev loop)"
    assert commit.on_pass == "success"


def test_minimal_config_applies_defaults(tmp_path):
    cfg = _load(tmp_path, _base())
    assert cfg.extensions == []
    assert cfg.additional_files == []
    assert cfg.state_dir == default_state_dir(tmp_path / "pipeline.yaml")
    assert cfg.isolation is None
    assert cfg.max_attempts == 3
    step = cfg.steps[0]
    assert step.action is None
    assert step.command is None
    assert step.timeout is None
    assert step.params == {}
    assert step.produces == []
    assert step.artifact is None
    assert step.verdicts is None
    assert step.max_iterations is None
    assert step.on_fail is None


def test_env_allowlist_defaults_to_empty_and_loads(tmp_path):
    """The subprocess env allowlist is optional and additive (NFR-2.5) -
    absent → empty, present → the exact list."""
    assert _load(tmp_path, _base()).env_allowlist == []
    data = _base() | {"env_allowlist": ["MY_VAR", "OTHER_VAR"]}
    assert _load(tmp_path, data).env_allowlist == ["MY_VAR", "OTHER_VAR"]


def test_produces_accepts_a_list(tmp_path):
    data = _base()
    data["steps"][0]["produces"] = ["a.md", "b.md"]
    assert _load(tmp_path, data).steps[0].produces == ["a.md", "b.md"]


def test_unknown_top_level_key_rejected(tmp_path):
    data = _base() | {"bogus": 1}
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        _load(tmp_path, data)


def test_unknown_step_key_rejected(tmp_path):
    data = _base()
    data["steps"][0]["bogus"] = 1
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        _load(tmp_path, data)


def test_unknown_llm_key_rejected(tmp_path):
    data = _base()
    data["llm"]["bogus"] = 1
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        _load(tmp_path, data)


def test_extra_body_llm_key_rejected(tmp_path):
    """extra_body was dead config: the SDK model silently drops it (the real
    field is litellm_extra_body), so the schema no longer accepts it."""
    data = _base()
    data["llm"]["extra_body"] = {"chat_template_kwargs": {"thinking": False}}
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        _load(tmp_path, data)


def test_llm_reasoning_effort_round_trips(tmp_path):
    data = _base()
    data["llm"]["reasoning_effort"] = "medium"
    assert _load(tmp_path, data).llm.reasoning_effort == "medium"


def test_llm_reasoning_effort_defaults_none(tmp_path):
    assert _load(tmp_path, _base()).llm.reasoning_effort is None


def test_llm_reasoning_effort_unknown_value_rejected(tmp_path):
    data = _base()
    data["llm"]["reasoning_effort"] = "maximum"
    with pytest.raises(ValidationError):
        _load(tmp_path, data)


def test_step_reasoning_effort_round_trips(tmp_path):
    data = _base()
    data["steps"][0]["reasoning_effort"] = "xhigh"
    assert _load(tmp_path, data).steps[0].reasoning_effort == "xhigh"


def test_step_reasoning_effort_defaults_none(tmp_path):
    assert _load(tmp_path, _base()).steps[0].reasoning_effort is None


def test_step_reasoning_effort_unknown_value_rejected(tmp_path):
    data = _base()
    data["steps"][0]["reasoning_effort"] = "banana"
    with pytest.raises(ValidationError):
        _load(tmp_path, data)


def test_unknown_route_key_rejected(tmp_path):
    data = _base()
    data["steps"][0]["on_pass"] = {"goto": "success", "bogus": 1}
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        _load(tmp_path, data)


def test_unknown_verdicts_key_rejected(tmp_path):
    data = _base()
    data["steps"][0]["artifact"] = "a.md"
    data["steps"][0]["verdicts"] = {"pass": "P", "fail": "F", "bogus": 1}
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        _load(tmp_path, data)


def test_three_keyword_verdict_and_on_unverifiable_load(tmp_path):
    data = _base()
    data["steps"][0] = {
        "name": "qa", "agent": "qa", "artifact": "qa-report.md",
        "verdicts": {"pass": "PASS", "fail": "FAIL",
                     "unverifiable": "UNVERIFIABLE"},
        "on_pass": "success", "on_unverifiable": "success",
    }
    step = _load(tmp_path, data).steps[0]
    assert step.verdicts.pass_ == "PASS"
    assert step.verdicts.fail == "FAIL"
    assert step.verdicts.unverifiable == "UNVERIFIABLE"
    assert step.on_unverifiable == "success"


def test_dangling_on_unverifiable_rejected(tmp_path):
    data = _base()
    data["steps"][0]["on_unverifiable"] = "nope"
    with pytest.raises(ValidationError, match="unknown route target 'nope'"):
        _load(tmp_path, data)


def test_dangling_on_pass_rejected(tmp_path):
    data = _base()
    data["steps"][0]["on_pass"] = "nope"
    with pytest.raises(ValidationError, match="unknown route target 'nope'"):
        _load(tmp_path, data)


def test_dangling_retry_goto_rejected(tmp_path):
    data = _base()
    data["steps"][0]["on_fail"] = {"goto": "nope", "retry": True}
    with pytest.raises(ValidationError, match="unknown route target 'nope'"):
        _load(tmp_path, data)


def test_terminal_targets_allowed(tmp_path):
    data = _base()
    data["steps"][0]["on_pass"] = "failure"
    assert _load(tmp_path, data).steps[0].on_pass == "failure"


def test_both_agent_and_action_rejected(tmp_path):
    data = _base()
    data["steps"][0]["action"] = "commit"
    with pytest.raises(ValidationError,
                       match="exactly one of 'agent', 'action', or 'command'"):
        _load(tmp_path, data)


def test_neither_agent_nor_action_rejected(tmp_path):
    data = _base()
    del data["steps"][0]["agent"]
    with pytest.raises(ValidationError,
                       match="exactly one of 'agent', 'action', or 'command'"):
        _load(tmp_path, data)


@pytest.mark.parametrize("field", ["agent", "action", "command"])
def test_empty_type_value_rejected(tmp_path, field):
    data = _base()
    data["steps"][0] = {"name": "x", field: "", "on_pass": "success"}
    with pytest.raises(ValidationError, match="must be a non-empty string"):
        _load(tmp_path, data)


def test_step_kind_is_explicit_discriminator(tmp_path):
    """The step type IS the key it carries: agent/command/action."""
    data = _base()
    data["steps"] = [
        {"name": "a", "agent": "dev", "on_pass": "success"},
        {"name": "c", "command": "true", "on_pass": "success"},
        {"name": "x", "action": "commit", "on_pass": "success"},
    ]
    assert [s.kind for s in _load(tmp_path, data).steps] == \
        ["agent", "command", "action"]


def test_command_step_round_trips(tmp_path):
    data = _base()
    data["steps"] = [{"name": "test", "command": "python -m pytest",
                      "timeout": 60, "on_pass": "success"}]
    step = _load(tmp_path, data).steps[0]
    assert step.command == "python -m pytest"
    assert step.timeout == 60


def test_command_step_defaults_timeout_none(tmp_path):
    data = _base()
    data["steps"] = [{"name": "test", "command": "make check",
                      "on_pass": "success"}]
    step = _load(tmp_path, data).steps[0]
    assert step.command == "make check"
    assert step.timeout is None


@pytest.mark.parametrize("timeout", [0, -1])
def test_command_timeout_must_be_positive(tmp_path, timeout):
    data = _base()
    data["steps"] = [{"name": "test", "command": "true", "timeout": timeout}]
    with pytest.raises(ValidationError, match="greater than 0"):
        _load(tmp_path, data)


def test_command_and_agent_rejected(tmp_path):
    data = _base()
    data["steps"][0]["command"] = "true"
    with pytest.raises(ValidationError,
                       match="exactly one of 'agent', 'action', or 'command'"):
        _load(tmp_path, data)


def test_command_and_action_rejected(tmp_path):
    data = _base()
    data["steps"] = [{"name": "x", "action": "commit", "command": "true"}]
    with pytest.raises(ValidationError,
                       match="exactly one of 'agent', 'action', or 'command'"):
        _load(tmp_path, data)


def test_agent_action_command_rejected(tmp_path):
    data = _base()
    data["steps"][0]["action"] = "commit"
    data["steps"][0]["command"] = "true"
    with pytest.raises(ValidationError,
                       match="exactly one of 'agent', 'action', or 'command'"):
        _load(tmp_path, data)


def test_timeout_without_command_rejected(tmp_path):
    data = _base()
    data["steps"][0]["timeout"] = 60
    with pytest.raises(ValidationError, match="'timeout' requires 'command'"):
        _load(tmp_path, data)


def test_terminal_timeout_round_trip(tmp_path):
    data = _base()
    data["steps"][0]["terminal_timeout"] = 900
    assert _load(tmp_path, data).steps[0].terminal_timeout == 900


@pytest.mark.parametrize("step", [
    {"name": "x", "command": "true", "terminal_timeout": 900},
    {"name": "x", "action": "commit", "terminal_timeout": 900},
])
def test_terminal_timeout_requires_agent(tmp_path, step):
    data = _base()
    data["steps"] = [step]
    with pytest.raises(ValidationError, match="'terminal_timeout' requires 'agent'"):
        _load(tmp_path, data)


def test_negative_terminal_timeout_rejected(tmp_path):
    data = _base()
    data["steps"][0]["terminal_timeout"] = -1
    with pytest.raises(ValidationError):
        _load(tmp_path, data)


class TestCustomPrompt:
    def test_defaults_none(self, tmp_path):
        assert _load(tmp_path, _base()).steps[0].custom_prompt is None

    def test_literal_text_round_trips(self, tmp_path):
        data = _base()
        data["steps"][0]["custom_prompt"] = "verify the export tab first"
        assert _load(tmp_path, data).steps[0].custom_prompt == \
            "verify the export tab first"

    def test_existing_file_path_reads_contents(self, tmp_path):
        (tmp_path / "qa-plan.md").write_text("verify the export tab\n")
        data = _base()
        data["steps"][0]["custom_prompt"] = "qa-plan.md"
        assert _load(tmp_path, data).steps[0].custom_prompt == \
            "verify the export tab\n"

    def test_absolute_file_path_reads_contents(self, tmp_path):
        plan = tmp_path / "plan.txt"
        plan.write_text("ABSOLUTE PLAN")
        data = _base()
        data["steps"][0]["custom_prompt"] = str(plan)
        assert _load(tmp_path, data).steps[0].custom_prompt == "ABSOLUTE PLAN"

    def test_missing_path_with_extension_rejected(self, tmp_path):
        data = _base()
        data["steps"][0]["custom_prompt"] = "qa-plan.md"
        with pytest.raises(ValueError,
                           match="'custom_prompt' file not found: qa-plan.md"):
            _load(tmp_path, data)

    def test_missing_path_with_separator_rejected(self, tmp_path):
        data = _base()
        data["steps"][0]["custom_prompt"] = "tests/qa-plan"
        with pytest.raises(ValueError, match="tests/qa-plan"):
            _load(tmp_path, data)

    def test_requires_agent(self, tmp_path):
        data = _base()
        data["steps"] = [{"name": "x", "command": "true",
                          "custom_prompt": "hi"}]
        with pytest.raises(ValidationError,
                           match="'custom_prompt' requires 'agent'"):
            _load(tmp_path, data)

    def test_rejected_on_action_step(self, tmp_path):
        data = _base()
        data["steps"] = [{"name": "x", "action": "commit",
                          "custom_prompt": "hi"}]
        with pytest.raises(ValidationError,
                           match="'custom_prompt' requires 'agent'"):
            _load(tmp_path, data)

    def test_empty_value_rejected(self, tmp_path):
        data = _base()
        data["steps"][0]["custom_prompt"] = ""
        with pytest.raises(ValidationError, match="non-empty"):
            _load(tmp_path, data)


@pytest.mark.parametrize("field,value", [
    ("artifact", "a.md"),
    ("verdicts", {"pass": "P", "fail": "F"}),
    ("produces", ["a.md"]),
    ("params", {"x": 1}),
    ("max_iterations", 200),
])
def test_command_step_banned_fields_rejected(tmp_path, field, value):
    data = _base()
    data["steps"] = [{"name": "x", "command": "true", field: value}]
    with pytest.raises(ValidationError,
                       match=f"'command' steps cannot set '{field}'"):
        _load(tmp_path, data)


def test_artifact_without_verdicts_rejected(tmp_path):
    data = _base()
    data["steps"][0]["artifact"] = "a.md"
    with pytest.raises(ValidationError, match="'artifact' and 'verdicts' must appear together"):
        _load(tmp_path, data)


def test_verdicts_without_artifact_rejected(tmp_path):
    data = _base()
    data["steps"][0]["verdicts"] = {"pass": "P", "fail": "F"}
    with pytest.raises(ValidationError, match="'artifact' and 'verdicts' must appear together"):
        _load(tmp_path, data)


def test_produces_and_artifact_mutually_exclusive(tmp_path):
    data = _base()
    data["steps"][0]["produces"] = "a.md"
    data["steps"][0]["artifact"] = "b.md"
    data["steps"][0]["verdicts"] = {"pass": "P", "fail": "F"}
    with pytest.raises(ValidationError, match="mutually exclusive"):
        _load(tmp_path, data)


def test_duplicate_step_names_rejected(tmp_path):
    data = _base()
    data["steps"].append({"name": "dev", "agent": "dev", "on_pass": "success"})
    with pytest.raises(ValidationError, match="duplicate step names"):
        _load(tmp_path, data)


def test_zero_steps_rejected(tmp_path):
    data = _base()
    data["steps"] = []
    with pytest.raises(ValidationError):
        _load(tmp_path, data)


@pytest.mark.parametrize("terminal", ["success", "failure"])
def test_step_named_reserved_terminal_rejected(tmp_path, terminal):
    data = _base()
    data["steps"][0]["name"] = terminal
    data["steps"][0]["on_pass"] = "success"
    with pytest.raises(ValidationError, match="reserved terminal"):
        _load(tmp_path, data)


def test_non_mapping_yaml_rejected(tmp_path):
    p = tmp_path / "pipeline.yaml"
    p.write_text("- just\n- a\n- list\n")
    with pytest.raises(ValueError, match="must be a mapping"):
        load_config(p)


class TestIsolationConfig:
    def test_defaults(self):
        from agentic_dev_loop.core.config import IsolationConfig
        iso = IsolationConfig()
        assert iso.image is None
        assert iso.refresh is False
        assert iso.memory is None and iso.cpus is None
        assert iso.allow_git_mutations is False
        assert iso.state_dir == "~/.agentic-dev-loop/state"

    def test_accepts_overrides(self, tmp_path):
        data = _base() | {"isolation": {"image": "img@sha256:x", "refresh": True,
                                        "memory": "8g", "cpus": 4.0,
                                        "allow_git_mutations": True}}
        iso = _load(tmp_path, data).isolation
        assert iso.image == "img@sha256:x"
        assert iso.refresh is True
        assert iso.memory == "8g" and iso.cpus == 4.0
        assert iso.allow_git_mutations is True

    @pytest.mark.parametrize("memory", ["8g", "512m", "2k"])
    def test_valid_memory(self, tmp_path, memory):
        data = _base() | {"isolation": {"memory": memory}}
        assert _load(tmp_path, data).isolation.memory == memory

    def test_sandbox_venv_dir_accepts_and_round_trips(self, tmp_path):
        """022: `sandbox_venv_dir` is an optional relocation opt-in. It loads
        verbatim here; the outside-workspace validation happens at startup
        (where the workspace is known), not at config load."""
        data = _base() | {"isolation": {"sandbox_venv_dir":
                                        "~/.agentic-dev-loop/venvs/engine"}}
        iso = _load(tmp_path, data).isolation
        assert iso.sandbox_venv_dir == "~/.agentic-dev-loop/venvs/engine"
        roundtripped = type(iso).model_validate(iso.model_dump())
        assert roundtripped.sandbox_venv_dir == iso.sandbox_venv_dir

    def test_seccomp_profile_round_trips(self, tmp_path):
        """023: `seccomp_profile` is an optional host path to a seccomp
        profile JSON (Docker --security-opt seccomp=<path>). It loads verbatim;
        Docker validates the path at container-create time."""
        data = _base() | {"isolation": {"seccomp_profile":
                                        "/host/chromium-userns.json"}}
        iso = _load(tmp_path, data).isolation
        assert iso.seccomp_profile == "/host/chromium-userns.json"
        roundtripped = type(iso).model_validate(iso.model_dump())
        assert roundtripped.seccomp_profile == iso.seccomp_profile

    @pytest.mark.parametrize("memory", ["8", "g", "8G", "8 g", ""])
    def test_invalid_memory_rejected(self, tmp_path, memory):
        data = _base() | {"isolation": {"memory": memory}}
        with pytest.raises(ValidationError, match="must match"):
            _load(tmp_path, data)

    @pytest.mark.parametrize("cpus", [0, -1])
    def test_nonpositive_cpus_rejected(self, tmp_path, cpus):
        data = _base() | {"isolation": {"cpus": cpus}}
        with pytest.raises(ValidationError, match="cpus must be > 0"):
            _load(tmp_path, data)

    def test_unknown_isolation_key_rejected(self, tmp_path):
        data = _base() | {"isolation": {"bogus": 1}}
        with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
            _load(tmp_path, data)

    def test_round_trip(self, tmp_path):
        data = _base() | {"isolation": {"image": "img", "refresh": True,
                                        "memory": "4g", "cpus": 2.0}}
        cfg = _load(tmp_path, data)
        iso = cfg.isolation
        roundtripped = type(iso).model_validate(iso.model_dump())
        assert roundtripped == iso


class TestStepEnvAndSetup:
    def test_env_accepts_null_and_values(self, tmp_path):
        data = _base()
        data["steps"][0]["env"] = {"A": "x", "B": None}
        step = _load(tmp_path, data).steps[0]
        assert step.env == {"A": "x", "B": None}

    def test_env_defaults_none(self, tmp_path):
        assert _load(tmp_path, _base()).steps[0].env is None

    def test_setup_allowed_on_command_step(self, tmp_path):
        data = _base()
        data["steps"] = [{"name": "t", "command": "make",
                          "setup": "python -m venv .venv-sandbox",
                          "on_pass": "success"}]
        assert _load(tmp_path, data).steps[0].setup is not None

    def test_setup_allowed_on_agent_step(self, tmp_path):
        data = _base()
        data["steps"] = [{"name": "dev", "agent": "dev", "setup": "npm i",
                          "on_pass": "success"}]
        assert _load(tmp_path, data).steps[0].setup == "npm i"

    def test_setup_rejected_on_action_step(self, tmp_path):
        data = _base()
        data["steps"] = [{"name": "x", "action": "commit", "setup": "npm i",
                          "on_pass": "success"}]
        with pytest.raises(
                ValidationError,
                match="'setup' is only allowed on 'command' or 'agent' steps"):
            _load(tmp_path, data)

    def test_env_round_trip(self, tmp_path):
        data = _base()
        data["steps"][0]["env"] = {"A": "x", "B": None}
        step = _load(tmp_path, data).steps[0]
        rt = type(step).model_validate(step.model_dump())
        assert rt.env == {"A": "x", "B": None}


class TestSetupProbe:
    def test_defaults_none(self, tmp_path):
        assert _load(tmp_path, _base()).steps[0].setup_probe is None

    def test_parses_with_setup(self, tmp_path):
        data = _base()
        data["steps"] = [{"name": "t", "command": "make",
                          "setup": "make warm-cache",
                          "setup_probe": "file node_modules/esbuild/bin/esbuild | grep -q ELF",
                          "on_pass": "success"}]
        step = _load(tmp_path, data).steps[0]
        assert step.setup_probe == (
            "file node_modules/esbuild/bin/esbuild | grep -q ELF")

    def test_requires_setup(self, tmp_path):
        data = _base()
        data["steps"] = [{"name": "t", "command": "make",
                          "setup_probe": "file node_modules/esbuild/bin/esbuild | grep -q ELF",
                          "on_pass": "success"}]
        with pytest.raises(ValidationError,
                           match="'setup_probe' requires 'setup'"):
            _load(tmp_path, data)

    def test_empty_rejected(self, tmp_path):
        data = _base()
        data["steps"] = [{"name": "t", "command": "make", "setup": "make warm-cache",
                          "setup_probe": "  ", "on_pass": "success"}]
        with pytest.raises(ValidationError, match="non-empty"):
            _load(tmp_path, data)


class TestStepNetwork:
    def test_network_defaults_false(self, tmp_path):
        step = _load(tmp_path, _base()).steps[0]
        assert step.network is False

    def test_network_on_command_step(self, tmp_path):
        data = _base()
        data["steps"] = [{"name": "fetch", "command": "curl https://example.com",
                          "network": True, "on_pass": "success"}]
        assert _load(tmp_path, data).steps[0].network is True

    def test_network_on_agent_step(self, tmp_path):
        data = _base()
        data["steps"] = [{"name": "qa", "agent": "qa", "network": True,
                          "on_pass": "success"}]
        assert _load(tmp_path, data).steps[0].network is True

    def test_network_on_action_step_allowed_at_config_level(self, tmp_path):
        """020: the schema can't distinguish a host-side built-in action from
        a sandbox action, so `network` on an action step is accepted here and
        rejected later (host-side) once the registry is known - see
        `validate_action_network`."""
        data = _base()
        data["steps"] = [{"name": "commit", "action": "commit", "network": True,
                          "on_pass": "success"}]
        assert _load(tmp_path, data).steps[0].network is True

    def test_network_false_on_action_step_allowed(self, tmp_path):
        data = _base()
        data["steps"] = [{"name": "commit", "action": "commit", "network": False,
                          "on_pass": "success"}]
        assert _load(tmp_path, data).steps[0].network is False


class TestDepsPolicy:
    def test_defaults_to_locked_with_declared_lockfiles(self, tmp_path):
        cfg = _load(tmp_path, _base())
        assert cfg.deps_policy == "locked"
        assert cfg.lockfiles == ["uv.lock"]

    def test_locked_without_lockfiles_rejected(self, tmp_path):
        data = {k: v for k, v in _base().items() if k != "lockfiles"}
        with pytest.raises(ValidationError,
                           match="requires at least one entry"):
            _load(tmp_path, data)

    def test_open_policy_loads_without_lockfiles(self, tmp_path):
        data = {k: v for k, v in _base().items() if k != "lockfiles"} \
            | {"deps_policy": "open"}
        assert _load(tmp_path, data).deps_policy == "open"
        assert _load(tmp_path, data).lockfiles == []

    def test_unknown_policy_rejected(self, tmp_path):
        data = _base() | {"deps_policy": "bootstrap"}
        with pytest.raises(ValidationError,
                           match="deps_policy must be 'locked' or 'open'"):
            _load(tmp_path, data)

    def test_lockfiles_accepts_a_list(self, tmp_path):
        data = _base() | {"lockfiles": ["uv.lock", "package-lock.json"]}
        assert _load(tmp_path, data).lockfiles == ["uv.lock", "package-lock.json"]

    def test_lockfiles_accepts_a_single_string(self, tmp_path):
        data = _base() | {"lockfiles": "uv.lock"}
        assert _load(tmp_path, data).lockfiles == ["uv.lock"]


def test_four_keyword_verdict_and_on_env_load(tmp_path):
    data = _base()
    data["steps"][0] = {
        "name": "qa", "agent": "qa", "artifact": "qa-report.md",
        "verdicts": {"pass": "PASS", "fail": "FAIL",
                     "unverifiable": "UNVERIFIABLE", "env": "ENV"},
        "on_pass": "success", "on_unverifiable": "success",
        "on_env": "failure",
    }
    step = _load(tmp_path, data).steps[0]
    assert step.verdicts.pass_ == "PASS"
    assert step.verdicts.fail == "FAIL"
    assert step.verdicts.unverifiable == "UNVERIFIABLE"
    assert step.verdicts.env == "ENV"
    assert step.on_env == "failure"


def test_dangling_on_env_rejected(tmp_path):
    data = _base()
    data["steps"][0]["on_env"] = "nope"
    with pytest.raises(ValidationError, match="unknown route target 'nope'"):
        _load(tmp_path, data)


def test_env_keyword_optional_three_keyword_still_loads(tmp_path):
    data = _base()
    data["steps"][0] = {
        "name": "qa", "agent": "qa", "artifact": "qa-report.md",
        "verdicts": {"pass": "PASS", "fail": "FAIL",
                     "unverifiable": "UNVERIFIABLE"},
        "on_pass": "success", "on_unverifiable": "success",
    }
    step = _load(tmp_path, data).steps[0]
    assert step.verdicts.env is None
    assert step.on_env is None
