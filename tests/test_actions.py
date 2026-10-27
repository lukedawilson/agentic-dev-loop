"""Action tests - ported from the consumer's TestKillListenersOnPort /
TestGitWorktreeClean / TestCommitNode, retargeted at the node-shaped
`(state, params) -> dict` API. Real processes and real git repos throughout:
git and sockets are local utilities, not network boundaries."""

import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from agentic_dev_loop.engine.actions import (_listening_pids, _parse_cwd_output, _pid_cwd,
                                  _trusted_hooks_dir, commit, git_worktree_clean,
                                  kill_listeners_on_port, tooling_exclude_pathspec)

lsof_required = pytest.mark.skipif(
    shutil.which("lsof") is None, reason="lsof required")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def spawn_listener(port: int, cwd: Path | None = None) -> subprocess.Popen:
    """Spawn a real process listening on 127.0.0.1:port - stands in for an
    orphaned app server. Its command line contains 'socket' (from the -c code)."""
    code = (
        "import socket,time;"
        "s=socket.socket();"
        "s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);"
        f"s.bind(('127.0.0.1',{port}));"
        "s.listen();"
        "time.sleep(120)"
    )
    return subprocess.Popen([sys.executable, "-c", code], cwd=cwd)


def wait_listening(port: int, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.05)
    raise RuntimeError(f"listener on port {port} never came up")


def init_git_repo(path: Path) -> None:
    """A real git repo with an initial commit, ready for commit-node tests."""
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"],
                   cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"],
                   cwd=path, check=True)
    (path / "seed.txt").write_text("seed")
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", "seed"], cwd=path, check=True,
                   capture_output=True)


def git_log(path: Path) -> str:
    return subprocess.run(["git", "log", "--oneline"], cwd=path, check=True,
                          capture_output=True, text=True).stdout


COMMIT_PARAMS = {"message": "feat: construct {subject} (agent dev loop)"}


class TestKillListenersOnPort:
    """The orphan sweep exists to kill real orphaned servers, so it is tested
    with real processes - mocking it would test nothing."""

    @lsof_required
    def test_kills_listener_on_port(self):
        port = free_port()
        proc = spawn_listener(port)
        try:
            wait_listening(port)
            kill_listeners_on_port(port)
            proc.wait(timeout=5)
            assert proc.poll() is not None
        finally:
            if proc.poll() is None:
                proc.kill()

    @lsof_required
    def test_no_listener_is_a_noop(self):
        kill_listeners_on_port(free_port())  # must not raise

    @lsof_required
    def test_match_filter_spares_non_matching_processes(self):
        port = free_port()
        proc = spawn_listener(port)
        try:
            wait_listening(port)
            kill_listeners_on_port(port, match="no-such-process-name")
            assert proc.poll() is None  # still alive - filter did not match
        finally:
            proc.kill()

    @lsof_required
    def test_match_filter_kills_matching_processes(self):
        port = free_port()
        proc = spawn_listener(port)
        try:
            wait_listening(port)
            kill_listeners_on_port(port, match="socket")  # in the -c code
            proc.wait(timeout=5)
            assert proc.poll() is not None
        finally:
            if proc.poll() is None:
                proc.kill()

    @lsof_required
    def test_escalates_to_sigkill_when_term_ignored(self):
        """A server that ignores SIGTERM must still be swept - otherwise the
        orphan holds the port indefinitely."""
        port = free_port()
        code = (
            "import signal,socket,time;"
            "signal.signal(signal.SIGTERM, signal.SIG_IGN);"
            "s=socket.socket();"
            "s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);"
            f"s.bind(('127.0.0.1',{port}));"
            "s.listen();"
            "time.sleep(120)"
        )
        proc = subprocess.Popen([sys.executable, "-c", code])
        try:
            wait_listening(port)
            kill_listeners_on_port(port)
            proc.wait(timeout=10)
            assert proc.poll() is not None
        finally:
            if proc.poll() is None:
                proc.kill()

    def test_kill_listeners_wrapper_removed(self):
        """The port_sweep pipeline step that used the node-shaped wrapper is
        gone; the startup sweep calls kill_listeners_on_port directly."""
        import agentic_dev_loop.engine.actions as actions_module
        assert not hasattr(actions_module, "kill_listeners")

    @lsof_required
    def test_never_sweeps_own_listener(self):
        """A pipeline's own process may be listening on the swept port (the
        live viz server). It must never be swept - only orphaned listeners
        from other processes (previous runs / agent subprocesses)."""
        port = free_port()
        s = socket.socket()
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", port))
        s.listen()
        try:
            assert str(os.getpid()) not in _listening_pids(port, None)
        finally:
            s.close()

    @lsof_required
    def test_cwd_filter_kills_matching_cwd(self, tmp_path):
        port = free_port()
        proc = spawn_listener(port, cwd=tmp_path)
        try:
            wait_listening(port)
            kill_listeners_on_port(port, cwd=tmp_path)
            proc.wait(timeout=5)
            assert proc.poll() is not None
        finally:
            if proc.poll() is None:
                proc.kill()

    @lsof_required
    def test_cwd_filter_spares_other_directory(self, tmp_path):
        other = tmp_path / "elsewhere"
        other.mkdir()
        port = free_port()
        proc = spawn_listener(port, cwd=tmp_path)
        try:
            wait_listening(port)
            kill_listeners_on_port(port, cwd=other)
            assert proc.poll() is None  # still alive - cwd differs
        finally:
            proc.kill()

    @lsof_required
    def test_cwd_with_nonmatching_match_spares(self, tmp_path):
        port = free_port()
        proc = spawn_listener(port, cwd=tmp_path)
        try:
            wait_listening(port)
            kill_listeners_on_port(port, match="no-such-process-name",
                                   cwd=tmp_path)
            assert proc.poll() is None  # cwd matched but command line didn't
        finally:
            proc.kill()


