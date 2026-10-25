# Unit 4 Deployment Architecture - Claude Dynamic Workflows driver

## Install flows

### macOS / Linux (posix)

```bash
# workflow-mode user (Claude Code only)
pip install agentic-dev-loop            # base: core + viz + workflow CLI
agentic-dev-loop-workflow install-skill # → ~/.claude/skills/agentic-dev-loop/

# full engine user (unchanged from today)
pip install agentic-dev-loop[engine]
agentic-dev-loop --version
```

### Windows (PowerShell)

```powershell
pip install agentic-dev-loop
agentic-dev-loop-workflow install-skill   # powershell entry point ships in the wheel
```

Both console scripts ship posix + powershell entry points (ID-1 requirement-first: Windows support mandatory).

## Workflow build flow (per use)

1. Dev invokes `/agentic-dev-loop` in Claude Code.
2. Claude follows SKILL.md → `python -m agentic_dev_loop.core.project <pipeline.yaml>` (or the console script).
3. Projection validates pipeline + prompt refs (fail loud on either).
4. `render_workflow_js` writes `.agentic-dev-loop/workflow.js` (header: core version + pipeline hash).
5. Claude creates the CC workflow from that file. Steps frozen.

## Run-time layout (workflow mode)

| Piece | Where | Notes |
| --- | --- | --- |
| Workflow steps | Claude Code | Deterministic router in generated JS |
| Agent subagents | CC env, re-read `.agent.md` from workspace | Prompt reference (BR-5) |
| Command subagents | CC env | Exit code = verdict |
| Viz server | localhost, subagent-launched, free-port fallback | Ingest token → workflow state |
| Commit | Docker-free shim, subagent-invoked | Clean-at-start gate (BR-8) |

## CI deployment

| Job | OSes | Purpose |
| --- | --- | --- |
| Existing engine job | ubuntu + windows | unchanged (unit/PBT/docker-marked) |
| NEW workflow-mode job (ID-4) | ubuntu + windows | install `[workflow]` only → project fixture → render → golden compare; proves no hidden engine imports |
| release.yml | tag-triggered | unchanged - wheel carries the skill payload |

## Migration mechanics (NFR-2 hard break)

- CHANGELOG + release notes carry the old→new import table (e.g. `agentic_dev_loop.config` → `agentic_dev_loop.core.config`, `agentic_dev_loop.extensions.aidlc` → `agentic_dev_loop.engine.extensions.aidlc` - exact paths fixed at Code Generation).
- This repo's own `agentic-dev-loop/pipeline.yaml` `extensions:` string updated in the same change.
- the consumer repo: documented upgrade note (pipeline.yaml extensions string + import updates).
- Explicit instruction to consumers: update before upgrading the package (alpha, breaking allowed pre-1.0).
