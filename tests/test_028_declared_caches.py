"""028: pipeline-declared ``caches:``/``sync_excludes:`` config surface.

Load-time validation and the shared exclusion matcher, plus the host-side
shebang repair for a venv built under a previous run's export path.
"""

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from agentic_dev_loop.core.config import PipelineConfig, load_config
from agentic_dev_loop.core.paths import dir_excluded, path_excluded
from agentic_dev_loop.engine.isolation import repair_venv_shebangs


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
        data["caches"] = ".nuget-packages"
        data["sync_excludes"] = "**/bin/**"
        cfg = _load(tmp_path, data)
        assert cfg.caches == [".nuget-packages"]
        assert cfg.sync_excludes == ["**/bin/**"]

    def test_defaults_empty(self, tmp_path):
        cfg = _load(tmp_path, _base())
        assert cfg.caches == []
        assert cfg.sync_excludes == []

    def test_wildcard_excludes_and_mount_dot_accepted(self, tmp_path):
        data = _base()
        data["caches"] = ["node_modules", "qa/.browsers"]
        data["sync_excludes"] = ["**/bin/**", "**/obj/**", "*.dll"]
        data["steps"] = [{"name": "dev", "agent": "dev",
                          "mounts": [{"path": ".", "mode": "rw"}],
                          "on_pass": "success"}]
        cfg = _load(tmp_path, data)
        assert cfg.caches == ["node_modules", "qa/.browsers"]
        assert cfg.sync_excludes == ["**/bin/**", "**/obj/**", "*.dll"]


class TestCachePathRejected:
    @pytest.mark.parametrize("path", ["../escape", "src/../../x", "a/../b"])
    def test_traversal(self, tmp_path, path):
        data = _base()
        data["caches"] = [path]
        with pytest.raises(ValidationError, match="traversal"):
            _load(tmp_path, data)

    @pytest.mark.parametrize("path", ["/etc/passwd", "\\\\windows", "C:\\\\repo"])
    def test_absolute(self, tmp_path, path):
        data = _base()
        data["caches"] = [path]
        with pytest.raises(ValidationError, match="repo-relative"):
            _load(tmp_path, data)

    def test_git(self, tmp_path):
        data = _base()
        data["caches"] = [".git"]
        with pytest.raises(ValidationError, match="\\.git"):
            _load(tmp_path, data)

    def test_host_only(self, tmp_path):
        data = _base()
        data["caches"] = [".venv"]
        with pytest.raises(ValidationError, match="host-only"):
            _load(tmp_path, data)

    def test_state_dir_collision(self, tmp_path):
        data = _base()
        data["state_dir"] = ".pr"
        data["caches"] = [".pr/cache"]
        with pytest.raises(ValidationError, match="state_dir"):
            _load(tmp_path, data)

    def test_mount_under_cache_rejected(self, tmp_path):
        data = _base()
        data["caches"] = ["node_modules"]
        data["steps"] = [{"name": "dev", "agent": "dev",
                          "mounts": [{"path": "node_modules/pkg"}]}]
        with pytest.raises(ValidationError, match="cache"):
            _load(tmp_path, data)

    def test_relocated_venv_contradiction(self, tmp_path):
        data = _base()
        data["caches"] = [".venv-sandbox"]
        data["isolation"] = {"sandbox_venv_dir": str(tmp_path / "outside-venv")}
        with pytest.raises(ValidationError, match="sandbox_venv_dir"):
            _load(tmp_path, data)


class TestSyncExcludeRejected:
    def test_traversal(self, tmp_path):
        data = _base()
        data["sync_excludes"] = ["../escape"]
        with pytest.raises(ValidationError, match="traversal"):
            _load(tmp_path, data)

    def test_absolute(self, tmp_path):
        data = _base()
        data["sync_excludes"] = ["/abs"]
        with pytest.raises(ValidationError, match="repo-relative"):
            _load(tmp_path, data)

    def test_git(self, tmp_path):
        data = _base()
        data["sync_excludes"] = [".git/config"]
        with pytest.raises(ValidationError, match="\\.git"):
            _load(tmp_path, data)

    def test_host_only(self, tmp_path):
        data = _base()
        data["sync_excludes"] = [".venv"]
        with pytest.raises(ValidationError, match="host-only"):
            _load(tmp_path, data)

    def test_exclude_under_cache_redundant(self, tmp_path):
        data = _base()
        data["caches"] = ["qa"]
        data["sync_excludes"] = ["qa/.artifacts"]
        with pytest.raises(ValidationError, match="redundant"):
            _load(tmp_path, data)

    def test_wildcard_overlap_with_cache_is_not_rejected(self, tmp_path):
        """Wildcard overlap is fuzzy, never a hard contradiction."""
        data = _base()
        data["caches"] = ["qa"]
        data["sync_excludes"] = ["**/bin/**"]
        assert _load(tmp_path, data).sync_excludes == ["**/bin/**"]


