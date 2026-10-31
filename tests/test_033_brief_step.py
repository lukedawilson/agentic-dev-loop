"""Brief step (033): config surface, context assembly, the run-scoped
artifact exemption, and the input-hash regeneration gate."""

import argparse
import subprocess
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from openhands.sdk import LLM

from agentic_dev_loop.core.config import PipelineConfig, load_config
from agentic_dev_loop.engine.graph import run_pipeline
from agentic_dev_loop.engine.registry import default_registry
from agentic_dev_loop.engine.stages import (
    Runtime,
    build_brief_context,
    brief_inputs_hash,
    clear_attempt_artifacts,
    make_brief_node,
    make_bump_node,
    resolve_brief_inputs,
    run_scoped_artifacts,
)


def _base() -> dict:
    return {
        "name": "t",
        "llm": {"model": "m", "api_key_env": "K"},
        "agents_dir": "agents",
        "lockfiles": ["uv.lock"],
        "steps": [{"name": "dev", "agent": "dev", "on_pass": "success"}],
    }


def _load(tmp_path, data):
    p = tmp_path / "pipeline.yaml"
    p.write_text(yaml.safe_dump(data))
    return load_config(p)


BRIEF_OK = (
    "## Repo rules digest\n\nREPO RULES\n\n"
    "## Unit scope digest\n\nSCOPE\n\n"
    "## Environment facts\n\n- offline; never fetch\n\n"
    "## Open risks\n\nNone\n"
)
BRIEF_OK_NEW = BRIEF_OK.replace("SCOPE", "SCOPE NEW")


BRIEF_STEP = {
    "name": "brief",
    "agent": "brief",
    "produces": "attempt-brief.md",
    "max_iterations": 12,
    "brief_inputs": {
        "include": ["AGENTS.md", "CLAUDE.md"],
        "env_facts": ["the workspace is an export with no .git; verify with find/ls, never git status"],
    },
    "on_pass": "dev",
}