class TestPidCwd:
    def test_parses_path_after_fcwd(self):
        assert _parse_cwd_output(
            "p42312\nfcwd\nn/Users/luke/dev/work/agent-engine\n") == \
            "/Users/luke/dev/work/agent-engine"

    def test_parses_path_without_fcwd_linux_format(self):
        """Linux lsof -Fn omits the fd line: `p<PID>` then `n<path>`."""
        assert _parse_cwd_output("p42312\nn/var/tmp/somewhere\n") == \
            "/var/tmp/somewhere"

    def test_none_when_no_path_line(self):
        assert _parse_cwd_output("p42312\nfcwd\n") is None
        assert _parse_cwd_output("p42312\n") is None

    def test_none_for_garbage(self):
        assert _parse_cwd_output("garbage\n") is None
        assert _parse_cwd_output("") is None

    def test_injection_shaped_pid_tokens_never_reach_lsof(self):
        """A non-numeric pid token is rejected before it can reach lsof
        (BR-5.2) - the subprocess is never invoked."""
        assert _pid_cwd("1;rm -rf") is None
        assert _pid_cwd("-9") is None
        assert _pid_cwd("") is None
        assert _pid_cwd(" 123") is None


class TestGitWorktreeClean:
    def test_clean_repo(self, tmp_path):
        repo = tmp_path / "repo"
        init_git_repo(repo)
        assert git_worktree_clean(repo) is True

    def test_dirty_repo(self, tmp_path):
        repo = tmp_path / "repo"
        init_git_repo(repo)
        (repo / "new.txt").write_text("uncommitted")
        assert git_worktree_clean(repo) is False

    def test_not_a_repo_is_not_clean(self, tmp_path):
        """Fail-safe: outside a git repo there is no clean baseline, so the
        commit node stays disabled."""
        assert git_worktree_clean(tmp_path) is False


