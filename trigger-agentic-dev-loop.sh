#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"

cd "$ROOT"

# The engine must run from an OUT-OF-WORKSPACE install (020): the sandbox
# mounts exactly this repo, so an in-repo `.venv` would be agent-writable and
# the next run would execute agent-modified engine code in the host. pipx
# installs into `~/.local/pipx/venvs` - outside the mount set, so the startup
# self-check accepts it:
#   pipx install agentic-dev-loop   # or `pipx install .` for a local snapshot
# An existing install is upgraded on EVERY run (below) - the engine evolves
# fast and a stale install rejects newer pipeline configs at load time
# (e.g. `deps_policy` in v1.0.0a11 rejected by an a10 install).
#
# The pipeline reads the LLM key from the HOST environment (api_key_env:).
# Do not create a `.env` file here - the engine refuses a run when the
# workspace contains `.env*` (019); export the key instead:
#   export OPENAI_API_KEY=...

if ! command -v agentic-dev-loop >/dev/null 2>&1; then
  echo "error: agentic-dev-loop is not on PATH" >&2
  echo "       install it outside the workspace with pipx and re-run:" >&2
  echo "         pipx install agentic-dev-loop" >&2
  exit 1
fi

# Keep the pipx-managed install current. Two flags are load-bearing:
#   --cooldown 0
#     Defeat pipx's internal "already at latest" short-circuit - without
#     it pipx can skip the installer entirely and report a stale install
#     as current (observed: a11 live, trigger still ran an a10 engine).
#   --pip-args="--prerelease=allow"
#     EVERY engine release is a prerelease (a1..aN). An unpinned uv
#     resolution excludes prereleases by default and finds "no solution",
#     which surfaces as the lying "already at latest" or a failed
#     reinstall. Allow prereleases explicitly.
# When the latest release is already installed, upgrade is a fast no-op.
# Fail fast on upgrade failure - running the loop with a stale engine is
# worse than not running it.
if ! pipx upgrade --cooldown 0 --pip-args="--prerelease=allow" agentic-dev-loop; then
  echo "error: 'pipx upgrade --cooldown 0 --pip-args=--prerelease=allow agentic-dev-loop' failed -" >&2
  echo "       the engine install may not be pipx-managed, or the index is unreachable" >&2
  exit 1
fi

# Document loader selection - exactly one of:
#   --plan <path>        one plan doc as the run's full context
#   --aidlc-unit <slug>  AI-DLC unit docs
#   --resume <thread-id> re-enter a checkpointed run (docs come from the
#                        checkpoint; the engine's --resume flag)
# The engine refuses flags from two loaders in one invocation ("Flags from
# multiple document loaders in one invocation"), so the trigger enforces
# exclusivity up front and forwards the chosen flag. The positional `unit-*`
# first argument remains as shorthand for --aidlc-unit:
#   ./trigger-agentic-dev-loop.sh unit-3 --dry-run
PLAN=""
UNIT=""
RESUME=""
if [[ "${1:-}" == unit-* ]]; then
    UNIT="$1"
    shift
fi
REMAINING=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        --plan)
            [[ $# -ge 2 ]] || { echo "error: --plan requires a path" >&2; exit 1; }
            PLAN="$2"; shift 2
            ;;
        --plan=*) PLAN="${1#--plan=}"; shift ;;
        --aidlc-unit)
            [[ $# -ge 2 ]] || { echo "error: --aidlc-unit requires a slug" >&2; exit 1; }
            UNIT="$2"; shift 2
            ;;
        --aidlc-unit=*) UNIT="${1#--aidlc-unit=}"; shift ;;
        --resume)
            [[ $# -ge 2 ]] || { echo "error: --resume requires a thread id" >&2; exit 1; }
            RESUME="$2"; shift 2
            ;;
        --resume=*) RESUME="${1#--resume=}"; shift ;;
        *) REMAINING+=("$1"); shift ;;
    esac
done
MODES=0
[[ -n "$PLAN" ]] && MODES=$((MODES + 1))
[[ -n "$UNIT" ]] && MODES=$((MODES + 1))
[[ -n "$RESUME" ]] && MODES=$((MODES + 1))
if [[ $MODES -gt 1 ]]; then
    echo "error: pass exactly one of --plan <path>, --aidlc-unit <slug>, or --resume <thread-id>" >&2
    exit 1
fi
if [[ $MODES -eq 0 ]]; then
    echo "error: pass exactly one of --plan <path>, --aidlc-unit <slug>, or --resume <thread-id> (e.g. --aidlc-unit unit-3)" >&2
    exit 1
fi
if [[ -n "$PLAN" ]]; then
    exec agentic-dev-loop "$ROOT/agentic-dev-loop/pipeline.yaml" --plan "$PLAN" "${REMAINING[@]+"${REMAINING[@]}"}"
fi
if [[ -n "$UNIT" ]]; then
    exec agentic-dev-loop "$ROOT/agentic-dev-loop/pipeline.yaml" --aidlc-unit "$UNIT" "${REMAINING[@]+"${REMAINING[@]}"}"
fi
exec agentic-dev-loop "$ROOT/agentic-dev-loop/pipeline.yaml" --resume "$RESUME" "${REMAINING[@]+"${REMAINING[@]}"}"
