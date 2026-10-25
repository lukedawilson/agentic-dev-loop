"""Extension registry - the seam between core and consumer/bundled extensions.

Core pre-registers the built-ins (action `commit`, document loader `plan`);
everything else arrives via `load_extensions` from the pipeline's
`extensions:` list - `module:Class` strings, explicit only, no
auto-discovery.

Host boundary (020): consumer actions are sandbox-executed, command-shaped
steps (`add_sandbox_action`); the host-side `add_action` path is sealed after
`default_registry` registers the trusted built-ins, so a workspace extension
can never register code that runs in the host process. Consumer extension
and tool modules are additionally refused BEFORE import when they resolve
under the workspace mount (import is execution).
"""

import argparse
import importlib
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Protocol


class DocumentLoader(Protocol):
    """Supplies the pipeline's doc bundle + subject. Selected by its CLI
    flag at invocation; `defaultable` loaders may also run with no flag."""

    name: str
    defaultable: bool

    def add_cli_args(self, parser: argparse.ArgumentParser) -> None: ...

    def resolve(self, args: argparse.Namespace) -> tuple[dict[str, str], str]:
        """Return (docs, subject). Empty docs fail fast at the call site."""
        ...


class Extension(Protocol):
    name: str

    def register(self, registry: "Registry") -> None: ...


def _contributed_options(loader: DocumentLoader) -> list[tuple[str, str]]:
    """The (option string, dest) pairs a loader would add - probed with a
    throwaway parser so collisions are caught at load time, not parse time,
    and the selection flow can map parsed args back to their loader."""
    probe = argparse.ArgumentParser(add_help=False)
    loader.add_cli_args(probe)
    return [(opt, action.dest) for action in probe._actions
            for opt in action.option_strings]


def _module_file(obj: object) -> Path | None:
    """The resolved source file of ``obj``'s defining module, or None when it
    has none (builtins, dynamically created classes). Modules carry
    ``__file__`` directly; classes carry ``__module__``."""
    file = getattr(obj, "__file__", None)
    if file:
        return Path(file).resolve()
    module_name = getattr(obj, "__module__", None)
    if not module_name:
        return None
    module = sys.modules.get(module_name)
    file = getattr(module, "__file__", None)
    return Path(file).resolve() if file else None


def _is_engine_bundled(module_name: str) -> bool:
    """True when ``module_name`` names the engine package or a submodule -
    trusted built-ins guarded by the orchestrator-install self-check, not by
    the workspace-resident refusal (020)."""
    return module_name == "agentic_dev_loop" or module_name.startswith(
        "agentic_dev_loop.")


def _module_origin(module_name: str) -> Path | None:
    """Resolve ``module_name``'s file location WITHOUT executing it.

    ``importlib.util.find_spec`` locates the module through the import
    machinery but never runs the module's own top-level code - the host must
    check workspace-residence before ``import_module`` (import IS execution;
    020). Returns None when the location can't be determined (builtins,
    namespace packages, not found); the import itself then surfaces any real
    error."""
    import importlib.util
    try:
        spec = importlib.util.find_spec(module_name)
    except (ImportError, ValueError, AttributeError):
        return None
    if spec is None or spec.origin is None:
        return None
    return Path(spec.origin).resolve()


def _refuse_workspace_resident_file(kind: str, ref: str, file: Path | None,
                                    workspace: Path | None) -> None:
    """Fail fast when ``file`` lives under the workspace - agent-writable
    files must never be imported/registered by the host (020)."""
    if workspace is None or file is None:
        return
    if file.is_relative_to(workspace.resolve()):
        raise ValueError(
            f"{kind} {ref!r} resolves to {file}, inside the workspace "
            f"{workspace} - workspace-resident {kind}s are refused; install "
            "consumer extensions outside the workspace "
            "(~/.agentic-dev-loop/extensions)")


def _refuse_workspace_resident(kind: str, ref: str, obj: object,
                               workspace: Path | None) -> None:
    """Fail fast when ``obj``'s defining module lives under the workspace.

    For objects already in hand (tool classes): their module was already
    imported, so this is defense-in-depth on top of the pre-import refusal in
    ``load_extensions``."""
    _refuse_workspace_resident_file(kind, ref, _module_file(obj), workspace)


