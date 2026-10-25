"""Unit-5 pure functions (code-gen step 3): mount resolution, egress assembly,
target matching, profile keying, export planning, and sync-back planning."""

from pathlib import Path

import pytest

from agentic_dev_loop.core.config import EgressSpec, MountSpec
from agentic_dev_loop.engine.egress_policy import (
    ALLOWLIST, BROWSER, EgressPolicy, TargetEntry, assemble_egress_policy,
    match_target,
)
from agentic_dev_loop.engine.isolation import container_profile_key
from agentic_dev_loop.engine.mounts import MountSet, resolve_mount_set
from agentic_dev_loop.engine.step_workspace import plan_step_workspace
from agentic_dev_loop.engine.sync_back import plan_sync_back


class TestResolveMountSet:
    def test_strict_default_empty(self):
        assert resolve_mount_set(None) == MountSet()
        assert resolve_mount_set([]) == MountSet()

    def test_modes_preserved(self):
        ms = resolve_mount_set([MountSpec(path="src", mode="ro"),
                                MountSpec(path="target", mode="rw")])
        assert ms.paths() == ("src", "target")
        assert ms.rw_paths() == ("target",)

    def test_default_ro(self):
        ms = resolve_mount_set([MountSpec(path="src")])
        assert ms.mounts[0].mode == "ro"

    def test_idempotent_round_trip(self):
        specs = [MountSpec(path="src", mode="ro"), MountSpec(path="c", mode="rw")]
        ms = resolve_mount_set(specs)
        assert resolve_mount_set(list(ms.to_specs())) == ms


class TestAssembleEgressPolicy:
    def test_none_is_strict_empty(self):
        p = assemble_egress_policy(None)
        assert p.policy_class == ALLOWLIST and p.targets == ()

    def test_browser_class(self):
        p = assemble_egress_policy(EgressSpec(class_="browser"))
        assert p.policy_class == BROWSER

    def test_preset_expansion_deterministic(self):
        a = assemble_egress_policy(EgressSpec(presets=["package-registries"]))
        b = assemble_egress_policy(EgressSpec(presets=["package-registries"]))
        assert a == b
        hosts = {t.host for t in a.targets}
        assert {"pypi.org", "registry.npmjs.org", "api.nuget.org",
                "crates.io", "github.com"} <= hosts

    def test_explicit_targets_appended(self):
        p = assemble_egress_policy(EgressSpec(targets=["example.com:443"]))
        assert p.targets == (TargetEntry(host="example.com", port=443),)

    def test_malformed_target_loud(self):
        with pytest.raises(ValueError, match="malformed"):
            assemble_egress_policy(EgressSpec(targets=["https://x/y/z"]))

    def test_round_trip(self):
        p = assemble_egress_policy(EgressSpec(targets=["a.com", "b.com:443"]))
        assert assemble_egress_policy(p.to_spec()) == p


class TestMatchTarget:
    def test_browser_allows_anything(self):
        p = EgressPolicy(policy_class=BROWSER)
        assert match_target("anything.example", p)

    def test_exact_match(self):
        p = EgressPolicy(targets=(TargetEntry(host="pypi.org"),))
        assert match_target("pypi.org", p)
        assert not match_target("files.pythonhosted.org", p)

    def test_port_constraint(self):
        p = EgressPolicy(targets=(TargetEntry(host="api.example", port=443),))
        assert match_target("api.example:443", p)
        assert not match_target("api.example:80", p)

    def test_empty_allowlist_denies(self):
        assert not match_target("anything.example", EgressPolicy())


class TestContainerProfileKey:
    def test_stable(self):
        ms = resolve_mount_set([MountSpec(path="src", mode="ro")])
        p = assemble_egress_policy(EgressSpec(targets=["a.com"]))
        assert container_profile_key(ms, p) == container_profile_key(ms, p)

    def test_diverges_on_mounts(self):
        p = assemble_egress_policy(EgressSpec(targets=["a.com"]))
        a = resolve_mount_set([MountSpec(path="src")])
        b = resolve_mount_set([MountSpec(path="tests")])
        assert container_profile_key(a, p) != container_profile_key(b, p)

    def test_diverges_on_policy(self):
        ms = resolve_mount_set([MountSpec(path="src")])
        a = assemble_egress_policy(EgressSpec(targets=["a.com"]))
        b = assemble_egress_policy(EgressSpec(targets=["b.com"]))
        assert container_profile_key(ms, a) != container_profile_key(ms, b)