class TestBriefInputsConfig:
    def test_round_trips(self, tmp_path):
        data = _base()
        data["steps"] = [BRIEF_STEP, {"name": "dev", "agent": "dev",
                                      "on_pass": "success"}]
        cfg = _load(tmp_path, data)
        brief = cfg.steps[0]
        assert brief.brief_inputs.include == ["AGENTS.md", "CLAUDE.md"]
        assert brief.brief_inputs.env_facts == [
            "the workspace is an export with no .git; verify with find/ls, never git status"
        ]
        assert brief.produces == ["attempt-brief.md"]
        assert brief.max_iterations == 12

    def test_single_strings_coerced(self, tmp_path):
        data = _base()
        data["steps"] = [{
            "name": "brief", "agent": "brief", "produces": "attempt-brief.md",
            "brief_inputs": {"include": "AGENTS.md",
                             "env_facts": "offline; never fetch"},
            "on_pass": "success",
        }]
        brief = _load(tmp_path, data).steps[0]
        assert brief.brief_inputs.include == ["AGENTS.md"]
        assert brief.brief_inputs.env_facts == ["offline; never fetch"]

    def test_steps_without_brief_inputs_default_none(self, tmp_path):
        assert _load(tmp_path, _base()).steps[0].brief_inputs is None

    def test_unknown_key_rejected(self, tmp_path):
        data = _base()
        data["steps"] = [{
            "name": "brief", "agent": "brief", "produces": "attempt-brief.md",
            "brief_inputs": {"include": ["AGENTS.md"], "bogus": 1},
            "on_pass": "success",
        }]
        with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
            _load(tmp_path, data)

    def test_rejected_on_command_step(self, tmp_path):
        data = _base()
        data["steps"] = [{
            "name": "brief", "command": "true",
            "brief_inputs": {"include": ["AGENTS.md"]},
        }]
        with pytest.raises(ValidationError, match="'brief_inputs' requires 'agent'"):
            _load(tmp_path, data)

    def test_rejected_on_action_step(self, tmp_path):
        data = _base()
        data["steps"] = [{
            "name": "brief", "action": "commit",
            "brief_inputs": {"include": ["AGENTS.md"]},
        }]
        with pytest.raises(ValidationError, match="'brief_inputs' requires 'agent'"):
            _load(tmp_path, data)

    def test_requires_produces(self, tmp_path):
        data = _base()
        data["steps"] = [{
            "name": "brief", "agent": "brief",
            "brief_inputs": {"include": ["AGENTS.md"]},
            "on_pass": "success",
        }]
        with pytest.raises(ValidationError,
                           match="'brief_inputs' requires 'produces'"):
            _load(tmp_path, data)

    def test_rejects_artifact_verdicts(self, tmp_path):
        data = _base()
        data["steps"] = [{
            "name": "brief", "agent": "brief",
            "artifact": "brief.md",
            "verdicts": {"pass": "PASS", "fail": "FAIL"},
            "brief_inputs": {"include": ["AGENTS.md"]},
            "on_pass": "success",
        }]
        with pytest.raises(ValidationError,
                           match="produces-only agent step"):
            _load(tmp_path, data)

    @pytest.mark.parametrize("bad", ["/etc/passwd", "../AGENTS.md", ".git/config"])
    def test_include_rejects_unsafe_paths(self, tmp_path, bad):
        data = _base()
        data["steps"] = [{
            "name": "brief", "agent": "brief", "produces": "attempt-brief.md",
            "brief_inputs": {"include": [bad]},
            "on_pass": "success",
        }]
        with pytest.raises(ValidationError):
            _load(tmp_path, data)

    def test_env_facts_empty_entry_rejected(self, tmp_path):
        data = _base()
        data["steps"] = [{
            "name": "brief", "agent": "brief", "produces": "attempt-brief.md",
            "brief_inputs": {"include": ["AGENTS.md"], "env_facts": [""]},
            "on_pass": "success",
        }]
        with pytest.raises(ValidationError, match="non-empty"):
            _load(tmp_path, data)


    def test_sections_default_neutral(self, tmp_path):
        data = _base()
        data["steps"] = [BRIEF_STEP, {"name": "dev", "agent": "dev",
                                      "on_pass": "success"}]
        brief = _load(tmp_path, data).steps[0]
        assert brief.brief_inputs.sections == [
            "Repo rules digest", "Unit scope digest",
            "Environment facts", "Open risks",
        ]

    def test_sections_override(self, tmp_path):
        data = _base()
        step = dict(BRIEF_STEP)
        step["brief_inputs"] = {**step["brief_inputs"],
                                "sections": ["Rules", "Scope", "Risks"]}
        data["steps"] = [step, {"name": "dev", "agent": "dev",
                                "on_pass": "success"}]
        brief = _load(tmp_path, data).steps[0]
        assert brief.brief_inputs.sections == ["Rules", "Scope", "Risks"]

    def test_sections_empty_rejected(self, tmp_path):
        data = _base()
        data["steps"] = [{
            "name": "brief", "agent": "brief", "produces": "attempt-brief.md",
            "brief_inputs": {"include": ["AGENTS.md"], "sections": []},
            "on_pass": "success",
        }]
        with pytest.raises(ValidationError, match="non-empty list"):
            _load(tmp_path, data)

    def test_sections_duplicate_rejected(self, tmp_path):
        data = _base()
        data["steps"] = [{
            "name": "brief", "agent": "brief", "produces": "attempt-brief.md",
            "brief_inputs": {"include": ["AGENTS.md"],
                             "sections": ["Rules", "Rules"]},
            "on_pass": "success",
        }]
        with pytest.raises(ValidationError, match="unique"):
            _load(tmp_path, data)


