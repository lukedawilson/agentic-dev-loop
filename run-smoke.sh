#!/usr/bin/env bash
# Engine smoke test wrapper: the provider and the plan directory are
# independent. The provider argument injects the llm block into the plan's
# provider-free pipeline.yaml template (see tests/smoke/run_smoke.py).
#
# usage: ./run-smoke.sh <provider> <config-dir> [run_smoke.py args...]
#   provider    one of the PROVIDER_BLOCKS keys in tests/smoke/run_smoke.py
#               (currently openhands, copilot, claude); the wrapper derives
#               the list from that dict so it cannot drift from the script
#   config-dir  a smoke plan directory: pipeline.yaml (provider-free),
#               agents/dev.agent.md, optionally plan.md. This repo ships
#               tests/smoke/viz-provider and tests/smoke/viz-model.
#
# openhands preflight: OPENAI_API_KEY must be exported.
# copilot preflight:   token file at ~/.agentic-dev-loop/auth/copilot.json
#                      (agentic-dev-loop copilot-login) and the pinned
#                      model must be in the token's tier list.
# claude preflight:    token file at ~/.agentic-dev-loop/auth/claude.json
#                      (agentic-dev-loop claude-login).
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

usage() {
    echo "usage: $0 <provider> <config-dir> [run_smoke.py args...]" >&2
    exit 2
}

[ "$#" -ge 2 ] || usage

provider="$1"
config_dir="$2"
shift 2

python_bin="$repo_root/.venv/bin/python"
if [ ! -x "$python_bin" ]; then
    echo "run-smoke.sh: repo venv not found at $python_bin - run ./setup-venv.sh first" >&2
    exit 2
fi

runner="$repo_root/tests/smoke/run_smoke.py"

# Valid providers come from the runner's PROVIDER_BLOCKS (single source of
# truth), so the wrapper can never reject a provider the runner accepts.
valid_providers="$(cd "$repo_root/tests/smoke" && "$python_bin" -c 'import sys; sys.path.insert(0, "."); from run_smoke import PROVIDER_BLOCKS; print(" ".join(sorted(PROVIDER_BLOCKS)))')"

case " $valid_providers " in
    *" $provider "*) ;;
    *)
        echo "run-smoke.sh: unknown provider '$provider' (expected one of: $valid_providers)" >&2
        exit 2
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

exec "$python_bin" "$runner" "$config_dir" --provider "$provider" "$@"
