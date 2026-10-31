"""`--dry-run`: the full load-and-validate pass with zero execution.

Everything short of running a step happens here - YAML parse, step schema
validation, extension loading (document loaders included), agents_dir /
verdict / routing-target resolution, isolation config validation, and pure
image-ref resolution (no Docker calls). No LLM is built, no sandbox is
started, and no state/checkpoint/viz writes happen, so a dry run needs no
API key.
"""

from __future__ import annotations

from pathlib import Path

from agentic_dev_loop.core.config import PipelineConfig, StepConfig, load_config
from .hostboundary import check_orchestrator_install, check_tooling_integrity
from .loaders import resolve_docs
from .isolation import (isolation_state_dir, resolve_effective_profile,
                        resolve_image_ref, resolved_seccomp_profile)
from .stages import validate_brief_inputs
from .registry import (Registry, default_registry, load_extensions,
                       validate_action_mounts_egress, validate_action_network)
from .secrets import scan_env_files


def _validate_agents(cfg: PipelineConfig, agents_dir: Path) -> None:
    """Every `agent:` step must resolve to an existing file under agents_dir
    (the same fail-fast `load_agent` enforces at run time).

    The brief prompt is engine-embedded (046), so a ``brief_inputs:`` step is
    exempt from the agents_dir lookup - it never reads a vendored file."""
    for step in cfg.steps:
        if step.agent is None or step.brief_inputs is not None:
            continue
        path = agents_dir / f"{step.agent}.agent.md"
        if not path.is_file():
            raise FileNotFoundError(f"Agent file not found: {path}")


def _validate_actions(cfg: PipelineConfig, registry: Registry) -> None:
    """Unknown action names fail at graph build time; catch them here so a
    dry run is a true load pass. Host-side built-in actions cannot take
    ``network`` (020) - deferred here because the config schema can't
    distinguish host-side from sandbox actions."""
    known = set(registry.actions) | set(registry.sandbox_actions)
    for step in cfg.steps:
        if step.action is None:
            continue
        if step.action not in known:
            raise ValueError(
                f"step {step.name!r}: unknown action {step.action!r} "
                f"(registered: {sorted(known)})")
        validate_action_network(step.name, step.action, step.network, registry)
        validate_action_mounts_egress(step.name, step.action, step.mounts,
                                      step.egress, registry)


def _resolved_image(cfg: PipelineConfig) -> tuple[str, str, float]:
    """The exec image ref + resource caps. An explicit `image: ""` is a hard
    fail (AE-SBX-005) rather than a silent fallback to the digest-pinned
    default; the env override still wins, exactly as at startup."""
    profile = resolve_effective_profile(cfg.isolation)
    if cfg.isolation is not None and cfg.isolation.image == "":
        image = resolve_image_ref("")
    else:
        image = resolve_image_ref(profile.image)
    return image, profile.memory, profile.cpus


def _step_summary(step: StepConfig) -> str:
    """The kind + key params for one step, on a single line."""
    if step.kind == "agent":
        parts = [f"agent={step.agent}"]
        if step.artifact is not None:
            parts.append(f"artifact={step.artifact}")
        if step.verdicts is not None:
            parts.append(f"verdicts={step.verdicts.pass_}/{step.verdicts.fail}")
        if step.produces:
            parts.append(f"produces={','.join(step.produces)}")
        if step.max_iterations is not None:
            parts.append(f"max_iterations={step.max_iterations}")
    elif step.kind == "command":
        parts = [f"command={step.command}"]
        if step.timeout is not None:
            parts.append(f"timeout={step.timeout:g}")
        if step.setup is not None:
            parts.append(f"setup={step.setup}")
    else:
        parts = [f"action={step.action}"]
        if step.params:
            parts.append(f"params={step.params}")
    if step.network:
        parts.append("network=True")
    return ", ".join(parts)


def _print_summary(cfg_path: Path, cfg: PipelineConfig, subject: str,
                   loader, docs: dict[str, str], image: str, memory: str,
                   cpus: float, state_dir: Path) -> None:
    print(f"Pipeline: {cfg_path.resolve()}")
    print(f"Subject: {subject}")
    if loader is not None:
        print(f"Document loader: {loader.name} ({len(docs)} docs)")
        for key in sorted(docs):
            print(f"  - {key}")
    else:
        print("Document loader: none")
    print("Steps:")
    for step in cfg.steps:
        print(f"  - {step.name} [{step.kind}]: {_step_summary(step)}")
    print("Isolation:")
    print("  backend: docker")
    print(f"  image: {image}")
    print(f"  memory: {memory}")
    print(f"  cpus: {cpus}")
    print(f"  state_dir: {state_dir}")
    print("dry run complete - nothing executed")


def dry_run(cfg_path, args, registry: Registry | None = None) -> int:
    """Validate a pipeline end-to-end without executing it, and print a
    summary plan. Returns 0 on a clean load; any load failure propagates as
    the existing loud error (the CLI exits 1).

    `registry` carries extensions already loaded by the CLI (build_parser
    loads them once for loader-contributed flags; a second load would
    collide with the SDK's process-global tool registry). When absent -
    programmatic calls - the registry is built here."""
    cfg_path = Path(cfg_path)
    cfg = load_config(cfg_path)
    base = cfg_path.resolve().parent
    check_orchestrator_install(Path.cwd())
    scan_env_files(Path.cwd())
    check_tooling_integrity(cfg, cfg_path, base, Path.cwd())

    if registry is None:
        registry = default_registry()
        load_extensions(cfg.extensions, registry,
                        extensions_dir=cfg.extensions_dir,
                        workspace=Path.cwd())

    _validate_agents(cfg, base / cfg.agents_dir)
    _validate_actions(cfg, registry)
    resolved_seccomp_profile(cfg)  # fails loud on an unknown bundled:<name>
    for step in cfg.steps:
        validate_brief_inputs(Path.cwd(), step)

    loader, docs, subject, _doc_sources = resolve_docs(cfg, base, registry, args)
    image, memory, cpus = _resolved_image(cfg)
    state_dir = isolation_state_dir(cfg)

    _print_summary(cfg_path, cfg, subject, loader, docs, image, memory, cpus,
                   state_dir)
    return 0
