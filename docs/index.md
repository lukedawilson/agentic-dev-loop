# agentic-dev-loop

Turn an LLM into a gated, sandboxed, resumable construction loop over your codebase
{: .adl-subtitle }

The [README](https://github.com/lukedawilson/agentic-dev-loop/blob/main/README.md)
carries the pitch, the key features, and a quickstart; the pages below are the
manual.

- [Architecture](architecture.md) - the five layers, one full run end to end,
  and the host-boundary security model
- [CLI](cli.md) - run flags, resuming, the live view, reports, container
  management, and onboarding commands
- [pipeline.yaml reference](pipeline.md) - the complete configuration
  reference: steps, isolation, secrets, networking, dependency policy
- [LLM providers](llm-providers.md) - OpenAI-compatible endpoints and the
  GitHub Copilot subscription
- [Authoring a loop](authoring.md) - from `init` to a committed pipeline:
  agent files, stage lineup, adaptation checklist
- [Setup and troubleshooting](setup.md) - Docker engines, the dev image,
  caching, error codes
- [Browser sandbox](browser-sandbox.md) - running a browser inside the
  sandbox
- [Known issues](known-issues.md) - container lifecycle edge cases
- [Development environment](development.md) - hacking on the engine itself
