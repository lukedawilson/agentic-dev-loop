---
description: Set up or repair the agentic-dev-loop pipeline's .venv. Use when running `agentic-dev-loop` fails with "No such file or directory" for .venv/bin/agentic-dev-loop, or when pip falls back to building Rust (maturin) wheels.
---

Set up the repo's Python virtualenv so the engine's local commands run:

!`./setup-venv.sh`

The script is idempotent and handles the environment-specific trap: on this
machine a Termux userland (Android-targeting Python) shares a PATH with the
Debian proot system (`python3` → Debian, but `pip` → Termux). Building the
venv with the Android Python makes pip fall back to source builds (Rust/maturin)
for compiled packages like `pydantic-core` and `litellm`; the script selects the
non-Android interpreter and uses the venv's own pip.

After it runs, report:
- which Python interpreter was selected, and any Android/Termux interpreters skipped
- whether `python3-venv` / `python3-pip` had to be apt-installed
- whether the `.venv` was rebuilt
- the smoke-check result (`.venv/bin/agentic-dev-loop` present)

Then confirm the LLM secret is in place for the pipeline. A run reads the
process environment by default; `--env FILE` loads a host env file that must
live outside the workspace. Never create a `.env` file inside the repo (the
engine refuses a run when the workspace contains one). If `OPENAI_API_KEY`
(or whatever the pipeline's `llm.api_key_env` names) is missing, ask the user
for it and verify with:

```bash
AGENTIC_SECRETS_FILE="$HOME/.config/agentic-dev-loop.env"
printf 'OPENAI_API_KEY=%s\n' "<key>" > "$AGENTIC_SECRETS_FILE"
.venv/bin/python -c "import os; os.environ.update(dict(line.strip().split('=', 1) for line in open('$AGENTIC_SECRETS_FILE') if line.strip() and not line.startswith('#'))); from agentic_dev_loop.core.config import load_config; from agentic_dev_loop.engine.llm import build_llm; build_llm(load_config('agentic-dev-loop/pipeline.yaml').llm); print('LLM OK')"
```

Do not run the full pipeline (it makes live LLM calls) unless the user asks.
