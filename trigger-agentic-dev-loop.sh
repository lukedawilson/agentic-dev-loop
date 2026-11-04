#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

PIPELINE="$ROOT/agentic-dev-loop/pipeline.yaml"

# Optional host environment file. The default lives outside the workspace so
# secrets never enter the repo; set AGENTIC_DEV_LOOP_ENV_FILE to override.
DEFAULT_ENV_FILE="${XDG_CONFIG_HOME:-$HOME/.config}/$(basename "$ROOT")/agentic-dev-loop.env"
ENV_FILE="${AGENTIC_DEV_LOOP_ENV_FILE:-$DEFAULT_ENV_FILE}"
if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

if ! command -v agentic-dev-loop >/dev/null 2>&1; then
  echo "error: agentic-dev-loop is not on PATH" >&2
  echo "       install it outside the workspace with pipx and re-run:" >&2
  echo "         pipx install agentic-dev-loop" >&2
  exit 1
fi

# doctor --fast is the per-run gate: hard failures abort, warnings continue.
# A failing gate is repaired exactly once by upgrading to the latest engine
# release (which also rewrites the project pin), then the run fails hard.
if ! agentic-dev-loop doctor --fast; then
  echo "doctor --fast failed - upgrading to the latest engine release" >&2
  if ! agentic-dev-loop upgrade; then
    echo "error: agentic-dev-loop upgrade failed" >&2
    exit 1
  fi
  if ! agentic-dev-loop doctor --fast; then
    echo "error: doctor --fast still fails after upgrading the engine" >&2
    exit 1
  fi
fi

# Document loader selection - exactly one of:
#   --plan <path>        one plan doc as the run's full context
#   --aidlc-unit <slug>  AI-DLC unit docs
#   --resume <thread-id> re-enter a checkpointed run
# A positional unit-* first argument remains shorthand for --aidlc-unit.
PLAN=""
UNIT=""
RESUME=""
REMAINING=()
if [[ "${1:-}" == unit-* ]]; then
  UNIT="$1"
  shift
fi
while [[ $# -gt 0 ]]; do
  case "$1" in
    --plan)
      [[ $# -ge 2 ]] || { echo "error: --plan requires a path" >&2; exit 1; }
      PLAN="$2"; shift 2 ;;
    --plan=*) PLAN="${1#--plan=}"; shift ;;
    --aidlc-unit)
      [[ $# -ge 2 ]] || { echo "error: --aidlc-unit requires a slug" >&2; exit 1; }
      UNIT="$2"; shift 2 ;;
    --aidlc-unit=*) UNIT="${1#--aidlc-unit=}"; shift ;;
    --resume)
      [[ $# -ge 2 ]] || { echo "error: --resume requires a thread id" >&2; exit 1; }
      RESUME="$2"; shift 2 ;;
    --resume=*) RESUME="${1#--resume=}"; shift ;;
    *) REMAINING+=("$1"); shift ;;
  esac
done
MODES=0
[[ -n "$PLAN" ]] && MODES=$((MODES + 1))
[[ -n "$UNIT" ]] && MODES=$((MODES + 1))
[[ -n "$RESUME" ]] && MODES=$((MODES + 1))
if [[ $MODES -ne 1 ]]; then
  echo "error: pass exactly one of --plan <path>, --aidlc-unit <slug>, or --resume <thread-id>" >&2
  exit 1
fi

ARGS=()
if [[ -n "$PLAN" ]]; then
  ARGS+=(--plan "$PLAN")
elif [[ -n "$UNIT" ]]; then
  ARGS+=(--aidlc-unit "$UNIT")
else
  ARGS+=(--resume "$RESUME")
fi
exec agentic-dev-loop "$PIPELINE" "${ARGS[@]}" "${REMAINING[@]+"${REMAINING[@]}"}"