class TestPlanStepWorkspace:
    def _repo(self, tmp_path):
        repo = tmp_path / "repo"
        (repo / "src").mkdir(parents=True)
        (repo / "src" / "a.py").write_text("print('a')")
        (repo / ".git").mkdir()
        (repo / ".git" / "HEAD").write_text("ref")
        (repo / ".venv").mkdir()
        (repo / ".venv" / "x").write_text("venv")
        (repo / "cache").mkdir()
        (repo / "cache" / "c.bin").write_text("cache")
        return repo

    def test_excludes_git_and_host_only(self, tmp_path):
        repo = self._repo(tmp_path)
        plan = plan_step_workspace(repo, ["cache"], "linux",
                                   tmp_path / "export")
        assert ".git/HEAD" not in plan.copy_paths
        assert ".venv/x" not in plan.copy_paths
        assert "src/a.py" in plan.copy_paths

    def test_cache_excluded_from_copy(self, tmp_path):
        repo = self._repo(tmp_path)
        plan = plan_step_workspace(repo, ["cache"], "linux",
                                   tmp_path / "export")
        assert "cache/c.bin" not in plan.copy_paths
        assert plan.cache_mounts[0].relative == "cache"

    def test_missing_cache_loud(self, tmp_path):
        repo = self._repo(tmp_path)
        with pytest.raises(ValueError, match="does not exist"):
            plan_step_workspace(repo, ["nope"], "linux", tmp_path / "export")

    def test_no_git_in_any_path(self, tmp_path):
        repo = self._repo(tmp_path)
        plan = plan_step_workspace(repo, [], "linux", tmp_path / "export")
        for rel in plan.copy_paths:
            assert ".git" not in rel.split("/")

    def test_paths_stay_inside_export(self, tmp_path):
        repo = self._repo(tmp_path)
        export = tmp_path / "export"
        plan = plan_step_workspace(repo, [], "linux", export)
        for rel in plan.copy_paths:
            assert (export / rel).is_relative_to(export)


class TestPlanSyncBack:
    def test_declared_rw_only(self):
        changes = {"src/a.py": "modified", "tests/b.py": "modified",
                   ".git/HEAD": "modified"}
        plan = plan_sync_back(changes, ["src"])
        assert plan.to_copy == ("src/a.py",)
        assert plan.to_delete == ()
        assert plan.conflicts == ()

    def test_delete_and_conflict(self):
        changes = {"src/a.py": "deleted", "src/b.py": "conflict"}
        plan = plan_sync_back(changes, ["src"])
        assert plan.to_delete == ("src/a.py",)
        assert plan.conflicts == ("src/b.py",)

    def test_git_never_syncs(self):
        plan = plan_sync_back({".git/config": "modified"}, ["."])
        assert plan.to_copy == () and plan.conflicts == ()


class TestPlanSyncBackWholeTreeAndCache:
    def test_dot_declares_whole_tree(self):
        plan = plan_sync_back(
            {"src/a.py": "modified", "docs/new.md": "added"}, ["."])
        assert plan.to_copy == ("docs/new.md", "src/a.py")
        assert plan.to_delete == ()
        assert plan.conflicts == ()

    def test_cache_never_syncs_even_under_dot(self):
        plan = plan_sync_back(
            {"node_modules/dep.js": "deleted", "src/a.py": "modified"},
            ["."], cache_paths=["node_modules"])
        assert plan.to_copy == ("src/a.py",)
        assert plan.to_delete == ()
        assert plan.conflicts == ()

    def test_nested_cache_never_syncs(self):
        plan = plan_sync_back(
            {"qa/.browsers/chrome": "deleted", "src/a.py": "modified"},
            ["."], cache_paths=["qa/.browsers"])
        assert plan.to_copy == ("src/a.py",)
        assert plan.to_delete == ()
