"""Engine-shipped egress presets (BR-9).

Preset data, never mechanism (D-2): one ``package-registries`` preset serves
every toolchain, so the restore step stays toolchain-agnostic. Adding a
toolchain adds a target here plus a cache declaration - no per-toolchain
egress code exists.
"""

from __future__ import annotations

from agentic_dev_loop.core.config import KNOWN_EGRESS_PRESETS

# The per-toolchain cache dirs (BR-9.3, BR-3.1): in-workspace paths that are
# bind-mounted rw into the export rather than copied. Cache declarations are
# preset data (D-2) - adding a toolchain adds a path here, never mechanism.
CACHE_DIRS: tuple[str, ...] = (
    ".venv-sandbox",
    "node_modules",
    "qa/node_modules",
    "qa/.browsers",
    ".nuget-packages",
    "target",
)


# The package registries every toolchain's locked restore touches (BR-9.1):
# pypi, npm, NuGet, cargo, plus the VCS/source-install hosts shared by all.
EGRESS_PRESETS: dict[str, tuple[str, ...]] = {
    "package-registries": (
        "pypi.org",
        "files.pythonhosted.org",
        "api.osv.dev",
        "registry.npmjs.org",
        "nodejs.org",
        "api.nuget.org",
        "crates.io",
        "static.crates.io",
        "github.com",
        "codeload.github.com",
        "objects.githubusercontent.com",
    ),
}


def expand_presets(preset_names: list[str]) -> tuple[str, ...]:
    """Expand preset references to their target strings (Q9). Unknown names
    fail loud (BR-8.4); each name is expanded exactly once, in declaration
    order, so the result is deterministic (PBT-03)."""
    targets: list[str] = []
    for name in preset_names:
        if name not in EGRESS_PRESETS:
            raise ValueError(
                f"unknown egress preset {name!r} (known: "
                f"{sorted(EGRESS_PRESETS)})")
        targets.extend(EGRESS_PRESETS[name])
    return tuple(targets)


# The preset names and the data must stay in lock-step (the config schema
# validates names against KNOWN_EGRESS_PRESETS).
assert set(EGRESS_PRESETS) == set(KNOWN_EGRESS_PRESETS)
