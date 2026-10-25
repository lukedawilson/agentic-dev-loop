# 026 - Commercial-name scrub: code-mode inventory

> **Status: READY (awaiting code-mode execution).** User directive 2026-10-25: this repo's committed files must not name the commercial consumer product. Docs scrub DONE (aidlc-docs/, planning-docs/, docs/ - 34 mentions, including 3 historical audit entries). This plan enumerates the remaining files, which require code mode: repo root, src/, tests/.

## Files and exact replacements

### Repo root

| File:line | Current | Replacement |
| --- | --- | --- |
| README.md:192 | `key: YOSK_R2_KEYS` (secrets-store example) | `key: R2_KEYS` |
| Dockerfile.dotnet:1 | `# Custom sandbox image for dotnet construction loops (e.g. yosk-cms).` | `# Custom sandbox image for dotnet construction loops (e.g. the consumer repo).` |

### src/ (docstrings/comments only - no behaviour change)

| File:line | Current text | Replacement |
| --- | --- | --- |
| src/agentic_dev_loop/engine/actions.py:3 | `ported unchanged from yosk` | `ported unchanged from the consumer` |
| src/agentic_dev_loop/engine/graph.py:98 | `yosk's `construct`/`resume_loop` equivalent` | `the consumer's `construct`/`resume_loop` equivalent` |
| src/agentic_dev_loop/engine/hostboundary.py:32 | `the dogfood pipeline and yosk use` | `the dogfood pipeline and the consumer repo use` |
| src/agentic_dev_loop/engine/stages.py:3 | `The yosk loop's hardcoded stages` | `The consumer loop's hardcoded stages` |
| src/agentic_dev_loop/engine/stages.py:71 | `yosk runs` | `the consumer runs` |
| src/agentic_dev_loop/engine/stages.py:243 | `(yosk always looped back to dev)` | `(the consumer always looped back to dev)` |
| src/agentic_dev_loop/engine/stages.py:552 | `yosk's dev node owned` | `the consumer's dev node owned` |

### tests/ (module docstrings, fixture YAML names, one test method name)

| File:line | Current | Replacement |
| --- | --- | --- |
| tests/test_actions.py:1 | `ported from yosk's TestKillListenersOnPort` | `ported from the consumer's TestKillListenersOnPort` |
| tests/test_agents.py:1 | `ported from yosk's TestLoadAgent` | `ported from the consumer's TestLoadAgent` |
| tests/test_config.py:9 | `The plan's yosk-equivalent reference pipeline` | `The plan's reference pipeline (consumer-equivalent)` |
| tests/test_config.py:16,89 | fixture `name: yosk-construction` + `assert cfg.name == "yosk-construction"` | `name: reference-construction` + matching assert |
| tests/test_graph.py:3 | `The yosk-equivalent YAML below` | `The consumer-equivalent YAML below` |
| tests/test_graph.py:25 | `name: yosk-construction` | `name: reference-construction` |
| tests/test_graph.py:104 | `the yosk-equivalent pipeline committed clean` | `the consumer-equivalent pipeline committed clean` |
| tests/test_graph.py:134 | `test_yosk_pipeline_node_set` | `test_reference_pipeline_node_set` |
| tests/test_stages.py:3 | `The yosk hardcoded stages` | `The consumer hardcoded stages` |
| tests/test_stages.py:81 | `yosk layout:` | `consumer layout:` |

## Notes

- All src changes are comments/docstrings; zero behaviour change. Test fixture renames are mechanical and assertion-matched (`test_config.py` asserts the fixture name, `test_graph.py` uses it).
- `src/**/__pycache__` and `tests/**/__pycache__` pyc files are untracked build artifacts (git-ignored) - no action; they regenerate.
- Phrasing is deliberate: "the consumer repo" for repo references, "the consumer loop" for loop provenance, "the consumer's" for possession. It must match the phrasing already used in the scrubbed planning docs, because several planning docs quote these docstrings (e.g. planning-docs/011 quotes stages.py:243's `_unclear_target` docstring).
- After edits: run the full suite (`pytest`, green before merge) and `git grep -i yosk` must return nothing on tracked files.
- audit.md keeps its append-only record; this scrub is logged there without naming the product.