class Registry:
    def __init__(self) -> None:
        self.actions: dict[str, Callable] = {}       # host-side built-ins only
        self.sandbox_actions: dict[str, str] = {}    # sandbox-executed (script)
        self.document_loaders: list[DocumentLoader] = []
        self.cli_option_owners: dict[str, str] = {}  # option string -> loader name
        self.cli_dests: dict[str, str] = {}  # argparse dest -> loader name
        self._loading_extension: str | None = None
        self._host_actions_open: bool = True
        self.workspace: Path | None = None

    def add_action(self, name: str, fn: Callable) -> None:
        """Register a host-side action ``(state, params) -> state update``.

        Trusted built-ins only: ``default_registry`` seals this path after
        registering ``commit``, so a consumer extension calling it is refused
        - non-built-in actions must use ``add_sandbox_action``."""
        if not self._host_actions_open:
            raise ValueError(
                f"action {name!r} cannot register a host-side function - only "
                "engine built-ins may; consumer actions must use "
                "add_sandbox_action(name, script) so they execute inside the "
                "sandbox")
        if name in self.actions or name in self.sandbox_actions:
            raise ValueError(f"Duplicate action name {name!r}")
        self.actions[name] = fn

    def add_sandbox_action(self, name: str, script: str) -> None:
        """Register a sandbox-executed consumer action (020): a command-shaped
        script rendered with the step's ``params:`` (plus ``{subject}``) and
        run through the same sandbox path as command steps - exit code is the
        verdict. Consumer actions never run in the host process."""
        if not name or name != name.strip():
            raise ValueError(
                f"action name {name!r} is not a valid identifier - a non-empty "
                "name with no leading/trailing whitespace is required")
        if not isinstance(script, str) or not script.strip():
            raise ValueError(
                f"sandbox action {name!r} requires a non-empty script string")
        if name in self.actions or name in self.sandbox_actions:
            raise ValueError(f"Duplicate action name {name!r}")
        self.sandbox_actions[name] = script

    def seal_host_actions(self) -> None:
        """Close the host-side action path - call after the built-ins are
        registered, before any consumer extension loads."""
        self._host_actions_open = False

    def add_document_loader(self, loader: DocumentLoader) -> None:
        pairs = _contributed_options(loader)
        for opt, _dest in pairs:
            owner = self.cli_option_owners.get(opt)
            if owner is not None:
                raise ValueError(
                    f"CLI option {opt!r} is contributed by both document "
                    f"loader {owner!r} and {loader.name!r}")
        for opt, dest in pairs:
            self.cli_option_owners[opt] = loader.name
            self.cli_dests[dest] = loader.name
        self.document_loaders.append(loader)

    def add_tool(self, name: str, tool: type) -> None:
        """Register an SDK tool class (agent frontmatter names resolve
        against the SDK's global registry). The name is validated first
        (BR-3): a non-empty identifier that does not collide with any name
        already in the SDK registry - the SDK silently overwrites duplicates,
        so agentic-dev-loop rejects before ``register_tool`` ever runs.

        The tool class's module must not live under the workspace (020): a
        tool handler is consumer-trusted code executed by the agent runtime,
        so agent-writable tool modules are refused at registration."""
        _refuse_workspace_resident("tool", name, tool, self.workspace)
        from openhands.sdk.tool import register_tool
        from openhands.sdk.tool import registry as sdk_registry

        if not name or name != name.strip():
            raise ValueError(
                f"tool name {name!r} is not a valid identifier - a non-empty "
                "name with no leading/trailing whitespace is required")
        if name in sdk_registry.list_registered_tools():
            existing = sdk_registry.get_tool_module_qualnames().get(name)
            existing = f" (already owned by {existing!r})" if existing else ""
            owner = self._loading_extension
            if owner is not None:
                raise ValueError(
                    f"extension {owner!r} tried to register tool {name!r}, which "
                    f"is already in the SDK registry{existing} - refusing to "
                    "silently overwrite it")
            raise ValueError(
                f"tool name {name!r} is already in the SDK registry{existing} - "
                "refusing to silently overwrite it")
        register_tool(name, tool)


