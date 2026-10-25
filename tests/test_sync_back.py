"""Unit-5 sync-back runtime (code-gen step 6): shutil diff-copy, declared-rw
only, .git never touched, conflicts loud."""

import hashlib
import os

import pytest

from agentic_dev_loop.engine.sync_back import sync_back


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
