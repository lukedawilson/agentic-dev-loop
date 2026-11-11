# Contributing

- **Read [AGENTS.md](AGENTS.md) first** - it is the instruction set for both
  human and agent contributors.
- Development is structured by **AI-DLC** - the lifecycle workflow in
  `.aidlc/aidlc-rules/aws-aidlc-rules/` with working docs in `aidlc-docs/`.
  The bundled document loader autodetects the current unit via
  `--aidlc-unit`, or loads any plan with `--plan`.
- OpenCode commands over the workflow: `/unit-status`, `/epic-status`,
  `/ticket-details`, `/update-subtask`, `/apply-aidlc-patches`, and more.
- Working extension samples (actions, tools, document loaders) ship as
  `tests/sample_ext.py`.
- The local development environment is documented in
  [docs/development.md](docs/development.md).