def make_cfg(steps, **over):
    base = {"name": "t", "llm": {"model": "m", "api_key_env": "K"},
            "agents_dir": "agents", "lockfiles": ["uv.lock"], "steps": steps}
    base.update(over)
    return PipelineConfig.model_validate(base)


def make_state(**over):
    state = {"subject": "unit u", "docs": {"plan.md": "THE PLAN"},
             "artifacts": {}, "attempt": 1, "max_attempts": 3, "notes": [],
             "failed": False, "step_verdicts": {}, "retry_target": None,
             "thread_id": "t1", "brief_hashes": {}}
    state.update(over)
    return state


@pytest.fixture
def runtime(tmp_path, fake_sandbox):
    state_dir = tmp_path / ".pr"
    state_dir.mkdir()
    (tmp_path / "AGENTS.md").write_text("REPO RULES")
    (tmp_path / "CLAUDE.md").write_text("CLAUDE RULES")
    (tmp_path / "README.md").write_text("README")
    (tmp_path / "standards.yaml").write_text("STANDARDS")
    steps = [
        {"name": "brief", "agent": "brief", "produces": "attempt-brief.md",
         "max_iterations": 12,
         "brief_inputs": {"include": ["AGENTS.md", "CLAUDE.md"],
                          "env_facts": ["offline; never fetch"]},
         "on_pass": "dev"},
        {"name": "dev", "agent": "dev", "produces": "implementation-summary.md",
         "on_pass": "success"},
    ]
    return Runtime(cfg=make_cfg(steps), registry=default_registry(),
                   llm=LLM(model="openai/test-model", api_key="sk-test"),
                   agents_dir=tmp_path / "agents", state_dir=state_dir,
                   workspace=tmp_path, sandbox=fake_sandbox)


class TestResolveBriefInputs:
    def test_include_literal_and_env_facts(self, runtime):
        included, facts = resolve_brief_inputs(runtime, runtime.cfg.steps[0])
        assert included == {"AGENTS.md": "REPO RULES",
                            "CLAUDE.md": "CLAUDE RULES"}
        assert facts == ["offline; never fetch"]

    def test_unknown_include_fails_loud(self, runtime):
        step = make_cfg([{
            "name": "brief", "agent": "brief", "produces": "attempt-brief.md",
            "brief_inputs": {"include": ["NO-SUCH.md"]},
            "on_pass": "success",
        }]).steps[0]
        with pytest.raises(FileNotFoundError, match="NO-SUCH.md"):
            resolve_brief_inputs(runtime, step)

    def test_glob_resolves_sorted_files(self, tmp_path, fake_sandbox):
        state_dir = tmp_path / ".pr"; state_dir.mkdir()
        (tmp_path / "a.md").write_text("A")
        (tmp_path / "b.md").write_text("B")
        cfg = make_cfg([{
            "name": "brief", "agent": "brief", "produces": "attempt-brief.md",
            "brief_inputs": {"include": ["*.md"]},
            "on_pass": "success",
        }])
        rt = Runtime(cfg=cfg, registry=default_registry(),
                     llm=LLM(model="openai/test-model", api_key="sk-test"),
                     agents_dir=tmp_path / "agents", state_dir=state_dir,
                     workspace=tmp_path, sandbox=fake_sandbox)
        included, _ = resolve_brief_inputs(rt, rt.cfg.steps[0])
        assert list(included) == ["a.md", "b.md"]

    def test_glob_matching_nothing_fails_loud(self, runtime):
        step = make_cfg([{
            "name": "brief", "agent": "brief", "produces": "attempt-brief.md",
            "brief_inputs": {"include": ["*.nope"]},
            "on_pass": "success",
        }]).steps[0]
        with pytest.raises(FileNotFoundError, match="\\*.nope"):
            resolve_brief_inputs(runtime, step)

    def test_literal_directory_fails_loud(self, runtime, tmp_path):
        (tmp_path / "adir").mkdir()
        step = make_cfg([{
            "name": "brief", "agent": "brief", "produces": "attempt-brief.md",
            "brief_inputs": {"include": ["adir"]},
            "on_pass": "success",
        }]).steps[0]
        with pytest.raises(FileNotFoundError, match="adir"):
            resolve_brief_inputs(runtime, step)