class TestCommitNode:
    """Run 004 policy: agents never commit mid-run; the pipeline commits the
    constructed work exactly once, and only when the worktree was clean at
    run start (so the auto-commit can never sweep up pre-existing unrelated
    changes the way the dev agent's b4acb62a did)."""

    def test_commits_constructed_work(self, tmp_path, monkeypatch):
        repo = tmp_path / "repo"
        init_git_repo(repo)
        (repo / "feature.cs").write_text("// constructed by the pipeline")
        monkeypatch.chdir(repo)

        update = commit({"subject": "unit U2", "commit_allowed": True}, COMMIT_PARAMS)

        log = git_log(repo)
        assert "feat: construct unit U2 (agent dev loop)" in log
        assert "seed" in log  # exactly one new commit on top
        assert update == {}

    def test_message_template_renders_subject(self, tmp_path, monkeypatch):
        repo = tmp_path / "repo"
        init_git_repo(repo)
        (repo / "f.txt").write_text("x")
        monkeypatch.chdir(repo)

        commit({"subject": "pipeline widgets", "commit_allowed": True},
               {"message": "build {subject}"})

        assert "build pipeline widgets" in git_log(repo)

    def test_missing_message_param_fails_fast(self, tmp_path, monkeypatch):
        repo = tmp_path / "repo"
        init_git_repo(repo)
        (repo / "f.txt").write_text("x")
        monkeypatch.chdir(repo)

        with pytest.raises(ValueError, match="message"):
            commit({"subject": "s", "commit_allowed": True}, {})

    def test_skips_when_worktree_was_dirty_at_run_start(self, tmp_path,
                                                        monkeypatch, capsys):
        repo = tmp_path / "repo"
        init_git_repo(repo)
        (repo / "feature.cs").write_text("// constructed by the pipeline")
        monkeypatch.chdir(repo)

        commit({"subject": "unit U2", "commit_allowed": False}, COMMIT_PARAMS)

        assert "feature.cs" not in subprocess.run(
            ["git", "ls-files"], cwd=repo, capture_output=True, text=True).stdout
        assert "Skipping auto-commit" in capsys.readouterr().out

    def test_missing_state_key_defaults_to_skip(self, tmp_path, monkeypatch):
        """Checkpoints written before the policy existed have no
        commit_allowed key - resuming them must not suddenly commit."""
        repo = tmp_path / "repo"
        init_git_repo(repo)
        (repo / "feature.cs").write_text("// constructed by the pipeline")
        monkeypatch.chdir(repo)

        commit({"subject": "unit U2"}, COMMIT_PARAMS)

        assert "feature.cs" not in subprocess.run(
            ["git", "ls-files"], cwd=repo, capture_output=True, text=True).stdout

    def test_nothing_to_commit_is_a_noop(self, tmp_path, monkeypatch, capsys):
        repo = tmp_path / "repo"
        init_git_repo(repo)
        monkeypatch.chdir(repo)
        before = git_log(repo)

        commit({"subject": "unit U2", "commit_allowed": True}, COMMIT_PARAMS)

        assert git_log(repo) == before
        assert "Nothing to commit" in capsys.readouterr().out

    def test_repo_hooks_are_bypassed_by_trusted_hooks_path(self, tmp_path,
                                                            monkeypatch):
        """Q3: the commit action runs git with `-c core.hooksPath=<trusted>`,
        so a repo hook in `.git/hooks` never executes - a hostile/agent-written
        pre-commit hook cannot block or mutate the commit."""
        repo = tmp_path / "repo"
        init_git_repo(repo)
        (repo / "feature.cs").write_text("// constructed by the pipeline")
        marker = tmp_path / "hook-ran"
        hook = repo / ".git" / "hooks" / "pre-commit"
        hook.write_text(f"#!/bin/sh\ntouch {marker}\nexit 1\n")
        hook.chmod(0o755)
        # Point the trusted hooks dir at a tmp dir - never the real home.
        trusted = tmp_path / "trusted-hooks"
        monkeypatch.setattr("agentic_dev_loop.engine.actions._trusted_hooks_dir",
                            lambda: str(trusted))
        monkeypatch.chdir(repo)

        update = commit({"subject": "unit U2", "commit_allowed": True}, COMMIT_PARAMS)

        assert update == {}
        assert not marker.exists()  # the repo hook never ran
        assert "feat: construct unit U2 (agent dev loop)" in git_log(repo)

    def test_commit_failure_does_not_fail_the_run(self, tmp_path, monkeypatch,
                                                  capsys):
        """The work is already verified - a git error is a loud warning, not
        a pipeline failure. The trusted hooksPath still applies (a failing
        hook in the trusted dir fails the commit loudly, non-fatally)."""
        repo = tmp_path / "repo"
        init_git_repo(repo)
        (repo / "feature.cs").write_text("// constructed by the pipeline")
        trusted = tmp_path / "trusted-hooks"
        trusted.mkdir()
        hook = trusted / "pre-commit"
        hook.write_text("#!/bin/sh\nexit 1\n")
        hook.chmod(0o755)
        if not os.access(hook, os.X_OK):
            pytest.skip("tmp filesystem is mounted noexec; git ignores the hook")
        monkeypatch.setattr("agentic_dev_loop.engine.actions._trusted_hooks_dir",
                            lambda: str(trusted))
        monkeypatch.chdir(repo)

        update = commit({"subject": "unit U2", "commit_allowed": True}, COMMIT_PARAMS)

        assert update == {}
        assert "auto-commit failed" in capsys.readouterr().err

    def test_add_excludes_live_checkpoint_db(self, tmp_path, monkeypatch):
        """The live checkpointer DB under state_dir stays open for the whole
        run, so `git add -A` cannot hash it. The add must exclude sqlite
        files under the state dir."""
        repo = tmp_path / "repo"
        init_git_repo(repo)
        (repo / "feature.cs").write_text("// constructed by the pipeline")
        db = repo / ".pr" / "loop-checkpoints.sqlite"
        db.parent.mkdir(parents=True)
        db.write_bytes(b"\x00" * 64)
        monkeypatch.chdir(repo)

        commit({"subject": "unit U2", "commit_allowed": True,
                "state_dir": str(db.parent)}, COMMIT_PARAMS)

        assert "feat: construct unit U2 (agent dev loop)" in git_log(repo)
        tracked = subprocess.run(
            ["git", "ls-files"], cwd=repo, capture_output=True,
            text=True).stdout
        assert "feature.cs" in tracked
        assert "loop-checkpoints.sqlite" not in tracked

    def test_git_add_failure_warns_and_skips_commit(self, tmp_path, monkeypatch,
                                                    capsys):
        """A failed `git add` (e.g. a file git cannot stat) must not surface
        as a misleading 'commit failed: nothing to commit' - warn with the
        real add error and skip the commit entirely."""
        repo = tmp_path / "repo"
        init_git_repo(repo)
        (repo / "feature.cs").write_text("x")
        monkeypatch.chdir(repo)

        from agentic_dev_loop.engine import actions as actions_mod
        real_git = actions_mod._git
        calls = []

        def fake_git(args, cwd=None):
            calls.append(list(args))
            if "add" in args:
                return subprocess.CompletedProcess(
                    ["git", *args], 128, "",
                    "fatal: cannot stat '.pr/loop-checkpoints.sqlite'")
            return real_git(args, cwd=cwd)

        monkeypatch.setattr("agentic_dev_loop.engine.actions._git", fake_git)

        commit({"subject": "unit U2", "commit_allowed": True}, COMMIT_PARAMS)

        err = capsys.readouterr().err
        assert "auto-commit failed" in err
        assert "cannot stat" in err
        assert not any(call[2] == "commit" for call in calls)

    def test_no_exclude_pathspec_when_state_dir_outside_repo(self, tmp_path,
                                                             monkeypatch):
        """A state dir outside the repo has no locked DB inside the repo -
        the add must not carry an exclude pathspec (and must not crash
        building a relative path out of it)."""
        outside = tmp_path / "state" / ".pr"
        repo = tmp_path / "repo"
        init_git_repo(repo)
        (repo / "feature.cs").write_text("x")
        monkeypatch.chdir(repo)

        from agentic_dev_loop.engine import actions as actions_mod
        real_git = actions_mod._git
        add_calls = []

        def fake_git(args, cwd=None):
            if "add" in args:
                add_calls.append(list(args))
            return real_git(args, cwd=cwd)

        monkeypatch.setattr("agentic_dev_loop.engine.actions._git", fake_git)

        commit({"subject": "unit U2", "commit_allowed": True,
                "state_dir": str(outside)}, COMMIT_PARAMS)

        assert len(add_calls) == 1
        assert not any(a.startswith(":(exclude)") for a in add_calls[0])
        assert "feat: construct unit U2 (agent dev loop)" in git_log(repo)

