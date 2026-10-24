"""Bundled Docker seccomp profiles (023).

Docker's default seccomp profile blocks the user-namespace syscalls Chromium's
namespace sandbox needs, which makes ``chromium.launch(chromiumSandbox=True)``
die at the zygote under ``--cap-drop ALL``. These profiles are Docker's default
profile with an allowlist for those userns syscalls; pass one as
``isolation.seccomp_profile`` to swap it in.
"""

from __future__ import annotations

from importlib.resources import as_file, files

CHROMIUM_USERNS_PROFILE = "chromium-userns.json"


def chromium_userns_profile_path() -> str:
    """Host filesystem path to the bundled Chromium userns seccomp profile.

    Docker's ``--security-opt seccomp=<path>`` takes a host path, so the JSON
    must resolve to a real file. Wheels unpack to disk, so ``as_file`` returns
    the installed path without copying.
    """
    traversable = files("agentic_dev_loop.engine.seccomp").joinpath(
        CHROMIUM_USERNS_PROFILE)
    with as_file(traversable) as path:
        return str(path)