class TestBuildBriefContext:
    def test_docs_include_and_env_facts_render(self, runtime):
        ctx = build_brief_context(runtime, runtime.cfg.steps[0],
                                  {"plan.md": "THE PLAN"})
        assert "## plan.md\n\nTHE PLAN" in ctx
        assert "## AGENTS.md\n\nREPO RULES" in ctx
        assert "## CLAUDE.md\n\nCLAUDE RULES" in ctx
        assert "## Environment facts\n\n- offline; never fetch" in ctx
        assert "Previous attempt feedback" not in ctx
        assert "Stage artifacts" not in ctx

    def test_include_does_not_duplicate_docs(self, runtime):
        ctx = build_brief_context(runtime, runtime.cfg.steps[0],
                                  {"AGENTS.md": "REPO RULES FROM DOCS"})
        assert ctx.count("## AGENTS.md") == 1
        assert "REPO RULES FROM DOCS" in ctx

    def test_custom_prompt_between_docs_and_env_facts(self, runtime):
        step = make_cfg([{
            "name": "brief", "agent": "brief", "produces": "attempt-brief.md",
            "brief_inputs": {"include": ["AGENTS.md"], "env_facts": ["offline"]},
            "custom_prompt": "brief scope", "on_pass": "success",
        }]).steps[0]
        ctx = build_brief_context(runtime, step, {"plan.md": "THE PLAN"})
        assert "## Run-specific instructions\n\nbrief scope" in ctx
        assert ctx.count("## Run-specific instructions") == 1
        assert ctx.index("## plan.md") < ctx.index("## Run-specific instructions")
        assert ctx.index("## Run-specific instructions") < ctx.index("## Environment facts")


class TestBriefInputsHash:
    def test_stable_and_content_sensitive(self, runtime):
        step = runtime.cfg.steps[0]
        h1 = brief_inputs_hash(runtime, step)
        assert h1 == brief_inputs_hash(runtime, step)
        (runtime.workspace / "AGENTS.md").write_text("CHANGED")
        assert brief_inputs_hash(runtime, step) != h1

    def test_env_facts_are_hashed(self, runtime):
        step = runtime.cfg.steps[0]
        h1 = brief_inputs_hash(runtime, step)
        changed = step.model_copy(deep=True)
        changed.brief_inputs.env_facts.append("new fact")
        assert brief_inputs_hash(runtime, changed) != h1

    def test_loader_docs_are_hashed(self, runtime):
        step = runtime.cfg.steps[0]
        h1 = brief_inputs_hash(runtime, step, {"plan.md": "PLAN A"})
        h2 = brief_inputs_hash(runtime, step, {"plan.md": "PLAN B"})
        assert h1 != h2