def default_registry() -> Registry:
    """Core built-ins: action `commit` (host-side, deliberately), document
    loader `plan` (flag `--plan PATH`). The host-side action path is sealed
    immediately after, so nothing consumer-registered can run host-side."""
    from . import actions as _actions
    from .loaders import PlanDocumentLoader

    reg = Registry()
    reg.add_action("commit", _actions.commit)
    reg.add_document_loader(PlanDocumentLoader())
    reg.seal_host_actions()
    return reg


def load_extensions(refs: list[str], registry: Registry, *,
                    extensions_dir: str | Path | None = None,
                    workspace: str | Path | None = None) -> None:
    """Import, instantiate, and register each `module:Class` ref. Any failure
    is a loud error naming the ref.

    Consumer modules resolve from ``extensions_dir`` (outside the workspace -
    020); the bundled engine extensions still import from the installed
    engine package. A module whose file resolves under ``workspace`` is
    refused BEFORE import, naming the file - no workspace file is ever
    imported by the host (import is execution)."""
    registry.workspace = Path(workspace) if workspace is not None else None
    added_path: str | None = None
    if extensions_dir is not None:
        ext_path = str(Path(extensions_dir).expanduser().resolve())
        if ext_path not in sys.path:
            sys.path.insert(0, ext_path)
            added_path = ext_path
    try:
        for ref in refs:
            module_name, sep, class_name = ref.partition(":")
            if not sep or not module_name or not class_name:
                raise ValueError(
                    f"Invalid extension ref {ref!r} - expected 'module:Class'")
            # 020: refuse a workspace-resident module BEFORE importing it -
            # import is execution, so the location check must run first.
            # Engine-bundled modules are trusted built-ins (guarded by
            # check_orchestrator_install) and skip this refusal.
            if not _is_engine_bundled(module_name):
                _refuse_workspace_resident_file(
                    "extension", ref, _module_origin(module_name),
                    registry.workspace)
            try:
                module = importlib.import_module(module_name)
                cls = getattr(module, class_name)
            except (ImportError, AttributeError) as e:
                raise ValueError(f"Cannot load extension {ref!r}: {e}") from e
            try:
                ext = cls()
            except Exception as e:
                raise ValueError(f"Cannot instantiate extension {ref!r}: {e}") from e
            registry._loading_extension = getattr(ext, "name", None)
            try:
                ext.register(registry)
            finally:
                registry._loading_extension = None
    finally:
        if added_path is not None:
            sys.path.remove(added_path)


def validate_action_network(step_name: str, action: str, network: bool,
                            registry: "Registry") -> None:
    """Reject `network: true` on a host-side built-in action (020): host-side
    actions run in the host process and never take egress. Sandbox (consumer)
    actions may take network, exactly like command steps. The config schema
    can't distinguish the two (the registry isn't loaded yet), so this runs
    once the registry is known."""
    if network and action in registry.actions:
        raise ValueError(
            f"step {step_name!r}: action {action!r} is host-side and cannot "
            "declare 'network: true' - host-side actions run in the host "
            "process; only sandbox (consumer) actions may take network, like "
            "command steps")


def validate_action_mounts_egress(step_name: str, action: str,
                                  mounts: list, egress, registry: "Registry") -> None:
    """Reject ``mounts:``/``egress:`` on a host-side built-in action (unit-5).

    Host-side actions run in the host process and are never sandbox-mounted or
    egress-routed; sandbox (consumer) actions may declare both, exactly like
    command steps. Mirrors ``validate_action_network`` (020)."""
    if action in registry.actions and (mounts or egress is not None):
        raise ValueError(
            f"step {step_name!r}: action {action!r} is host-side and cannot "
            "declare 'mounts' or 'egress' - host-side actions run in the host "
            "process; only sandbox (consumer) actions may declare mounts or "
            "egress, like command steps")