class TestArtifactShadowWarning:
    def test_produces_under_exclude_warns(self, tmp_path):
        data = _base()
        data["sync_excludes"] = ["**/bin/**"]
        data["steps"] = [{"name": "dev", "agent": "dev",
                          "produces": ["src/bin/out.dll"], "on_pass": "success"}]
        with pytest.warns(UserWarning, match="never sync back"):
            _load(tmp_path, data)

    def test_artifact_under_exclude_warns(self, tmp_path):
        data = _base()
        data["sync_excludes"] = ["**/obj/**"]
        data["steps"] = [{"name": "qa", "agent": "qa",
                          "artifact": "src/obj/report.txt",
                          "verdicts": {"pass": "PASS", "fail": "FAIL"}}]
        with pytest.warns(UserWarning, match="never sync back"):
            _load(tmp_path, data)

    def test_no_warning_when_clear(self, tmp_path):
        data = _base()
        data["sync_excludes"] = ["**/bin/**"]
        data["steps"] = [{"name": "dev", "agent": "dev",
                          "produces": ["summary.md"], "on_pass": "success"}]
        import warnings as _warnings
        with _warnings.catch_warnings():
            _warnings.simplefilter("error")
            _load(tmp_path, data)


class TestSharedMatcher:
    def test_literal_prefix(self):
        assert path_excluded("qa/.artifacts/x", ["qa/.artifacts"])
        assert path_excluded("qa/.artifacts", ["qa/.artifacts"])
        assert not path_excluded("qa/artifacts", ["qa/.artifacts"])

    def test_wildcard_bin_segment(self):
        assert path_excluded("src/proj/bin/foo.dll", ["**/bin/**"])
        assert not path_excluded("src/proj/bin", ["**/bin/**"])
        assert dir_excluded("src/proj/bin", ["**/bin/**"])

    def test_star_spans_separators(self):
        assert path_excluded("a/b/c.dll", ["*.dll"])
        assert path_excluded("c.dll", ["*.dll"])

    def test_dir_excluded_wildcard_never_prunes_nonmatching(self):
        assert not dir_excluded("src/proj/obj", ["*.dll"])

    def test_dot_excludes_whole_tree(self):
        assert path_excluded("anything/at/all", ["."])
        assert dir_excluded("src", ["."])


class TestRepairVenvShebangs:
    def test_rewrites_only_stale_export_shebangs(self, tmp_path):
        venv = tmp_path / "venv"
        (venv / "bin").mkdir(parents=True)
        state = tmp_path / "state"
        old = state / "exports" / "aaaa"
        new_export = state / "exports" / "bbbb"
        new_python = new_export / ".venv-sandbox" / "bin" / "python"
        new_python.parent.mkdir(parents=True)
        new_python.write_text("#!/bin/sh\n")  # placeholder; never executed here

        (venv / "bin" / "pip").write_text(
            f"#!{old}/.venv-sandbox/bin/python\nprint('hi')\n")
        (venv / "bin" / "python").symlink_to("/usr/bin/python")
        (venv / "bin" / "no-shebang").write_text("no shebang here\n")
        (venv / "bin" / "env-script").write_text("#!/usr/bin/env python\nx\n")

        repair_venv_shebangs(venv, state_dir=state, export_root=new_export)

        text = (venv / "bin" / "pip").read_text()
        assert text.splitlines()[0] == f"#!{new_python}"
        assert "print('hi')" in text
        assert (venv / "bin" / "python").is_symlink()
        assert (venv / "bin" / "no-shebang").read_text() == "no shebang here\n"
        assert (venv / "bin" / "env-script").read_text().startswith(
            "#!/usr/bin/env python")

    def test_repair_is_idempotent(self, tmp_path):
        venv = tmp_path / "venv"
        (venv / "bin").mkdir(parents=True)
        state = tmp_path / "state"
        new_export = state / "exports" / "bbbb"
        new_python = new_export / ".venv-sandbox" / "bin" / "python"
        new_python.parent.mkdir(parents=True)
        new_python.write_text("#!/bin/sh\n")
        script = venv / "bin" / "pip"
        script.write_text(f"#!{new_python}\nprint('hi')\n")

        repair_venv_shebangs(venv, state_dir=state, export_root=new_export)
        assert script.read_text().splitlines()[0] == f"#!{new_python}"
