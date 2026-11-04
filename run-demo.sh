#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"

cd "$ROOT"

# Default: flood the attempt-1 dev node with 600 console lines so the
# sidebar's tailing (bottom-anchored scrolling, 500-line eviction with the
# "…N lines omitted" counter) is visible. Any explicit args override this.
if [ "$#" -eq 0 ]; then
    set -- --flood 600
fi

# The venv contains an installed (non-editable) wheel of this package, so
# plain imports would run the last published version. Put the working tree's
# src/ first on PYTHONPATH so the demo serves the code in this repo - the
# same shadowing tests/conftest.py does for the test suite.
PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}" \
    exec "$ROOT/.venv/bin/python" "$ROOT/viz_demo.py" "$@"
