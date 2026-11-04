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


def run_publish(repo: Path, *args: str, yes: bool = True,
                stdin=None, input=None) -> subprocess.CompletedProcess:
    """Drive the CLI. Real cuts pass --yes by default so they skip the
    release-note confirmation prompt; pass yes=False with stdin=DEVNULL
    (closed) or input='y\\n' (typed answer) to test the prompt."""
    cmd = [sys.executable, str(PUBLISH), "--cwd", str(repo)]
    if yes:
        cmd.append("--yes")
    cmd.extend(args)
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        stdin=stdin,
        input=input,
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

    def test_dirty_tree_reported_but_not_in_changelog(self, tmp_path):
        repo, _ = make_repo(tmp_path)
        (repo / "extra.txt").write_text("uncommitted")
        proc = run_publish(repo, "--patch")
        assert proc.returncode == 0, proc.stderr
        text = read_changelog(repo)
        assert "extra.txt" not in text[text.index("## [0.2.1]"):]
        assert "extra.txt" in proc.stdout  # reported in the cut report only
        assert _git(repo, "status", "--porcelain").stdout == ""
        assert "extra.txt" in _git(repo, "show", "--stat", "--oneline",
                                   "HEAD").stdout

    def test_missing_unreleased_heading_fails(self, tmp_path):
        repo, _ = make_repo(tmp_path)
        (repo / "CHANGELOG.md").write_text("# Changelog\n\n## [0.2.0] - 2026-10-19\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-m", "drop unreleased")
        proc = run_publish(repo, "--patch")
        assert proc.returncode != 0
        assert "Unreleased" in proc.stderr
        assert read_version(repo) == "0.2.0"

    def test_dry_run_shows_rendered_section(self, tmp_path):
        """The dry run is the notes preview: it prints the rendered release
        section so the /publish flow can ask for approval before the cut."""
        repo, _ = make_repo(tmp_path, commits=(("a.txt", "a\n", "feat: alpha"),))
        proc = run_publish(repo, "--patch", "--dry-run", yes=False)
        assert proc.returncode == 0, proc.stderr
        assert "Release notes to be written:" in proc.stdout
        assert "## [0.2.1]" in proc.stdout
        assert "feat: alpha" in proc.stdout


class TestReleaseConfirmation:
    def test_closed_stdin_without_yes_fails(self, tmp_path):
        repo, _ = make_repo(tmp_path)
        proc = run_publish(repo, "--patch", yes=False, stdin=subprocess.DEVNULL)
        assert proc.returncode != 0
        assert "--yes" in proc.stderr
        assert read_version(repo) == "0.2.0"
        assert tags(repo) == []
        assert last_subject(repo) == "chore: initial"

    def test_declined_confirmation_aborts(self, tmp_path):
        repo, _ = make_repo(tmp_path)
        proc = run_publish(repo, "--patch", yes=False, input="n\n")
        assert proc.returncode != 0
        assert "aborted" in proc.stderr.lower()
        assert read_version(repo) == "0.2.0"
        assert tags(repo) == []
        assert last_subject(repo) == "chore: initial"

    def test_confirmed_cut_proceeds(self, tmp_path):
        repo, _ = make_repo(tmp_path)
        proc = run_publish(repo, "--patch", yes=False, input="y\n")
        assert proc.returncode == 0, proc.stderr
        assert read_version(repo) == "0.2.1"
        assert tags(repo) == ["v0.2.1"]

    def test_prompt_shows_rendered_entry(self, tmp_path):
        repo, _ = make_repo(tmp_path, commits=(("a.txt", "a", "feat: alpha"),))
        proc = run_publish(repo, "--patch", yes=False, input="y\n")
        assert proc.returncode == 0, proc.stderr
        assert "## [0.2.1]" in proc.stdout
        assert "feat: alpha" in proc.stdout

    def test_yes_flag_skips_prompt(self, tmp_path):
        repo, _ = make_repo(tmp_path)
        proc = run_publish(repo, "--patch", yes=True, stdin=subprocess.DEVNULL)
        assert proc.returncode == 0, proc.stderr
        assert read_version(repo) == "0.2.1"

    def test_dry_run_needs_no_confirmation(self, tmp_path):
        repo, _ = make_repo(tmp_path)
        proc = run_publish(repo, "--patch", "--dry-run", yes=False,
                           stdin=subprocess.DEVNULL)
        assert proc.returncode == 0, proc.stderr
        assert read_version(repo) == "0.2.0"
        assert tags(repo) == []


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

    def test_plan_number_reference_without_marker_passes(self, tmp_path):
        """Number-only plan mentions do not trigger the gate: consumers need
        the concrete list of changes in the subsection, not plan references."""
        repo, _ = make_repo(
            tmp_path,
            unreleased="### Changed\n\n- fix: 056 slug matching in the loader\n",
        )
        proc = run_publish(repo, "--patch", "--dry-run")
        assert proc.returncode == 0, proc.stderr

    def test_no_marker_passes_without_section(self, tmp_path):
        repo, _ = make_repo(
            tmp_path,
            unreleased="### Changed\n\n- feat: add kernel sandbox\n",
        )
        proc = run_publish(repo, "--patch")
        assert proc.returncode == 0, proc.stderr
        assert read_version(repo) == "0.2.1"

    def test_dry_run_refuses_violating_entry(self, tmp_path):
        """The dry run is the pre-cut gate: it runs the same refusal checks
        as the real cut, so a violating entry is caught before anything is
        written, committed, or tagged."""
        repo, _ = make_repo(
            tmp_path,
            unreleased=(
                "### Changed\n\n"
                "- feat: construct plan 031-qa-environmental-verdict.md\n"
            ),
        )
        proc = run_publish(repo, "--patch", "--dry-run")
        assert proc.returncode != 0
        assert "Consumer adoption" in proc.stderr
        assert read_version(repo) == "0.2.0"
        assert tags(repo) == []
        assert last_subject(repo) == "chore: initial"

    def test_dry_run_passes_violation_free_entry(self, tmp_path):
        repo, _ = make_repo(
            tmp_path,
            unreleased="### Changed\n\n- feat: add kernel sandbox\n",
        )
        proc = run_publish(repo, "--patch", "--dry-run")
        assert proc.returncode == 0, proc.stderr
        assert read_version(repo) == "0.2.0"

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