class TestBriefNode:
    def test_first_run_calls_agent_and_stores_hash(self, runtime, fake_agents):
        fake_agents.set("brief", [("write", "attempt-brief.md", BRIEF_OK)])
        update = make_brief_node(runtime, runtime.cfg.steps[0])(make_state())
        assert update["step_verdicts"] == {"brief": "pass"}
        assert update["failed"] is False
        assert update["artifacts"] == {"brief": BRIEF_OK}
        assert update["brief_hashes"] == {
            "brief": brief_inputs_hash(runtime, runtime.cfg.steps[0],
                                       make_state()["docs"])}
        (msg,) = fake_agents.messages_for("brief")
        assert "## AGENTS.md\n\nREPO RULES" in msg
        assert "## Environment facts\n\n- offline; never fetch" in msg

    def test_unchanged_inputs_skip_llm(self, runtime, fake_agents):
        fake_agents.set("brief", [("write", "attempt-brief.md", BRIEF_OK)])
        node = make_brief_node(runtime, runtime.cfg.steps[0])
        first = node(make_state())
        second = node(make_state(artifacts=first["artifacts"],
                                 brief_hashes=first["brief_hashes"]))
        assert second == {"step_verdicts": {"brief": "pass"}, "failed": False}
        assert fake_agents.agents_called() == ["brief"]

    def test_changed_inputs_rerun(self, runtime, fake_agents):
        fake_agents.set("brief", [
            ("write", "attempt-brief.md", BRIEF_OK),
            ("write", "attempt-brief.md", BRIEF_OK_NEW),
        ])
        node = make_brief_node(runtime, runtime.cfg.steps[0])
        first = node(make_state())
        (runtime.workspace / "AGENTS.md").write_text("EDITED")
        second = node(make_state(artifacts=first["artifacts"],
                                 brief_hashes=first["brief_hashes"]))
        assert second["artifacts"] == {"brief": BRIEF_OK_NEW}
        assert fake_agents.agents_called() == ["brief", "brief"]

    def test_loader_doc_disk_edit_reruns_brief(self, runtime, fake_agents,
                                                  tmp_path):
        plan = tmp_path / "plan.md"
        plan.write_text("PLAN V1")
        fake_agents.set("brief", [
            ("write", "attempt-brief.md", BRIEF_OK),
            ("write", "attempt-brief.md", BRIEF_OK_NEW),
        ])
        node = make_brief_node(runtime, runtime.cfg.steps[0])
        first = node(make_state(docs={"plan.md": "PLAN V1"},
                                doc_sources={"plan.md": str(plan)}))
        assert fake_agents.agents_called() == ["brief"]
        plan.write_text("PLAN V2")
        second = node(make_state(docs={"plan.md": "PLAN V1"},
                                 doc_sources={"plan.md": str(plan)},
                                 artifacts=first["artifacts"],
                                 brief_hashes=first["brief_hashes"]))
        assert second["artifacts"] == {"brief": BRIEF_OK_NEW}
        assert fake_agents.agents_called() == ["brief", "brief"]

    def test_empty_response_is_unclear(self, runtime, fake_agents):
        fake_agents.set("brief", [("ok",)])
        update = make_brief_node(runtime, runtime.cfg.steps[0])(make_state())
        assert update["step_verdicts"] == {"brief": None}
        assert update["retry_target"] == "brief"
        assert "produced no final response" in update["notes"][0]


class TestRunScopedHygiene:
    def test_attempt_clear_exempts_brief(self, runtime):
        brief = runtime.state_dir / "attempt-brief.md"
        dev = runtime.state_dir / "implementation-summary.md"
        last = runtime.state_dir / "last-error.md"
        for p in (brief, dev, last):
            p.write_text("stale")
        clear_attempt_artifacts(runtime)
        assert brief.exists()
        assert not dev.exists() and not last.exists()

    def test_run_start_clear_removes_brief(self, runtime):
        brief = runtime.state_dir / "attempt-brief.md"
        brief.write_text("stale")
        clear_attempt_artifacts(runtime, exempt_run_scoped=False)
        assert not brief.exists()

    def test_run_scoped_artifacts_lists_only_brief(self, runtime):
        assert run_scoped_artifacts(runtime.cfg) == ["attempt-brief.md"]


