"""Bundled Docker seccomp profiles (023).

Docker's default seccomp profile blocks the user-namespace syscalls Chromium's
namespace sandbox needs, which makes ``chromium.launch(chromiumSandbox=True)``
die at the zygote under ``--cap-drop ALL``. These profiles are Docker's default
profile with an allowlist for those userns syscalls; pass one as
``isolation.seccomp_profile`` to swap it in. The ``bundled:<name>`` sentinel
(025) resolves a profile by name so consumers declare intent instead of
rediscovering the packaged path.
"""

from __future__ import annotations

from importlib.resources import as_file, files

CHROMIUM_USERNS_NAME = "chromium-userns"
CHROMIUM_USERNS_PROFILE = "chromium-userns.json"

# ``bundled:<name>`` sentinel suffix -> packaged JSON filename. This is the
# single registry new bundled profiles slot into (025).
BUNDLED_PROFILES = {
    CHROMIUM_USERNS_NAME: CHROMIUM_USERNS_PROFILE,
}


def _profile_path(filename: str) -> str:
    """Host filesystem path to a packaged seccomp profile JSON."""
    traversable = files("agentic_dev_loop.engine.seccomp").joinpath(filename)
    with as_file(traversable) as path:
        return str(path)


def bundled_profile_path(name: str) -> str:
    """Host filesystem path to the named bundled seccomp profile.

    Raises ``ValueError`` listing every known ``bundled:<name>`` when ``name``
    is unknown, so a bad sentinel fails loud at load instead of at
    container-create time.
    """
    filename = BUNDLED_PROFILES.get(name)
    if filename is None:
        known = ", ".join(f"bundled:{n}" for n in sorted(BUNDLED_PROFILES))
        raise ValueError(
            f"unknown bundled seccomp profile {name!r} (known: {known})")
    return _profile_path(filename)


def chromium_userns_profile_path() -> str:
    """Host filesystem path to the bundled Chromium userns seccomp profile.

    Docker's ``--security-opt seccomp=<path>`` takes a host path, so the JSON
    must resolve to a real file. Wheels unpack to disk, so ``as_file`` returns
    the installed path without copying.
    """
    return bundled_profile_path(CHROMIUM_USERNS_NAME)


def resolve_seccomp_profile(value: str | None) -> str | None:
    """Expand a ``bundled:<name>`` sentinel to its real profile path (025).

    A raw host path passes through unchanged (Docker still validates it at
    container-create time, unchanged from 023). An unknown or empty bundled
    name fails loud with every known ``bundled:<name>``.
    """
    if value is None or not value.startswith("bundled:"):
        return value
    name = value[len("bundled:"):]
    if not name:
        known = ", ".join(f"bundled:{n}" for n in sorted(BUNDLED_PROFILES))
        raise ValueError(
            f"seccomp_profile 'bundled:' is missing a name (known: {known})")
    return bundled_profile_path(name)
