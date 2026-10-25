"""Unit-5 export runtime (code-gen step 5): copy-based materialisation into
``state_dir/exports/<run-id>/``, excluding .git and host-only dirs."""

import os
import subprocess

from agentic_dev_loop.engine.step_workspace import StepWorkspace


def _repo(tmp_path):
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "a.py").write_text("print('a')")
    (repo / "docs").mkdir()
    (repo / "docs" / "d.md").write_text("# doc")
    (repo / ".git").mkdir()
    (repo / ".git" / "HEAD").write_text("ref")
    (repo / ".venv").mkdir()
    (repo / ".venv" / "x").write_text("venv")
    (repo / "dirty.txt").write_text("dirty working tree")
    return repo


def test_materialise_copies_except_git_and_host_only(tmp_path):
    repo = _repo(tmp_path)
    export = tmp_path / "state" / "exports" / "run1"
    ws = StepWorkspace.materialise(repo, [], "linux", export)
    assert (export / "src" / "a.py").read_text() == "print('a')"
    assert (export / "docs" / "d.md").exists()
    assert (export / "dirty.txt").read_text() == "dirty working tree"
    assert not (export / ".git").exists()
    assert not (export / ".venv").exists()


def test_materialise_preserves_symlinks_without_resolving(tmp_path):
    repo = _repo(tmp_path)
    (repo / "links").mkdir()
    (repo / "links" / "dangling").symlink_to("nowhere")
    (repo / "links" / "valid").symlink_to("../src/a.py")
    export = tmp_path / "state" / "exports" / "run1"
    # Completing materialise also proves the baseline snapshot survives the
    # dangling link (it runs _snapshot on the export after the copy).
    StepWorkspace.materialise(repo, [], "linux", export)
    dangling = export / "links" / "dangling"
    valid = export / "links" / "valid"
    # Symlinks copy as symlinks, never resolved against the host: the
    # dangling one stays dangling, the valid one keeps its relative target.
    assert dangling.is_symlink()
    assert os.readlink(dangling) == "nowhere"
    assert not dangling.exists()
    assert valid.is_symlink()
    assert os.readlink(valid) == "../src/a.py"


def test_missing_cache_is_loud(tmp_path):
    repo = _repo(tmp_path)
    export = tmp_path / "state" / "exports" / "run1"
    import pytest
    with pytest.raises(ValueError, match="does not exist"):
        StepWorkspace.materialise(repo, ["missing-cache"], "linux", export)


def test_cache_mount_targets_pre_created(tmp_path):
    repo = _repo(tmp_path)
    (repo / "node_modules").mkdir()
    (repo / "node_modules" / "x").write_text("dep")
    export = tmp_path / "state" / "exports" / "run1"
    ws = StepWorkspace.materialise(repo, ["node_modules"], "linux", export)
    assert (export / "node_modules").is_dir()
    # The cache is bind-mounted, not copied: its contents are absent from the export.
    assert not (export / "node_modules" / "x").exists()
    points = ws.mount_points()
    assert points[0] == (str(export), str(export), "rw")
    assert points[1] == (str(repo / "node_modules"),
                         f"{export}/node_modules", "rw")


def test_sync_back_round_trip(tmp_path):
    repo = _repo(tmp_path)
    export = tmp_path / "state" / "exports" / "run1"
    ws = StepWorkspace.materialise(repo, [], "linux", export)
    (export / "src" / "a.py").write_text("print('changed')")
    report = ws.sync_back(["src"])
    assert report.conflicts == ()
    assert "src/a.py" in report.synced_paths
    assert (repo / "src" / "a.py").read_text() == "print('changed')"


def test_non_declared_never_syncs(tmp_path):
    repo = _repo(tmp_path)
    export = tmp_path / "state" / "exports" / "run1"
    ws = StepWorkspace.materialise(repo, [], "linux", export)
    (export / "dirty.txt").write_text("changed but not declared")
    report = ws.sync_back(["src"])
    assert report.synced_paths == ()
    assert (repo / "dirty.txt").read_text() == "dirty working tree"


def test_whole_tree_dot_sync_back_and_cache_untouched(tmp_path):
    """BR-4 + BR-3/F2: a ``.``-rw sync-back through StepWorkspace copies edits
    back while a bind-mounted cache is never classified as a deletion."""
    repo = _repo(tmp_path)
    (repo / "node_modules").mkdir()
    (repo / "node_modules" / "dep.js").write_text("cache")
    export = tmp_path / "state" / "exports" / "run1"
    ws = StepWorkspace.materialise(repo, ["node_modules"], "linux", export)
    (export / "src" / "a.py").write_text("print('changed')")
    report = ws.sync_back(["."])
    assert report.conflicts == ()
    assert "src/a.py" in report.synced_paths
    assert (repo / "src" / "a.py").read_text() == "print('changed')"
    assert (repo / "node_modules" / "dep.js").read_text() == "cache"
    assert "node_modules/dep.js" not in report.synced_paths
