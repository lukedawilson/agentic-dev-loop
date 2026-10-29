"""Unit-5 sync-back runtime (code-gen step 6): shutil diff-copy, declared-rw
only, .git never touched, conflicts loud."""

import hashlib
import os

import pytest

from agentic_dev_loop.engine.sync_back import merge_append_only, sync_back


def _digest(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _workspace(tmp_path):
    ws = tmp_path / "ws"
    (ws / "src").mkdir(parents=True)
    (ws / "src" / "a.py").write_text("orig-a")
    (ws / "src" / "b.py").write_text("orig-b")
    (ws / "tests").mkdir()
    (ws / "tests" / "t.py").write_text("orig-t")
    (ws / ".git").mkdir()
    (ws / ".git" / "HEAD").write_text("ref")
    return ws


def _export(tmp_path, ws):
    exp = tmp_path / "exp"
    exp.mkdir()
    for rel in ("src/a.py", "src/b.py", "tests/t.py"):
        src = ws / rel
        (exp / rel).parent.mkdir(parents=True, exist_ok=True)
        (exp / rel).write_text(src.read_text())
    return exp


def test_added_modified_deleted_sync(tmp_path):
    ws = _workspace(tmp_path)
    exp = _export(tmp_path, ws)
    (exp / "src" / "a.py").write_text("changed-a")       # modified
    (exp / "src" / "new.py").write_text("new")           # added
    (exp / "src" / "b.py").unlink()                      # deleted
    baseline = {"src/a.py": _digest(ws / "src" / "a.py"),
                "src/b.py": _digest(ws / "src" / "b.py")}
    report = sync_back(exp, ws, ["src"], baseline=baseline)
    assert report.conflicts == ()
    assert set(report.synced_paths) == {"src/a.py", "src/new.py", "src/b.py"}
    assert (ws / "src" / "a.py").read_text() == "changed-a"
    assert (ws / "src" / "new.py").read_text() == "new"
    assert not (ws / "src" / "b.py").exists()


def test_non_declared_never_syncs(tmp_path):
    ws = _workspace(tmp_path)
    exp = _export(tmp_path, ws)
    (exp / "tests" / "t.py").write_text("changed-t")
    report = sync_back(exp, ws, ["src"], baseline={})
    assert report.synced_paths == ()
    assert (ws / "tests" / "t.py").read_text() == "orig-t"


def test_git_never_touched(tmp_path):
    ws = _workspace(tmp_path)
    exp = _export(tmp_path, ws)
    # simulate an export-side .git that must never be copied back
    (exp / ".git").mkdir(exist_ok=True)
    (exp / ".git" / "HEAD").write_text("tampered")
    report = sync_back(exp, ws, ["."], baseline={})
    assert ".git" not in report.synced_paths
    assert (ws / ".git" / "HEAD").read_text() == "ref"


def test_conflict_is_loud(tmp_path):
    ws = _workspace(tmp_path)
    exp = _export(tmp_path, ws)
    (exp / "src" / "a.py").write_text("agent-change")
    (ws / "src" / "a.py").write_text("external-change")
    # baseline is the pre-change content; both moved -> conflict
    baseline = {"src/a.py": hashlib.sha256(b"orig-a").hexdigest()}
    report = sync_back(exp, ws, ["src"], baseline=baseline)
    assert report.conflicts == ("src/a.py",)
    assert report.synced_paths == ()
    assert (ws / "src" / "a.py").read_text() == "external-change"


def test_whole_tree_dot_syncs_back(tmp_path):
    """BR-4/BR-1.2: a ``.``-rw declaration syncs the whole (non-forbidden,
    non-cache) tree - the dev agent's code edits land in the real repo."""
    ws = _workspace(tmp_path)
    exp = _export(tmp_path, ws)
    (exp / "src" / "a.py").write_text("changed-a")        # modified
    (exp / "docs").mkdir()
    (exp / "docs" / "new.md").write_text("new")           # added under .
    baseline = {"src/a.py": _digest(ws / "src" / "a.py"),
                "src/b.py": _digest(ws / "src" / "b.py")}
    report = sync_back(exp, ws, ["."], baseline=baseline)
    assert report.conflicts == ()
    assert set(report.synced_paths) == {"src/a.py", "docs/new.md"}
    assert (ws / "src" / "a.py").read_text() == "changed-a"
    assert (ws / "docs" / "new.md").read_text() == "new"


def test_symlink_syncs_as_symlink_never_resolved(tmp_path):
    """A symlink the sandbox creates in a declared-rw path syncs back as a
    symlink, including a dangling one (whose target only makes sense inside
    the export). Resolving it against the host would crash or corrupt."""
    ws = _workspace(tmp_path)
    exp = _export(tmp_path, ws)
    (exp / "links").mkdir()
    (exp / "links" / "to-src").symlink_to("../src/a.py")
    (exp / "links" / "dangling").symlink_to("no-such-target")
    report = sync_back(exp, ws, declared_rw=["."])
    assert report.conflicts == ()
    assert set(report.synced_paths) == {"links/to-src", "links/dangling"}
    assert (ws / "links" / "to-src").is_symlink()
    assert os.readlink(ws / "links" / "to-src") == "../src/a.py"
    assert (ws / "links" / "dangling").is_symlink()
    assert os.readlink(ws / "links" / "dangling") == "no-such-target"


def test_cache_not_deleted(tmp_path):
    """BR-3/F2: a bind-mounted cache is absent from the export but present in
    the real repo - a ``.``-rw sync-back must never classify it as a deletion
    and delete the real workspace's cache."""
    ws = _workspace(tmp_path)
    (ws / "node_modules").mkdir()
    (ws / "node_modules" / "dep.js").write_text("cache")
    exp = _export(tmp_path, ws)
    (exp / "node_modules").mkdir()  # pre-created empty cache mount target
    (exp / "src" / "a.py").write_text("changed-a")
    baseline = {"src/a.py": _digest(ws / "src" / "a.py"),
                "src/b.py": _digest(ws / "src" / "b.py")}
    report = sync_back(exp, ws, ["."], baseline=baseline,
                       cache_paths=["node_modules"])
    assert report.conflicts == ()
    assert "node_modules/dep.js" not in report.synced_paths
    assert (ws / "node_modules" / "dep.js").read_text() == "cache"
    assert (ws / "src" / "a.py").read_text() == "changed-a"


class TestSyncExcludes:
    """028: declared sync-excludes are scratch - never copied into the export
    and never classified as add/modify/delete/conflict."""

    def _ws(self, tmp_path):
        ws = _workspace(tmp_path)
        (ws / "src" / "proj" / "bin").mkdir(parents=True)
        (ws / "src" / "proj" / "bin" / "a.dll").write_text("bin-a")
        (ws / "src" / "proj" / "obj").mkdir(parents=True)
        (ws / "src" / "proj" / "obj" / "b.o").write_text("obj-b")
        return ws

    def test_literal_exclude_skips_all_buckets(self, tmp_path):
        ws = self._ws(tmp_path)
        exp = _export(tmp_path, ws)
        (exp / "qa" / ".artifacts").mkdir(parents=True)
        (exp / "qa" / ".artifacts" / "new").write_text("added")
        (exp / "qa" / ".artifacts" / "old").write_text("modified")
        (ws / "qa" / ".artifacts").mkdir(parents=True, exist_ok=True)
        (ws / "qa" / ".artifacts" / "deleted").write_text("gone")
        (exp / "qa" / ".artifacts" / "conflict").write_text("exp-move")
        (ws / "qa" / ".artifacts" / "conflict").write_text("real-move")
        baseline = {"qa/.artifacts/old": hashlib.sha256(b"old").hexdigest(),
                    "qa/.artifacts/conflict": hashlib.sha256(b"base").hexdigest()}
        report = sync_back(exp, ws, ["."], baseline=baseline,
                           excludes=["qa/.artifacts"])
        assert report.conflicts == ()
        assert all("qa/.artifacts" not in p for p in report.synced_paths)

    def test_wildcard_bin_obj_deep_nesting(self, tmp_path):
        ws = self._ws(tmp_path)
        exp = _export(tmp_path, ws)
        (exp / "src" / "proj" / "bin").mkdir(parents=True, exist_ok=True)
        (exp / "src" / "proj" / "obj").mkdir(parents=True, exist_ok=True)
        (exp / "src" / "proj" / "bin" / "new.dll").write_text("new")
        (exp / "src" / "proj" / "obj" / "new.o").write_text("new")
        (exp / "src" / "a.py").write_text("changed-a")
        baseline = {"src/a.py": _digest(ws / "src" / "a.py")}
        report = sync_back(exp, ws, ["."], baseline=baseline,
                           excludes=["**/bin/**", "**/obj/**"])
        assert report.conflicts == ()
        assert report.synced_paths == ("src/a.py",)
        assert not (ws / "src" / "proj" / "bin" / "new.dll").exists()
        assert not (ws / "src" / "proj" / "obj" / "new.o").exists()

    def test_host_side_drift_no_longer_conflicts(self, tmp_path):
        """028: host-side obj/bin drift is excluded from both snapshots, so it
        can no longer raise a BR-4.4 conflict (AE-SBX-016)."""
        ws = self._ws(tmp_path)
        exp = _export(tmp_path, ws)
        (ws / "src" / "proj" / "bin" / "a.dll").write_text("host-drift")
        (exp / "src" / "a.py").write_text("agent-change")
        baseline = {"src/a.py": _digest(ws / "src" / "a.py")}
        report = sync_back(exp, ws, ["."], baseline=baseline,
                           excludes=["**/bin/**", "**/obj/**"])
        assert report.conflicts == ()
        assert report.synced_paths == ("src/a.py",)

    def test_star_spans_separators(self, tmp_path):
        ws = _workspace(tmp_path)
        exp = _export(tmp_path, ws)
        (exp / "src" / "proj").mkdir(parents=True, exist_ok=True)
        (exp / "src" / "proj" / "x.dll").write_text("new")
        (exp / "root.dll").write_text("new")
        report = sync_back(exp, ws, ["."], excludes=["*.dll"])
        assert report.synced_paths == ()

    def test_plan_sync_back_deterministic_with_excludes(self):
        from agentic_dev_loop.engine.sync_back import plan_sync_back
        changes = {
            "src/a.py": "modified",
            "src/bin/x.dll": "modified",
            "src/bin/y.dll": "added",
            "src/obj/z.o": "deleted",
            "src/bin/c.dll": "conflict",
        }
        kwargs = dict(declared_rw=["."], excludes=["**/bin/**", "**/obj/**"])
        a = plan_sync_back(changes, **kwargs)
        b = plan_sync_back(changes, **kwargs)
        assert a == b
        assert a.to_copy == ("src/a.py",)
        assert a.to_delete == ()
        assert a.conflicts == ()


class TestMergeAppendOnly:
    def test_identical_sides_are_noop(self):
        assert merge_append_only("a\nb\n", "a\nb\n") == "a\nb\n"

    def test_host_only_lines_survive(self):
        assert merge_append_only("a\nb\nhost\n", "a\nb\n") == "a\nb\nhost\n"

    def test_export_only_lines_appended_in_order(self):
        assert merge_append_only(
            "a\nb\n", "a\nb\nexport1\nexport2\n") == "a\nb\nexport1\nexport2\n"

    def test_both_appended_tails_union(self):
        assert merge_append_only(
            "a\nb\nhost-new\n", "a\nb\nexport-new\n") == "a\nb\nhost-new\nexport-new\n"

    def test_deletions_never_applied_and_duplicates_kept(self):
        assert merge_append_only("a\nb\n", "a\nb\na\n") == "a\nb\na\n"

    def test_empty_host_is_export_text(self):
        assert merge_append_only("", "a\n") == "a\n"

    def test_no_export_only_lines_returns_host_verbatim(self):
        host = "a\nb"
        assert merge_append_only(host, "a\nb") is host


class TestMergeOnConflictPartition:
    def test_merge_conflict_partitioned_and_non_conflicting_copied(self, tmp_path):
        ws = _workspace(tmp_path)
        exp = _export(tmp_path, ws)
        (ws / "src" / "audit.md").write_text("base\nhost-line\n")
        (exp / "src" / "audit.md").write_text("base\nexport-line\n")
        (exp / "src" / "a.py").write_text("changed-a")
        baseline = {"src/audit.md": hashlib.sha256(b"base\n").hexdigest(),
                    "src/a.py": _digest(ws / "src" / "a.py")}
        report = sync_back(exp, ws, ["src"], baseline=baseline,
                           merge_on_conflict=["src/audit.md"])
        assert report.conflicts == ()
        assert report.merged_paths == ("src/audit.md",)
        assert "src/a.py" in report.synced_paths
        assert (ws / "src" / "a.py").read_text() == "changed-a"
        # merge paths are merged by the engine, not by the planner
        assert (ws / "src" / "audit.md").read_text() == "base\nhost-line\n"
        assert (exp / "src" / "audit.md").read_text() == "base\nexport-line\n"

    def test_non_merge_conflict_still_loud_and_atomic(self, tmp_path):
        ws = _workspace(tmp_path)
        exp = _export(tmp_path, ws)
        (exp / "src" / "a.py").write_text("agent-change")
        (ws / "src" / "a.py").write_text("external-change")
        baseline = {"src/a.py": hashlib.sha256(b"orig-a").hexdigest()}
        report = sync_back(exp, ws, ["src"], baseline=baseline,
                           merge_on_conflict=["other.md"])
        assert report.conflicts == ("src/a.py",)
        assert report.merged_paths == ()
        assert report.synced_paths == ()

    def test_mixed_merge_and_hard_keeps_non_merge_atomic(self, tmp_path):
        """A hard conflict leaves the non-merge copy/delete atomic, but the
        merge path is still reported so the caller can union-merge it first."""
        ws = _workspace(tmp_path)
        exp = _export(tmp_path, ws)
        (ws / "src" / "audit.md").write_text("base\nhost-line\n")
        (exp / "src" / "audit.md").write_text("base\nexport-line\n")
        (exp / "src" / "b.py").write_text("agent-b")
        (ws / "src" / "b.py").write_text("external-b")
        baseline = {"src/audit.md": hashlib.sha256(b"base\n").hexdigest(),
                    "src/b.py": hashlib.sha256(b"orig-b").hexdigest()}
        report = sync_back(exp, ws, ["src"], baseline=baseline,
                           merge_on_conflict=["src/audit.md"])
        assert report.conflicts == ("src/b.py",)
        assert report.merged_paths == ("src/audit.md",)
        assert report.synced_paths == ()

    def test_wildcard_merge_match(self, tmp_path):
        ws = _workspace(tmp_path)
        exp = _export(tmp_path, ws)
        (ws / "src" / "build.log").write_text("base\nhost\n")
        (exp / "src" / "build.log").write_text("base\nexport\n")
        baseline = {"src/build.log": hashlib.sha256(b"base\n").hexdigest()}
        report = sync_back(exp, ws, ["src"], baseline=baseline,
                           merge_on_conflict=["*.log"])
        assert report.conflicts == ()
        assert report.merged_paths == ("src/build.log",)

    def test_export_deleted_merge_path_is_hard_conflict(self, tmp_path):
        ws = _workspace(tmp_path)
        exp = _export(tmp_path, ws)
        (ws / "src" / "audit.md").write_text("host\n")
        baseline = {"src/audit.md": hashlib.sha256(b"base\n").hexdigest()}
        report = sync_back(exp, ws, ["src"], baseline=baseline,
                           merge_on_conflict=["src/audit.md"])
        assert report.conflicts == ("src/audit.md",)
        assert report.merged_paths == ()
        assert report.synced_paths == ()
        assert (ws / "src" / "audit.md").read_text() == "host\n"

    def test_host_deleted_merge_path_is_hard_conflict(self, tmp_path):
        ws = _workspace(tmp_path)
        exp = _export(tmp_path, ws)
        (exp / "src" / "audit.md").write_text("export\n")
        baseline = {"src/audit.md": hashlib.sha256(b"base\n").hexdigest()}
        report = sync_back(exp, ws, ["src"], baseline=baseline,
                           merge_on_conflict=["src/audit.md"])
        assert report.conflicts == ("src/audit.md",)
        assert report.merged_paths == ()
        assert report.synced_paths == ()
        assert not (ws / "src" / "audit.md").exists()

    def test_export_only_change_on_merge_path_still_copies(self, tmp_path):
        ws = _workspace(tmp_path)
        exp = _export(tmp_path, ws)
        (ws / "src" / "audit.md").write_text("base\n")
        (exp / "src" / "audit.md").write_text("base\nexport\n")
        # Baseline comes from the real workspace bytes, so a fabricated
        # sha256(b"base\n") would never match and would misclassify the
        # copy as a merge conflict.
        baseline = {"src/audit.md": _digest(ws / "src" / "audit.md")}
        report = sync_back(exp, ws, ["src"], baseline=baseline,
                           merge_on_conflict=["src/audit.md"])
        assert report.conflicts == ()
        assert report.merged_paths == ()
        assert "src/audit.md" in report.synced_paths
        assert (ws / "src" / "audit.md").read_text() == "base\nexport\n"


class TestDeletedBranchBaselineGuard:
    """037: the deleted branch consults the baseline before unlinking. A real
    file absent from the baseline and the export is a host-side addition, never
    an agent deletion; a baseline-present file deleted in the export still
    mirrors (unlinks) exactly as before."""

    def test_baseline_present_export_deleted_still_unlinks(self, tmp_path):
        ws = _workspace(tmp_path)
        exp = _export(tmp_path, ws)
        (exp / "src" / "b.py").unlink()
        baseline = {"src/a.py": _digest(ws / "src" / "a.py"),
                    "src/b.py": _digest(ws / "src" / "b.py")}
        report = sync_back(exp, ws, ["src"], baseline=baseline)
        assert report.conflicts == ()
        assert report.host_added_paths == ()
        assert "src/b.py" in report.synced_paths
        assert not (ws / "src" / "b.py").exists()

    def test_baseline_absent_real_only_file_is_host_added_and_untouched(
            self, tmp_path):
        ws = _workspace(tmp_path)
        exp = _export(tmp_path, ws)
        (ws / "src" / "host-added.py").write_text("host-side addition")
        baseline = {"src/a.py": _digest(ws / "src" / "a.py"),
                    "src/b.py": _digest(ws / "src" / "b.py")}
        report = sync_back(exp, ws, ["src"], baseline=baseline)
        assert report.conflicts == ()
        assert report.host_added_paths == ("src/host-added.py",)
        assert report.synced_paths == ()
        assert (ws / "src" / "host-added.py").read_text() == "host-side addition"

    def test_modified_branch_hard_conflict_unchanged(self, tmp_path):
        """Regression: BR-4.4 still treats a baseline-present path changed on
        both sides as a loud conflict, never an overwrite."""
        ws = _workspace(tmp_path)
        exp = _export(tmp_path, ws)
        (exp / "src" / "a.py").write_text("agent-change")
        (ws / "src" / "a.py").write_text("external-change")
        baseline = {"src/a.py": hashlib.sha256(b"orig-a").hexdigest()}
        report = sync_back(exp, ws, ["src"], baseline=baseline)
        assert report.conflicts == ("src/a.py",)
        assert report.host_added_paths == ()
        assert report.synced_paths == ()
        assert (ws / "src" / "a.py").read_text() == "external-change"
