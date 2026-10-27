"""Pure subprocess helper tests - PBT-02 properties plus unit pins.

``needs_shell``, ``split_command``, ``is_pid_token`` and ``child_env`` are
stateless pure functions shared by the command-step and action-step
surfaces; property tests exercise the invariants that keep the subprocess
boundary fail-safe (BR-4, BR-5)."""

import signal
import string

from hypothesis import given, settings
from hypothesis import strategies as st

from agentic_dev_loop.engine.proc import (
    child_env, host_shell_execution, is_pid_token, kill_process_tree,
    needs_shell, split_command,
)


class TestNeedsShell:
    def test_no_metacharacters_is_simple(self):
        assert needs_shell("pytest tests/ -q") is False
        assert needs_shell(".venv/bin/python -m pytest") is False
        assert needs_shell("") is False

    def test_shell_metacharacters_need_shell(self):
        for cmd in ("a && b", "a | b", "a; b", "a > b", "a < b",
                    "echo $HOME", "echo `date`", "cd ~", "ls *.py",
                    "ls file?.py", "echo [ab]", "echo a&b"):
            assert needs_shell(cmd) is True, cmd

    @given(st.text(alphabet="abcXYZ019_-/=.,: ", max_size=40))
    @settings(max_examples=200)
    def test_plain_sequences_never_need_shell(self, cmd: str):
        assert needs_shell(cmd) is False

    @given(st.text(alphabet="abcXYZ019_-/ ", max_size=20),
           st.sampled_from(["&", "|", ";", ">", "<", "$", "`", "~", "*", "?"]))
    @settings(max_examples=200)
    def test_metachar_anywhere_needs_shell(self, prefix: str, metachar: str):
        assert needs_shell(f"{prefix}{metachar}{prefix}") is True


class TestHostShellExecution:
    """How the HOST (non-sandbox) path executes a shell-needing command."""

    def test_uses_shell_true(self):
        argv, shell = host_shell_execution("a && b")
        assert (argv, shell) == ("a && b", True)


class TestSplitCommand:
    def test_splits_into_argv(self):
        assert split_command(".venv/bin/python -m pytest tests/ -q") == \
            [".venv/bin/python", "-m", "pytest", "tests/", "-q"]

    def test_quotes_group_arguments(self):
        assert split_command("python -c \"print('hi')\"") == \
            ["python", "-c", "print('hi')"]

    @given(st.lists(st.text(alphabet="abcXYZ019_-/.", min_size=1),
                    min_size=1, max_size=6))
    @settings(max_examples=200)
    def test_round_trip(self, tokens: list[str]):
        assert split_command(" ".join(tokens)) == tokens


class TestIsPidToken:
    def test_accepts_plain_decimal(self):
        assert is_pid_token("0") is True
        assert is_pid_token("123") is True
        assert is_pid_token("00042") is True

    def test_rejects_injection_shapes(self):
        for bad in ("-9", "1;rm", "", " 123", "12a", "1 2", "+1", "0x1f"):
            assert is_pid_token(bad) is False, bad

    @given(st.text(alphabet="0123456789", min_size=1, max_size=10))
    @settings(max_examples=200)
    def test_all_decimal_strings_accepted(self, digits: str):
        assert is_pid_token(digits) is True

    @given(st.text(alphabet=string.printable, min_size=1, max_size=20)
           .filter(lambda t: not t.isdigit()))
    @settings(max_examples=200)
    def test_non_decimal_rejected(self, token: str):
        assert is_pid_token(token) is False


class TestChildEnv:
    def test_fixed_whitelist_plus_api_key(self):
        parent = {"PATH": "/usr/bin", "HOME": "/home/u", "LANG": "C.UTF-8",
                  "LC_ALL": "C", "TERM": "xterm",
                  "OPENAI_API_KEY": "sk-123", "SECRET": "x", "FOO": "bar"}
        env = child_env(api_key_env="OPENAI_API_KEY", parent=parent)
        assert env == {"PATH": "/usr/bin", "HOME": "/home/u",
                       "LANG": "C.UTF-8", "LC_ALL": "C", "TERM": "xterm",
                       "OPENAI_API_KEY": "sk-123"}

    def test_allowlist_admits_extra_names(self):
        parent = {"PATH": "/usr/bin", "HOME": "/home/u", "FOO": "bar",
                  "SECRET": "x"}
        env = child_env(allowlist=["FOO"], api_key_env="OPENAI_API_KEY",
                        parent=parent)
        assert env["FOO"] == "bar"
        assert "SECRET" not in env
        assert "OPENAI_API_KEY" not in env  # not present in the source env

    @given(
        extra=st.dictionaries(st.text(alphabet=string.ascii_lowercase,
                                      min_size=1),
                              st.text(max_size=20), max_size=8),
        allow=st.sets(st.text(alphabet=string.ascii_lowercase, min_size=1),
                      max_size=4),
    )
    @settings(max_examples=200)
    def test_scrubbing_property(self, extra: dict, allow: set):
        parent = {"PATH": "/p", "HOME": "/h", "APIKEY": "k", **extra}
        env = child_env(allowlist=list(allow), api_key_env="APIKEY",
                        parent=parent)
        assert env["APIKEY"] == "k"
        assert env["PATH"] == "/p"
        assert env["HOME"] == "/h"
        for key in parent:
            expected = key in {"PATH", "HOME", "LANG", "LC_ALL", "TERM",
                               "APIKEY"} or key in allow
            assert (key in env) is expected, key


class TestKillProcessTree:
    """The timeout reap path: ``start_new_session`` made ``proc.pid`` the
    process-group id, so ``killpg`` reaps the whole tree."""

    class _Proc:
        def __init__(self, log):
            self.pid = 99
            self.log = log

        def kill(self):
            self.log.append("kill")

    def test_reaps_the_whole_process_group(self, monkeypatch):
        killed = []
        monkeypatch.setattr(
            "agentic_dev_loop.engine.proc.os.killpg",
            lambda pid, sig: killed.append((pid, sig)))
        proc = self._Proc([])

        kill_process_tree(proc)

        assert killed == [(99, signal.SIGKILL)]
        assert proc.log == []  # the wrapper needs no separate kill
