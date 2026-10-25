"""Registry + extension-loading tests."""

import pytest

from agentic_dev_loop.engine import actions
from agentic_dev_loop.engine.registry import (
    Registry, default_registry, load_extensions,
    validate_action_mounts_egress, validate_action_network,
)
from sample_ext import SampleTool


class _Loader:
    """Minimal document loader carrying one CLI option string."""

    def __init__(self, name: str, option: str, defaultable: bool = False):
        self.name = name
        self.option = option
        self.defaultable = defaultable

    def add_cli_args(self, parser):
        parser.add_argument(self.option)

    def resolve(self, args):  # pragma: no cover - selection flow is Task 7
        raise NotImplementedError


class TestBuiltinRegistration:
    def test_default_registry_has_builtin_actions(self):
        reg = default_registry()
        assert reg.actions["commit"] is actions.commit

    def test_kill_listeners_action_not_registered(self):
        """The port_sweep pipeline step that used the node-shaped wrapper is
        gone; the startup sweep calls kill_listeners_on_port directly."""
        assert "kill_listeners" not in default_registry().actions


class TestActionRegistration:
    def test_duplicate_action_name_raises(self):
        reg = Registry()
        reg.add_action("x", lambda s, p: {})
        with pytest.raises(ValueError, match="'x'"):
            reg.add_action("x", lambda s, p: {})

    def test_host_action_sealed_after_builtins(self):
        """020 action-contract rule: after `default_registry` seals the
        built-in-only host path, a consumer (non-built-in) cannot register a
        host-side function."""
        reg = default_registry()
        with pytest.raises(ValueError, match="add_sandbox_action"):
            reg.add_action("evil", lambda s, p: {})

    def test_sealed_host_action_via_extension_is_refused(self):
        reg = default_registry()
        with pytest.raises(ValueError, match="add_sandbox_action"):
            load_extensions(["sample_ext:HostActionExtension"], reg)

    def test_sandbox_action_registration_and_duplicate(self):
        reg = Registry()
        reg.add_sandbox_action("lint", "make lint")
        assert reg.sandbox_actions["lint"] == "make lint"
        with pytest.raises(ValueError, match="'lint'"):
            reg.add_sandbox_action("lint", "make lint")

    def test_sandbox_action_rejects_empty_script(self):
        reg = Registry()
        with pytest.raises(ValueError, match="non-empty script"):
            reg.add_sandbox_action("lint", "   ")

    def test_host_and_sandbox_action_names_share_namespace(self):
        reg = Registry()
        reg.add_sandbox_action("commit", "echo x")
        with pytest.raises(ValueError, match="'commit'"):
            reg.add_action("commit", lambda s, p: {})


class TestToolRegistration:
    def test_empty_name_rejected(self):
        with pytest.raises(ValueError, match="valid identifier"):
            Registry().add_tool("", SampleTool)

    def test_whitespace_name_rejected(self):
        with pytest.raises(ValueError, match="valid identifier"):
            Registry().add_tool("  sample  ", SampleTool)

    def test_collision_with_sdk_registry_rejected(self):
        reg = Registry()
        reg.add_tool("proc_unique_tool", SampleTool)
        with pytest.raises(ValueError, match="already"):
            reg.add_tool("proc_unique_tool", SampleTool)

    def test_collision_via_extension_names_the_extension_and_owner(self):
        load_extensions(["sample_ext:CollisionExtension"], Registry())
        with pytest.raises(ValueError) as excinfo:
            load_extensions(["sample_ext:CollisionExtension"], Registry())
        message = str(excinfo.value)
        assert "collision-extension" in message
        assert "sample_ext" in message


class TestDocumentLoaderRegistration:
    def test_records_contributed_cli_options(self):
        reg = Registry()
        reg.add_document_loader(_Loader("alpha", "--alpha-flag"))
        assert reg.cli_option_owners["--alpha-flag"] == "alpha"

    def test_duplicate_cli_option_names_both_loaders(self):
        reg = Registry()
        reg.add_document_loader(_Loader("alpha", "--shared"))
        with pytest.raises(ValueError) as excinfo:
            reg.add_document_loader(_Loader("beta", "--shared"))
        message = str(excinfo.value)
        assert "alpha" in message and "beta" in message and "--shared" in message


