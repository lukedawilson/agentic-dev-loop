#!/usr/bin/env bash
#
# setup-venv.sh - create/repair the throwaway .venv for local `pytest` runs.
#
# The loop itself runs from a pipx install (OUTSIDE the workspace - 020);
# this .venv is dev-only tooling, never the host engine. The pipeline's
# `test` step provisions its own `.venv-sandbox` inside the sandbox.
#
# Idempotent. Safe to re-run after a breakage or on a fresh checkout.
#
# Why this script exists (the "Termux + Debian proot" lesson):
#
#   On this machine the base system is Debian 13 (arm64), but there is also a
#   Termux userland under /data/data/com.termux whose Python targets Android
#   (SOABI cpython-313-aarch64-linux-android). The two share a PATH:
#
#     `python3`  -> /usr/bin/python3          (Debian, glibc / manylinux)
#     `pip`      -> /data/data/.../bin/pip     (Termux, Android!)
#
#   Installing with the Android Python "works" until pip hits a package with a
#   compiled extension (pydantic-core, litellm>=1.92, hypothesis). Those have
#   no android wheels, so pip falls back to building from source, which needs a
#   Rust toolchain (maturin) that isn't there. Result: a wall of
#   "Target triple not supported by rustup" errors.
#
#   The fix is to always build the venv with a *non-Android* Python (Debian's),
#   which resolves the same packages from manylinux aarch64 wheels. This script
#   selects that interpreter, never touches the bare `pip` on PATH, and uses
#   the venv's own pip thereafter.
#
#   A second, independent requirement: tmux. The OpenHands agent terminal
#   checks for tmux; without it the SDK falls back to a subprocess-based
#   terminal that intermittently drops output and loses its working directory,
#   breaking the dev agent's loop (empty responses -> endless retries). This
#   script installs tmux via apt when it is missing.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

VENV="$ROOT/.venv"

# ---------------------------------------------------------------------------
# 1. Pick a non-Android Python interpreter.
# ---------------------------------------------------------------------------
is_android_py() { # exit 0 if $1 is an Android (embedded/Termux) interpreter
  "$1" -c 'import sysconfig, sys; sys.exit(0 if "android" in sysconfig.get_platform() else 1)' 2>/dev/null
}

pick_python() {
  local p
  for p in "${PYTHON:-}" python3 /usr/bin/python3 python; do
    [ -n "$p" ] || continue
    command -v "$p" >/dev/null 2>&1 || continue
    if is_android_py "$p"; then
      echo "skipping Android Python: $p" >&2
      continue
    fi
    printf '%s\n' "$p"
    return 0
  done
  return 1
}

PY="$(pick_python)" || {
  echo "error: no non-Android Python found on PATH" >&2
  exit 1
}
echo "using Python: $PY"

# ---------------------------------------------------------------------------
# 2. Ensure tmux is installed - required by the OpenHands agent terminal.
# ---------------------------------------------------------------------------
if ! command -v tmux >/dev/null 2>&1; then
  echo "tmux missing - installing"
  if command -v apt-get >/dev/null 2>&1; then
    apt-get update
    apt-get install -y tmux
  else
    echo "error: tmux is required but not installed, and no apt-get available" >&2
    echo "       install tmux manually (e.g. 'brew install tmux') and re-run" >&2
    exit 1
  fi
fi

# ---------------------------------------------------------------------------
# 3. Ensure venv + pip are available (Debian ships them as separate packages).
# ---------------------------------------------------------------------------
if ! "$PY" -c 'import ensurepip' >/dev/null 2>&1; then
  echo "ensurepip missing - installing python3-venv + python3-pip"
  if command -v apt-get >/dev/null 2>&1; then
    apt-get update
    apt-get install -y python3-venv python3-pip
  else
    echo "error: ensurepip missing and no apt-get available" >&2
    echo "       install python3-venv and python3-pip for $PY manually" >&2
    exit 1
  fi
fi

# ---------------------------------------------------------------------------
# 4. Rebuild the venv if it is missing or was built with an Android Python.
# ---------------------------------------------------------------------------
if [ -x "$VENV/bin/python" ] && is_android_py "$VENV/bin/python"; then
  echo "rebuilding $VENV (built with an Android Python)"
  rm -rf "$VENV"
fi
if [ ! -x "$VENV/bin/python" ]; then
  "$PY" -m venv "$VENV"
fi

# ---------------------------------------------------------------------------
# 5. Install the package (non-editable) with dev extras.
#    Dev extras include pytest + hypothesis, which local runs invoke via
#    `.venv/bin/python -m pytest tests/ -q`.
# ---------------------------------------------------------------------------
"$VENV/bin/pip" install ".[dev]"

# ---------------------------------------------------------------------------
# 6. Smoke check that the test runner works.
# ---------------------------------------------------------------------------
if ! "$VENV/bin/python" -c 'import pytest, hypothesis' >/dev/null 2>&1; then
  echo "error: .venv/bin/python lacks pytest/hypothesis" >&2
  exit 1
fi

cat <<EOF

setup complete:
  .venv/bin/python  ->  $("$VENV/bin/python" -c 'import sysconfig; print(sysconfig.get_platform())')

next steps:
  .venv/bin/python -m pytest tests/ -q   # run the fast suite locally
  pipx install --pip-args="--pre" agentic-dev-loop   # install the loop engine OUTSIDE the workspace
  export OPENAI_API_KEY=...               # the pipeline fails fast without it
  agentic-dev-loop agentic-dev-loop/pipeline.yaml --dry-run
EOF
