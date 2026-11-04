"""Dependency-policy injection and missing-dependency parsing (021)."""

from agentic_dev_loop.core.config import PipelineConfig
from agentic_dev_loop.engine.deps import (
    MISSING_DEPS_HEADER, extension_message, parse_missing_dependencies,
    policy_injection)


def _cfg(**over):
    base = {"name": "t", "llm": {"model": "m", "api_key_env": "K"},
            "agents_dir": "a", "lockfiles": ["uv.lock"],
            "steps": [{"name": "d", "agent": "dev", "on_pass": "success"}]}
    base.update(over)
    return PipelineConfig.model_validate(base)


class TestPolicyInjection:
    def test_locked_names_declared_lockfiles(self):
        cfg = _cfg(deps_policy="locked", lockfiles=["uv.lock"])
        text = policy_injection(cfg)
        assert "`uv.lock`" in text
        assert MISSING_DEPS_HEADER in text

    def test_locked_with_multiple_lockfiles_joins_them(self):
        cfg = _cfg(lockfiles=["packages.lock.json", "package-lock.json"])
        text = policy_injection(cfg)
        assert "`packages.lock.json`, `package-lock.json`" in text

    def test_open_injects_bootstrap_protocol(self):
        cfg = _cfg(deps_policy="open")
        text = policy_injection(cfg)
        assert "add the exact-pinned entry to the dependency manifest" in text
        assert MISSING_DEPS_HEADER not in text


class TestParseMissingDependencies:
    def test_header_with_pins(self):
        text = ("Done.\n\n"
                + MISSING_DEPS_HEADER + "\n\nrequests==2.32.0\n"
                "pydantic==2.7.0\n")
        assert parse_missing_dependencies(text) == \
            ["requests==2.32.0", "pydantic==2.7.0"]

    def test_header_absent(self):
        assert parse_missing_dependencies("No dependencies needed.") == []

    def test_none_text(self):
        assert parse_missing_dependencies(None) == []

    def test_header_present_but_empty(self):
        text = f"All done.\n\n{MISSING_DEPS_HEADER}\n\n\n"
        assert parse_missing_dependencies(text) == []

    def test_section_ends_at_next_header(self):
        text = (MISSING_DEPS_HEADER + "\nrequests==2.32.0\n"
                "## Summary\n\nDone.\n")
        assert parse_missing_dependencies(text) == ["requests==2.32.0"]

    def test_header_name_is_exact(self):
        # Only the fixed header matches - decoration or case drift is malformed.
        assert parse_missing_dependencies("### Missing dependencies\nfoo==1.0\n") == []
        assert parse_missing_dependencies("## missing dependencies\nfoo==1.0\n") == []

    def test_blank_and_padded_lines_are_pins(self):
        text = f"{MISSING_DEPS_HEADER}\n\n  requests==2.32.0  \n"
        assert parse_missing_dependencies(text) == ["requests==2.32.0"]


class TestExtensionMessage:
    def test_names_lockfile_and_pins(self):
        cfg = _cfg(lockfiles=["uv.lock"])
        msg = extension_message(cfg, ["requests==2.32.0"])
        assert "`uv.lock`" in msg
        assert "requests==2.32.0" in msg