class TestBumpRegenerationGate:
    def _rt(self, runtime):
        return runtime

    def test_unchanged_inputs_keep_retry_target(self, runtime):
        step = runtime.cfg.steps[0]
        h = brief_inputs_hash(runtime, step, make_state()["docs"])
        state = make_state(attempt=1, step_verdicts={"dev": "fail"},
                           retry_target="dev", brief_hashes={"brief": h},
                           artifacts={"brief": "B"})
        update = make_bump_node(runtime)(state)
        assert update == {"attempt": 2, "step_verdicts": {}, "failed": False}

    def test_changed_inputs_redirect_to_brief(self, runtime):
        step = runtime.cfg.steps[0]
        state = make_state(attempt=1, step_verdicts={"dev": "fail"},
                           retry_target="dev", brief_hashes={"brief": "stale"},
                           artifacts={"brief": "B"})
        update = make_bump_node(runtime)(state)
        assert update["retry_target"] == "brief"

    def test_changed_docs_redirect_to_brief(self, runtime):
        step = runtime.cfg.steps[0]
        h = brief_inputs_hash(runtime, step, {"plan.md": "OLD PLAN"})
        state = make_state(attempt=1, step_verdicts={"dev": "fail"},
                           retry_target="dev", docs={"plan.md": "NEW PLAN"},
                           brief_hashes={"brief": h}, artifacts={"brief": "B"})
        update = make_bump_node(runtime)(state)
        assert update["retry_target"] == "brief"

    def test_changed_loader_doc_on_disk_redirects_to_brief(self, runtime,
                                                            tmp_path):
        plan = tmp_path / "plan.md"
        plan.write_text("PLAN V1")
        step = runtime.cfg.steps[0]
        h = brief_inputs_hash(runtime, step, {"plan.md": "PLAN V1"},
                              {"plan.md": str(plan)})
        plan.write_text("PLAN V2")
        state = make_state(attempt=1, step_verdicts={"dev": "fail"},
                           retry_target="dev", docs={"plan.md": "PLAN V1"},
                           doc_sources={"plan.md": str(plan)},
                           brief_hashes={"brief": h}, artifacts={"brief": "B"})
        update = make_bump_node(runtime)(state)
        assert update["retry_target"] == "brief"

    def test_unchanged_loader_doc_on_disk_keeps_retry_target(self, runtime,
                                                              tmp_path):
        plan = tmp_path / "plan.md"
        plan.write_text("PLAN V1")
        step = runtime.cfg.steps[0]
        h = brief_inputs_hash(runtime, step, {"plan.md": "PLAN V1"},
                              {"plan.md": str(plan)})
        state = make_state(attempt=1, step_verdicts={"dev": "fail"},
                           retry_target="dev", docs={"plan.md": "PLAN V1"},
                           doc_sources={"plan.md": str(plan)},
                           brief_hashes={"brief": h}, artifacts={"brief": "B"})
        update = make_bump_node(runtime)(state)
        assert update == {"attempt": 2, "step_verdicts": {}, "failed": False}

    def test_no_brief_step_is_backward_compatible(self, tmp_path, fake_sandbox):
        state_dir = tmp_path / ".pr"; state_dir.mkdir()
        rt = Runtime(cfg=make_cfg([
            {"name": "dev", "agent": "dev", "produces": "s.md",
             "on_pass": "success"}]), registry=default_registry(),
            llm=LLM(model="openai/test-model", api_key="sk-test"),
            agents_dir=tmp_path / "agents", state_dir=state_dir,
            workspace=tmp_path, sandbox=fake_sandbox)
        state = make_state(attempt=1, step_verdicts={"dev": "fail"},
                           retry_target="dev", brief_hashes={})
        update = make_bump_node(rt)(state)
        assert update == {"attempt": 2, "step_verdicts": {}, "failed": False}