class TestNoAdd:
    def test_no_add_excludes_unstaged_files(self, tmp_path):
        """--no-add commits only the files publish.py writes; the WIP stays
        uncommitted in the tree instead of being swept into the release."""
        repo, _ = make_repo(tmp_path, commits=(("extra.txt", "old\n",
                                                "feat: alpha"),))
        (repo / "extra.txt").write_text("dirty\n")
        proc = run_publish(repo, "--patch", "--no-add")
        assert proc.returncode == 0, proc.stderr
        assert read_version(repo) == "0.2.1"
        assert "v0.2.1" in tags(repo)
        assert last_subject(repo) == "release: v0.2.1"
        committed = _git(repo, "show", "--pretty=format:", "--name-only",
                         "HEAD").stdout.split()
        assert "pyproject.toml" in committed
        assert "CHANGELOG.md" in committed
        assert "extra.txt" not in committed
        assert (repo / "extra.txt").read_text() == "dirty\n"
        assert "extra.txt" in _git(repo, "status", "--porcelain").stdout

    def test_no_add_dry_run_reports_exclusion(self, tmp_path):
        repo, _ = make_repo(tmp_path)
        (repo / "extra.txt").write_text("dirty\n")
        proc = run_publish(repo, "--patch", "--dry-run", "--no-add",
                           yes=False)
        assert proc.returncode == 0, proc.stderr
        assert "excluded from the release commit" in proc.stdout


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


class TestPublishCommandFile:
    """The /publish command file is the source of truth for the release flow.

    Shell blocks in a command file run when OpenCode evaluates the command,
    before the agent sees the prompt, so a real (non-dry-run) publish must
    never live in a shell block. Confirmation must route through the
    `question` tool instead of publish.py's stdin prompt, which has no
    stdin to read when the command runs in a session.
    """

    COMMAND_FILE = REPO_ROOT / ".opencode" / "commands" / "publish.md"

    def test_real_publish_never_runs_in_shell_block(self):
        text = self.COMMAND_FILE.read_text()
        for line in text.splitlines():
            if line.strip().startswith("!") and "publish.py" in line:
                assert "--dry-run" in line, (
                    "a publish.py shell block without --dry-run would cut "
                    "a release before the user is asked: %r" % line
                )

    def test_confirmation_routes_through_question_tool(self):
        text = self.COMMAND_FILE.read_text()
        assert "`question` tool" in text

    def test_mandate_present(self):
        text = self.COMMAND_FILE.read_text()
        assert "### Consumer adoption" in text
        assert "CONSUMER_ADOPTION_PLANS" in text

    def test_unstaged_files_confirmation_present(self):
        """The flow must ask whether the reported uncommitted files are
        added to the release commit before the cut."""
        text = self.COMMAND_FILE.read_text()
        assert "uncommitted files" in text
        assert "--no-add" in text

    def test_notes_happiness_confirmation_present(self):
        """The flow must ask whether the user is happy with the rendered
        release notes, and offer a changes loop."""
        text = self.COMMAND_FILE.read_text()
        assert "happy" in text
        assert "--description" in text
