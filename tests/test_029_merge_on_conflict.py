"""029: ``merge_on_conflict:`` config surface and validation.

Top-level run-wide list of append-style logs that sync-back union-merges on a
concurrent-edit conflict instead of raising. Same declared-path vocabulary as
``sync_excludes:``; literals under a literal ``sync_excludes:``/``caches:``
entry are redundant and rejected.
"""

import yaml
import pytest
from pydantic import ValidationError

from agentic_dev_loop.core.config import load_config


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


class TestCoercionAndAcceptance:
    def test_single_string_coerced_to_list(self, tmp_path):
        data = _base()
        data["merge_on_conflict"] = "aidlc-docs/audit.md"
        assert _load(tmp_path, data).merge_on_conflict == ["aidlc-docs/audit.md"]

    def test_defaults_empty(self, tmp_path):
        assert _load(tmp_path, _base()).merge_on_conflict == []

    def test_wildcards_accepted(self, tmp_path):
        data = _base()
        data["merge_on_conflict"] = ["**/audit.md", "*.log"]
        assert _load(tmp_path, data).merge_on_conflict == ["**/audit.md", "*.log"]


class TestRejected:
    def test_traversal(self, tmp_path):
        data = _base()
        data["merge_on_conflict"] = ["../escape"]
        with pytest.raises(ValidationError, match="traversal"):
            _load(tmp_path, data)

    def test_absolute(self, tmp_path):
        data = _base()
        data["merge_on_conflict"] = ["/abs"]
        with pytest.raises(ValidationError, match="repo-relative"):
            _load(tmp_path, data)

    def test_git(self, tmp_path):
        data = _base()
        data["merge_on_conflict"] = [".git/config"]
        with pytest.raises(ValidationError, match=r"\.git"):
            _load(tmp_path, data)

    def test_host_only(self, tmp_path):
        data = _base()
        data["merge_on_conflict"] = [".venv"]
        with pytest.raises(ValidationError, match="host-only"):
            _load(tmp_path, data)

    def test_under_sync_excludes_redundant(self, tmp_path):
        data = _base()
        data["sync_excludes"] = ["scratch"]
        data["merge_on_conflict"] = ["scratch/audit.md"]
        with pytest.raises(ValidationError, match="redundant"):
            _load(tmp_path, data)

    def test_under_cache_redundant(self, tmp_path):
        data = _base()
        data["caches"] = ["node_modules"]
        data["merge_on_conflict"] = ["node_modules/audit.md"]
        with pytest.raises(ValidationError, match="redundant"):
            _load(tmp_path, data)

    def test_wildcard_overlap_not_rejected(self, tmp_path):
        """Wildcard overlap is fuzzy, never a hard contradiction (028 parity)."""
        data = _base()
        data["sync_excludes"] = ["**/bin/**"]
        data["caches"] = ["qa"]
        data["merge_on_conflict"] = ["**/audit.md"]
        assert _load(tmp_path, data).merge_on_conflict == ["**/audit.md"]

    def test_clear_entry_accepted(self, tmp_path):
        data = _base()
        data["merge_on_conflict"] = ["aidlc-docs/audit.md"]
        assert _load(tmp_path, data).merge_on_conflict == ["aidlc-docs/audit.md"]
