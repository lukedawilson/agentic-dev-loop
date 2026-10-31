"""Tests for tools/publish.py - the release-cut script behind /publish.

All tests drive the real CLI via subprocess against temporary git repositories
with a local bare remote, so commit/tag/push run for real without network or
mocks. File edits are verified by reading the files back (tomllib for
pyproject), never by trusting stdout.
"""

import os
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
PUBLISH = REPO_ROOT / "tools" / "publish.py"

PYPROJECT_TMPL = """[build-system]
requires = ["setuptools>=77"]
build-backend = "setuptools.build_meta"

[project]
name = "agentic-dev-loop"
version = "{version}"
description = "test"
requires-python = ">=3.12"
"""

CHANGELOG_TMPL = """# Changelog

## [Unreleased]
{unreleased}## [0.2.0] - 2026-10-19

### Added

- test entry
"""


def _git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=check,
        env={**os.environ,
             "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
             "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"},
    )


def make_repo(tmp_path: Path, version: str = "0.2.0", unreleased: str = "",
              commits: tuple = ()) -> tuple[Path, Path]:
    """A git repo with pyproject+CHANGELOG committed, a local bare remote,
    and optional extra commits as (filename, content, message) tuples."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "pyproject.toml").write_text(PYPROJECT_TMPL.format(version=version))
    (repo / "CHANGELOG.md").write_text(CHANGELOG_TMPL.format(unreleased=unreleased))
    _git(repo, "init", "-b", "main")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "chore: initial")
    for filename, content, msg in commits:
        (repo / filename).write_text(content)
        _git(repo, "add", "-A")
        _git(repo, "commit", "-m", msg)
    bare = tmp_path / "bare.git"
    _git(tmp_path, "init", "--bare", str(bare))
    _git(repo, "remote", "add", "origin", str(bare))
    return repo, bare


def run_publish(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(PUBLISH), "--cwd", str(repo), *args],
        capture_output=True,
        text=True,
        env={**os.environ,
             "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
             "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"},
    )


def read_version(repo: Path) -> str:
    return tomllib.loads((repo / "pyproject.toml").read_text())["project"]["version"]


def read_changelog(repo: Path) -> str:
    return (repo / "CHANGELOG.md").read_text()


def tags(repo: Path) -> list[str]:
    return _git(repo, "tag", "--list").stdout.split()


def last_subject(repo: Path) -> str:
    return _git(repo, "log", "-1", "--pretty=format:%s").stdout


class TestVersionResolution:
    def test_major_bump(self, tmp_path):
        repo, _ = make_repo(tmp_path)
        proc = run_publish(repo, "--major")
        assert proc.returncode == 0, proc.stderr
        assert read_version(repo) == "1.0.0"
        assert tags(repo) == ["v1.0.0"]

    def test_minor_bump(self, tmp_path):
        repo, _ = make_repo(tmp_path)
        proc = run_publish(repo, "--minor")
        assert proc.returncode == 0, proc.stderr
        assert read_version(repo) == "0.3.0"
        assert tags(repo) == ["v0.3.0"]

    def test_patch_bump(self, tmp_path):
        repo, _ = make_repo(tmp_path)
        proc = run_publish(repo, "--patch")
        assert proc.returncode == 0, proc.stderr
        assert read_version(repo) == "0.2.1"
        assert tags(repo) == ["v0.2.1"]

    def test_major_finalises_rc_of_x_0_0(self, tmp_path):
        repo, _ = make_repo(tmp_path, version="1.0.0rc1")
        proc = run_publish(repo, "--major")
        assert proc.returncode == 0, proc.stderr
        assert read_version(repo) == "1.0.0"
        assert tags(repo) == ["v1.0.0"]

    def test_patch_on_rc_bumps_patch(self, tmp_path):
        repo, _ = make_repo(tmp_path, version="1.0.0rc1")
        proc = run_publish(repo, "--patch")
        assert proc.returncode == 0, proc.stderr
        assert read_version(repo) == "1.0.1"

    def test_explicit_rc_version(self, tmp_path):
        repo, _ = make_repo(tmp_path)
        proc = run_publish(repo, "1.0.0rc1")
        assert proc.returncode == 0, proc.stderr
        assert read_version(repo) == "1.0.0rc1"
        assert tags(repo) == ["v1.0.0rc1"]

    def test_non_canonical_version_rejected(self, tmp_path):
        repo, _ = make_repo(tmp_path)
        proc = run_publish(repo, "1.0.0-rc.1")
        assert proc.returncode != 0
        assert "1.0.0rc1" in proc.stderr

    def test_lower_version_rejected(self, tmp_path):
        repo, _ = make_repo(tmp_path)
        proc = run_publish(repo, "0.1.0")
        assert proc.returncode != 0
        assert read_version(repo) == "0.2.0"
        assert tags(repo) == []

    def test_same_version_allowed_only_while_untagged(self, tmp_path):
        repo, _ = make_repo(tmp_path)
        proc = run_publish(repo, "0.2.0")
        assert proc.returncode == 0, proc.stderr
        assert tags(repo) == ["v0.2.0"]
        proc = run_publish(repo, "0.2.0")
        assert proc.returncode != 0
        assert "already tagged" in proc.stderr

    def test_v_prefix_accepted(self, tmp_path):
        repo, _ = make_repo(tmp_path)
        proc = run_publish(repo, "v0.3.0")
        assert proc.returncode == 0, proc.stderr
        assert tags(repo) == ["v0.3.0"]


class TestChangelog:
    def test_description_wins_over_unreleased(self, tmp_path):
        repo, _ = make_repo(tmp_path, unreleased="- Kernel sandbox\n")
        proc = run_publish(repo, "--minor", "-m", "Ship it")
        assert proc.returncode == 0, proc.stderr
        text = read_changelog(repo)
        section = text[text.index("## [0.3.0]"):text.index("## [0.2.0]")]
        assert "- Ship it" in section
        assert "Kernel sandbox" not in section  # the description replaces it
        # …but the curated notes survive under Unreleased for the next release
        unreleased = text[text.index("## [Unreleased]"):text.index("## [0.3.0]")]
        assert "Kernel sandbox" in unreleased
        assert text.count("Kernel sandbox") == 1

    def test_unreleased_content_moves_to_new_section(self, tmp_path):
        repo, _ = make_repo(tmp_path, unreleased="- Kernel sandbox\n")
        proc = run_publish(repo, "--patch")
        assert proc.returncode == 0, proc.stderr
        text = read_changelog(repo)
        section = text[text.index("## [0.2.1]"):text.index("## [0.2.0]")]
        assert "- Kernel sandbox" in section
        assert text.count("- Kernel sandbox") == 1  # moved, not duplicated
        assert text.index("## [Unreleased]") < text.index("## [0.2.1]")

    def test_inferred_from_git_history(self, tmp_path):
        repo, _ = make_repo(
            tmp_path,
            commits=(("a.txt", "a", "feat: alpha"), ("b.txt", "b", "fix: beta")),
        )
        proc = run_publish(repo, "--minor")
        assert proc.returncode == 0, proc.stderr
        text = read_changelog(repo)
        section = text[text.index("## [0.3.0]"):text.index("## [0.2.0]")]
        assert "feat: alpha" in section
        assert "fix: beta" in section

    def test_inferred_includes_uncommitted_work(self, tmp_path):
        repo, _ = make_repo(tmp_path)
        (repo / "extra.txt").write_text("uncommitted")
        proc = run_publish(repo, "--patch")
        assert proc.returncode == 0, proc.stderr
        text = read_changelog(repo)
        assert "extra.txt" in text[text.index("## [0.2.1]"):]

    def test_missing_unreleased_heading_fails(self, tmp_path):
        repo, _ = make_repo(tmp_path)
        (repo / "CHANGELOG.md").write_text("# Changelog\n\n## [0.2.0] - 2026-10-19\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-m", "drop unreleased")
        proc = run_publish(repo, "--patch")
        assert proc.returncode != 0
        assert "Unreleased" in proc.stderr
        assert read_version(repo) == "0.2.0"


class TestConsumerAdoption:
    def test_marker_without_adoption_section_fails(self, tmp_path):
        repo, _ = make_repo(
            tmp_path,
            unreleased=(
                "### Changed\n\n"
                "- feat: construct plan 031-qa-environmental-verdict.md\n"
            ),
        )
        proc = run_publish(repo, "--patch")
        assert proc.returncode != 0
        assert "Consumer adoption" in proc.stderr
        assert read_version(repo) == "0.2.0"
        assert tags(repo) == []

    def test_marker_with_adoption_section_passes(self, tmp_path):
        repo, _ = make_repo(
            tmp_path,
            unreleased=(
                "### Changed\n\n"
                "- feat: construct plan 031-qa-environmental-verdict.md\n\n"
                "### Consumer adoption\n\n"
                "- qa verdict steps must add `env: ENV` to their verdicts map\n"
            ),
        )
        proc = run_publish(repo, "--patch")
        assert proc.returncode == 0, proc.stderr
        assert read_version(repo) == "0.2.1"

    def test_no_marker_passes_without_section(self, tmp_path):
        repo, _ = make_repo(
            tmp_path,
            unreleased="### Changed\n\n- feat: add kernel sandbox\n",
        )
        proc = run_publish(repo, "--patch")
        assert proc.returncode == 0, proc.stderr
        assert read_version(repo) == "0.2.1"


class TestGitOps:
    def test_commit_tag_and_push(self, tmp_path):
        repo, bare = make_repo(tmp_path)
        proc = run_publish(repo, "--minor")
        assert proc.returncode == 0, proc.stderr
        assert last_subject(repo) == "release: v0.3.0"
        assert tags(repo) == ["v0.3.0"]
        refs = _git(repo, "ls-remote", str(bare)).stdout
        assert "refs/tags/v0.3.0" in refs
        assert "refs/heads/main" in refs

    def test_commit_includes_all_pending_changes(self, tmp_path):
        repo, _ = make_repo(tmp_path)
        (repo / "pending.txt").write_text("work in flight")
        proc = run_publish(repo, "--patch")
        assert proc.returncode == 0, proc.stderr
        assert _git(repo, "status", "--porcelain").stdout == ""
        assert "pending.txt" in _git(repo, "show", "--stat", "--oneline",
                                     "HEAD").stdout

    def test_detached_head_fails(self, tmp_path):
        repo, _ = make_repo(tmp_path)
        _git(repo, "checkout", "--detach")
        proc = run_publish(repo, "--patch")
        assert proc.returncode != 0
        assert read_version(repo) == "0.2.0"
        assert tags(repo) == []

    def test_not_a_git_repo_fails(self, tmp_path):
        repo = tmp_path / "plain"
        repo.mkdir()
        (repo / "pyproject.toml").write_text(PYPROJECT_TMPL.format(version="0.2.0"))
        (repo / "CHANGELOG.md").write_text(CHANGELOG_TMPL.format(unreleased=""))
        proc = run_publish(repo, "--patch")
        assert proc.returncode != 0
        assert "git" in proc.stderr.lower()

    def test_dry_run_changes_nothing(self, tmp_path):
        repo, _ = make_repo(tmp_path)
        before_pyproject = (repo / "pyproject.toml").read_text()
        before_changelog = (repo / "CHANGELOG.md").read_text()
        proc = run_publish(repo, "--minor", "--dry-run")
        assert proc.returncode == 0, proc.stderr
        assert "v0.3.0" in proc.stdout
        assert (repo / "pyproject.toml").read_text() == before_pyproject
        assert (repo / "CHANGELOG.md").read_text() == before_changelog
        assert tags(repo) == []
        assert last_subject(repo) == "chore: initial"


class TestArgs:
    def test_no_selector_fails(self, tmp_path):
        repo, _ = make_repo(tmp_path)
        proc = run_publish(repo)
        assert proc.returncode != 0
        assert read_version(repo) == "0.2.0"

    def test_two_selectors_fail(self, tmp_path):
        repo, _ = make_repo(tmp_path)
        proc = run_publish(repo, "--major", "--minor")
        assert proc.returncode != 0
        assert read_version(repo) == "0.2.0"
