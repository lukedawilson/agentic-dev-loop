# Development environment

```bash
git clone git@github.com:lukedawilson/agentic-dev-loop.git
cd agentic-dev-loop
pipx install --pip-args="--pre" agentic-dev-loop # engine venv OUTSIDE the workspace (self-check)
export OPENAI_API_KEY=...                     # set your LLM key in the host env (no .env in the repo)
python -m pytest tests/ -q                    # fast suite (in-repo .venv is for tests only)
python -m pytest tests/ -q -m "not docker"    # skip engine-dependent tests
agentic-dev-loop agentic-dev-loop/pipeline.yaml --dry-run   # run the shipped pipeline
.venv-sandbox/bin/python tests/smoke/viz_demo.py  # viz demo - scripted replay, no pipeline needed
```

- `setup-venv.sh` builds the throwaway `.venv` for local `pytest` runs. The
  loop itself must run from the pipx install - never an in-workspace `.venv`,
  which the startup self-check refuses.
- Docker-marked integration tests need a local daemon; CI runs them on Linux.
- The shipped pipeline (`agentic-dev-loop/pipeline.yaml`) dogfoods the tool
  on its own repo - dev -> test -> review -> qa -> commit, with the engine's own
  three agent definitions in `sdk_agents/`.
- CI: `ci.yml` (unit + integration suite, ubuntu), `publish-image.yml`
  (multi-arch dev image, amd64 + arm64), `release.yml` (PyPI publishing).
- Pre-publish tooling can point `AGENTIC_DEV_LOOP_DEV_IMAGE` at a locally
  built dev image.

## Building the documentation site

The manual lives in `docs/` and is built with MkDocs Material:

```bash
pip install ".[docs]"
mkdocs serve    # live preview
mkdocs build --strict
```

The site deploys to GitHub Pages from `.github/workflows/docs.yml` on every
push that touches `docs/` or `mkdocs.yml`. Internal docs (`docs/specs/`,
`docs/incidents/`, and the standalone analyses) are excluded from the site
nav via `exclude_docs:` in `mkdocs.yml`.