class TestToolingExcludePathspec:
    def test_empty_when_no_paths(self):
        assert tooling_exclude_pathspec(None) == []
        assert tooling_exclude_pathspec([]) == []

    def test_excludes_relative_paths(self):
        assert tooling_exclude_pathspec(["tools", "aidlc-docs", "pipeline.yaml"]) == \
            [":(exclude)tools", ":(exclude)aidlc-docs", ":(exclude)pipeline.yaml"]

    def test_skips_empty_and_parent_markers(self):
        assert tooling_exclude_pathspec(["", ".", ".."]) == []


class TestCommitExcludesTooling:
    def test_tooling_changes_not_swept_into_commit(self, tmp_path, monkeypatch):
        repo = tmp_path / "repo"
        init_git_repo(repo)
        (repo / "tools").mkdir()
        (repo / "tools" / "gate.sh").write_text("#!/bin/sh\ntrue\n")
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-m", "add tooling"], cwd=repo,
                       check=True, capture_output=True)
        # dirty tooling + constructed work
        (repo / "tools" / "gate.sh").write_text("#!/bin/sh\nfalse\n")
        (repo / "feature.cs").write_text("// constructed by the pipeline")
        monkeypatch.chdir(repo)

        commit({"subject": "unit U2", "commit_allowed": True,
                "tooling_paths": ["tools"]}, COMMIT_PARAMS)

        assert "feat: construct unit U2 (agent dev loop)" in git_log(repo)
        tracked = subprocess.run(["git", "ls-files"], cwd=repo,
                                 capture_output=True, text=True).stdout
        assert "feature.cs" in tracked
        status = subprocess.run(["git", "status", "--porcelain"], cwd=repo,
                                capture_output=True, text=True).stdout
        assert "tools/gate.sh" in status  # still dirty - never swept
