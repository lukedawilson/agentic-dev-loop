#!/usr/bin/env bash
# Engine smoke test wrapper: the provider and the plan directory are
# independent. The provider argument injects the llm block into the plan's
# provider-free pipeline.yaml template (see tests/smoke/run_smoke.py).
#
# usage: ./run-smoke.sh <provider> <config-dir> [run_smoke.py args...]
#   provider    openhands | copilot
#   config-dir  a smoke plan directory: pipeline.yaml (provider-free),
#               agents/dev.agent.md, optionally plan.md. This repo ships
#               tests/smoke/viz-keepalive.
#
# openhands preflight: OPENAI_API_KEY must be exported.
# copilot preflight:   token file at ~/.agentic-dev-loop/auth/copilot.json
#                      (agentic-dev-loop copilot-login) and the pinned
#                      model must be in the token's tier list.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

usage() {
    echo "usage: $0 <provider: openhands|copilot> <config-dir> [run_smoke.py args...]" >&2
    exit 2
}

[ "$#" -ge 2 ] || usage

provider="$1"
config_dir="$2"
shift 2

case "$provider" in
    openhands|copilot) ;;
    *)
        echo "run-smoke.sh: unknown provider '$provider' (expected openhands or copilot)" >&2
        usage
        ;;
esac

if [ ! -d "$config_dir" ]; then
    echo "run-smoke.sh: config directory not found: $config_dir" >&2
    exit 2
fi
if [ ! -f "$config_dir/pipeline.yaml" ]; then
    echo "run-smoke.sh: pipeline template not found: $config_dir/pipeline.yaml" >&2
    exit 2
fi

python_bin="$repo_root/.venv/bin/python"
if [ ! -x "$python_bin" ]; then
    echo "run-smoke.sh: repo venv not found at $python_bin - run ./setup-venv.sh first" >&2
    exit 2
fi

runner="$repo_root/tests/smoke/run_smoke.py"
exec "$python_bin" "$runner" "$config_dir" --provider "$provider" "$@"