class TestExtensionsDir:
    def test_resolves_from_extensions_dir(self, tmp_path):
        ext_dir = tmp_path / "exts"
        ext_dir.mkdir()
        (ext_dir / "myext.py").write_text(
            "class MyExt:\n"
            "    name = 'myext'\n"
            "    def register(self, registry):\n"
            "        registry.add_sandbox_action('myact', 'echo hi')\n")
        reg = Registry()
        load_extensions(["myext:MyExt"], reg, extensions_dir=ext_dir)
        assert reg.sandbox_actions["myact"] == "echo hi"

    def test_workspace_resident_extension_refused_naming_file(self, tmp_path):
        ws = tmp_path / "ws"
        ext_dir = ws / "extensions"
        ext_dir.mkdir(parents=True)
        (ext_dir / "bad.py").write_text(
            "class Bad:\n"
            "    name = 'bad'\n"
            "    def register(self, registry):\n"
            "        pass\n")
        reg = Registry()
        with pytest.raises(ValueError) as excinfo:
            load_extensions(["bad:Bad"], reg, extensions_dir=ext_dir,
                            workspace=ws)
        message = str(excinfo.value)
        assert "bad.py" in message
        assert "inside the workspace" in message

    def test_workspace_resident_tool_handler_refused(self, tmp_path, monkeypatch):
        ws = tmp_path / "ws"
        ws.mkdir()
        (ws / "wstool.py").write_text("class WsTool:\n    pass\n")
        monkeypatch.syspath_prepend(str(ws))
        import importlib
        toolmod = importlib.import_module("wstool")
        reg = Registry()
        reg.workspace = ws
        with pytest.raises(ValueError) as excinfo:
            reg.add_tool("ws_probe", toolmod.WsTool)
        assert "wstool.py" in str(excinfo.value)

    def test_workspace_resident_extension_refused_before_import(self, tmp_path):
        """020 ordering: the refusal must run BEFORE import - import IS
        execution, so a workspace-resident module's top-level code must never
        run in the host. The sentinel proves the module body never executed."""
        ws = tmp_path / "ws"
        ext_dir = ws / "extensions"
        ext_dir.mkdir(parents=True)
        sentinel = tmp_path / "executed"
        (ext_dir / "bad.py").write_text(
            f"from pathlib import Path\n"
            f"Path({str(sentinel)!r}).write_text('ran')\n"
            "class Bad:\n"
            "    name = 'bad'\n"
            "    def register(self, registry):\n"
            "        pass\n")
        reg = Registry()
        with pytest.raises(ValueError) as excinfo:
            load_extensions(["bad:Bad"], reg, extensions_dir=ext_dir,
                            workspace=ws)
        assert "bad.py" in str(excinfo.value)
        assert not sentinel.exists()

    def test_bundled_engine_extension_not_refused_when_engine_in_workspace(
            self, tmp_path):
        """020: the workspace-resident refusal is for consumer-trusted code;
        engine-bundled modules (agentic_dev_loop.*) are trusted built-ins
        already guarded by check_orchestrator_install, so they must load even
        when the engine resolves under the workspace (the dogfood layout)."""
        from pathlib import Path as P
        repo_root = P(__file__).resolve().parents[1]
        reg = Registry()
        load_extensions(
            ["agentic_dev_loop.engine.extensions.aidlc:AidlcExtension"], reg,
            workspace=repo_root)
        assert any(loader.name == "ai-dlc" for loader in reg.document_loaders)


class TestLoadExtensions:
    def test_fixture_extension_action_becomes_invocable(self):
        reg = Registry()
        load_extensions(["sample_ext:SampleExtension"], reg)
        assert reg.sandbox_actions["sample_action"] == "echo sample-ran {subject}"

    def test_extension_tool_registers_with_sdk(self):
        from openhands.sdk.tool import registry as sdk_registry
        reg = Registry()
        load_extensions(["sample_ext:ToolExtension"], reg)
        assert "sample" in sdk_registry.list_registered_tools()

    @pytest.mark.parametrize("ref", ["no_colon", ":NoModule", "nope:"])
    def test_malformed_ref_raises_naming_it(self, ref):
        with pytest.raises(ValueError) as excinfo:
            load_extensions([ref], Registry())
        assert ref in str(excinfo.value)

    def test_unknown_module_raises_naming_the_ref(self):
        with pytest.raises(ValueError) as excinfo:
            load_extensions(["no_such_module_xyz:Thing"], Registry())
        assert "no_such_module_xyz:Thing" in str(excinfo.value)

    def test_unknown_class_raises_naming_the_ref(self):
        with pytest.raises(ValueError) as excinfo:
            load_extensions(["sample_ext:NoSuchClass"], Registry())
        assert "sample_ext:NoSuchClass" in str(excinfo.value)

    def test_instantiation_failure_raises_naming_the_ref(self):
        with pytest.raises(ValueError) as excinfo:
            load_extensions(["sample_ext:BoomExtension"], Registry())
        assert "sample_ext:BoomExtension" in str(excinfo.value)

class TestValidateActionNetwork:
    def test_host_side_action_rejects_network(self):
        reg = default_registry()
        with pytest.raises(ValueError, match="host-side") as excinfo:
            validate_action_network("commit", "commit", True, reg)
        assert "commit" in str(excinfo.value)

    def test_sandbox_action_allows_network(self):
        reg = default_registry()
        reg.add_sandbox_action("lint", "make lint")
        validate_action_network("lint", "lint", True, reg)  # must not raise

    def test_unknown_action_is_not_this_guard(self):
        reg = default_registry()
        validate_action_network("x", "nope", True, reg)  # must not raise


class TestValidateActionMountsEgress:
    def test_host_side_action_rejects_mounts(self):
        reg = default_registry()
        with pytest.raises(ValueError, match="host-side"):
            validate_action_mounts_egress("commit", "commit", [{"path": "src"}],
                                          None, reg)

    def test_host_side_action_rejects_egress(self):
        reg = default_registry()
        with pytest.raises(ValueError, match="host-side"):
            validate_action_mounts_egress("commit", "commit", [],
                                          {"class": "browser"}, reg)

    def test_sandbox_action_allows_both(self):
        reg = default_registry()
        reg.add_sandbox_action("lint", "make lint")
        validate_action_mounts_egress("lint", "lint", [{"path": "src"}],
                                      {"class": "browser"}, reg)
