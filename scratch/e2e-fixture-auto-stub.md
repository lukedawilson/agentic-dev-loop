# Scratch: e2e fixture auto-materializes pipeline-referenced docs

Status: planned fix, not yet implemented. Delete this file once the fix lands.

## Problem (CI run 40896140012)

Commit 7c07546 added 3 `additional_files` entries to the shipped example
pipeline (agentic-dev-loop/pipeline.yaml): `../planning-docs/038-*.md`,
`../CHANGELOG.md`, `../docs/known-issues.md`. CI broke in
`tests/test_e2e_fixtures.py` because the `consumer_repo` fixture copies the
shipped pipeline byte-identical into a synthetic consumer repo but only
hand-materializes 4 of the referenced sibling files. The engine's designed
fail-fast fired for every test that runs `main()`:

```
FileNotFoundError: additional_files entry not found: .../planning-docs/038-network-level-egress-enforcement.md
```

This is deterministic, not a flake. Precedent: CHANGELOG a34 hand-added the
ERD stub after the same break. Fix chosen by user: derive the stubs from the
copied pipeline.yaml itself so future additional_files edits can never break
CI again.

## Engine facts the fix must respect (verified)

- `resolve_docs` (src/agentic_dev_loop/engine/loaders.py:135-138): each
  `cfg.additional_files` entry is resolved as `pipeline_dir / rel` and must
  `is_file()`, else FileNotFoundError.
- `resolve_brief_inputs` (src/agentic_dev_loop/engine/stages.py:532-543,
  `_resolve_include_spec` at 512-529): `brief_inputs.include` entries resolve
  against `runtime.workspace`, which is `Path.cwd()` at startup
  (src/agentic_dev_loop/engine/graph.py:151, 160, 177). Literal entries must
  name an existing file; wildcard entries must match at least one file.
- Config shape (src/agentic_dev_loop/core/config.py:540-559):
  `PipelineConfig.additional_files: list[str]`, `steps: list[StepConfig]`
  with `.name` and `.brief_inputs.include: list[str]`. `load_config(path)`
  returns the parsed config (already used in test_e2e_fixtures.py:261).
- Fixture details (tests/test_e2e_fixtures.py:77-124): copies EXAMPLE via
  `shutil.copytree(..., ignore=shutil.ignore_patterns(".pr"))`, rewrites
  pipeline.yaml to inject `state_dir: .pr`, writes stubs, `monkeypatch.chdir(repo)`,
  then `git init` + `git add -A` + init commit. Stubs must be created before
  the git init so they ride into the init commit (parity with today).

## Planned fixture change (tests/test_e2e_fixtures.py)

Replace the four hand-written stub writes with a derivation helper:

```python
def _materialize_referenced_docs(pipeline_path: Path, workspace: Path) -> None:
    """Create a stub for every file the copied pipeline references, derived
    from the pipeline config itself (additional_files + the brief step's
    brief_inputs.include) so future pipeline doc additions can never break
    the fixture again. Replaces the hand-written stubs from a34. Literal
    include entries only: a wildcard cannot be stub-materialized, so it
    raises instead of silently degrading."""
    cfg = load_config(pipeline_path)
    targets: list[Path] = []
    for rel in cfg.additional_files:
        targets.append(pipeline_path.parent / rel)
    for step in cfg.steps:
        if step.brief_inputs is None:
            continue
        for spec in step.brief_inputs.include:
            if any(ch in spec for ch in "*?["):
                raise RuntimeError(
                    f"e2e fixture cannot materialize wildcard "
                    f"brief_inputs include: {spec!r}")
            targets.append(workspace / spec)
    for target in targets:
        if target.is_file():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"# {target.name}\n", encoding="utf-8")
```

In the `consumer_repo` fixture, after the `state_dir` rewrite and before
`git init`:

```python
    # Every file the shipped pipeline references (additional_files plus the
    # brief step's brief_inputs.include) must exist in the consumer copy or
    # the engine fails fast. Derive stubs from the copied pipeline.yaml so
    # future doc additions can never break the fixture again (a34 fixed the
    # ERD case by hand; this generalizes it).
    _materialize_referenced_docs(example_pipeline, repo)
```

Delete the four explicit writes (README.md, AGENTS.md,
coding-standards-ddd.yaml, aidlc-docs/domain-model-erd.md) and their
comments; the helper materializes all of them (content irrelevant to the
scripted agents; paths are what the fail-fast checks).

## Planned helper tests (TDD: red first)

1. Missing additional_files entry gets a stub:
   - tmp pipeline yaml with `additional_files: [../a.md, ../docs/b.md]`,
     `steps: [brief step with brief_inputs.include: ["rules.md"]]`, llm block.
   - `_materialize_referenced_docs(pipeline, workspace)` with a clean workspace.
   - Assert `(pipeline.parent / "../a.md").is_file()`,
     `(workspace / "rules.md").is_file()`, `docs/b.md` exists.
2. Existing files are left untouched (content preserved).
3. Wildcard include raises RuntimeError (no silent fallback).
4. Fixture-level regression: keep the existing TestHistoricalFixtures +
   TestCommandGate tests green; they are the guard that catches future
   additional_files drift.

## Verification

- Red: `pytest tests/test_e2e_fixtures.py -q` fails on current main with the
  FileNotFoundError above.
- Green after fix: same command passes; helper tests pass; full suite
  `pytest tests/ -q -m "not docker"` passes.
- This change is tests-only: no engine behavior changes.