class TestDryRunBriefInputs:
    def test_unknown_include_fails_dry_run(self, tmp_path, monkeypatch):
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "pipeline.yaml").write_text(
            "name: brief-dry\n"
            "llm:\n  model: m\n  api_key_env: K\n"
            "agents_dir: agents\n"
            "deps_policy: open\n"
            "steps:\n"
            "  - name: brief\n"
            "    agent: brief\n"
            "    produces: attempt-brief.md\n"
            "    brief_inputs:\n"
            "      include: [NO-SUCH.md]\n"
            "    on_pass: success\n")
        agents = repo / "agents"
        agents.mkdir()
        monkeypatch.chdir(repo)
        from agentic_dev_loop.engine.dryrun import dry_run
        with pytest.raises(FileNotFoundError, match="NO-SUCH.md"):
            dry_run("pipeline.yaml",
                    argparse.Namespace(plan=None, resume=None,
                                       max_attempts=None))

    def test_valid_include_passes_dry_run(self, tmp_path, monkeypatch):
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "AGENTS.md").write_text("REPO RULES")
        (repo / "pipeline.yaml").write_text(
            "name: brief-dry\n"
            "llm:\n  model: m\n  api_key_env: K\n"
            "agents_dir: agents\n"
            "deps_policy: open\n"
            "steps:\n"
            "  - name: brief\n"
            "    agent: brief\n"
            "    produces: attempt-brief.md\n"
            "    brief_inputs:\n"
            "      include: [AGENTS.md]\n"
            "    on_pass: success\n")
        agents = repo / "agents"
        agents.mkdir()
        monkeypatch.chdir(repo)
        from agentic_dev_loop.engine.dryrun import dry_run
        assert dry_run("pipeline.yaml",
                       argparse.Namespace(plan=None, resume=None,
                                          max_attempts=None)) == 0


BRIEF_PIPELINE = """\
name: brief-loop
llm:
  model: openai/test-model
  api_key_env: TEST_LLM_KEY
state_dir: .pr
max_attempts: 3
agents_dir: sdk_agents
deps_policy: open
steps:
  - name: brief
    agent: brief
    produces: attempt-brief.md
    max_iterations: 12
    brief_inputs:
      include: [AGENTS.md]
      env_facts:
        - the workspace is an export with no .git; verify with find/ls, never git status
    on_pass: dev
  - name: dev
    agent: dev
    produces: implementation-summary.md
    on_pass: commit
  - name: commit
    action: commit
    params: { message: "feat: construct {subject} (agent dev loop)" }
    on_pass: success
"""


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True,
                          capture_output=True, text=True).stdout.strip()


def _args(**over):
    base = {"plan": None, "resume": None, "max_attempts": None}
    base.update(over)
    return argparse.Namespace(**base)


class TestBriefPipeline:
    def test_brief_runs_once_and_reaches_dev(self, tmp_path, monkeypatch,
                                             fake_sandbox, fake_agents):
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "pipeline.yaml").write_text(BRIEF_PIPELINE)
        (repo / "AGENTS.md").write_text("REPO RULES")
        _git(repo, "init")
        _git(repo, "config", "user.email", "test@example.com")
        _git(repo, "config", "user.name", "Test")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-m", "init")
        monkeypatch.chdir(repo)
        monkeypatch.setenv("TEST_LLM_KEY", "sk-test")

        brief_digest = (
            "## Repo rules digest\n\nREPO RULES\n\n"
            "## Unit scope digest\n\nBRIEF DIGEST\n\n"
            "## Environment facts\n\n"
            "- the workspace is an export with no .git; verify with find/ls, never git status\n\n"
            "## Open risks\n\nNone\n"
        )
        fake_agents.set("brief", [("write", "attempt-brief.md", brief_digest)])
        fake_agents.set("dev", [("write", "implementation-summary.md", "did the work")])

        ok, attempts = run_pipeline("pipeline.yaml", _args())
        assert (ok, attempts) == (True, 1)
        assert fake_agents.agents_called() == ["brief", "dev"]
        (dev_msg,) = fake_agents.messages_for("dev")
        assert "## Stage artifacts" in dev_msg
        assert "BRIEF DIGEST" in dev_msg
        (brief_msg,) = fake_agents.messages_for("brief")
        assert "## AGENTS.md\n\nREPO RULES" in brief_msg
        assert "## Environment facts" in brief_msg
        assert _git(repo, "log", "-1", "--pretty=%s") == \
            "feat: construct brief-loop (agent dev loop)"
