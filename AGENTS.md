# agentic-dev-loop - instructions for agents

## Default development mode - ASK BEFORE CODING

**The DEFAULT mode of development in this repo is the user driving the work -
NOT the agent writing code inline.**

Before writing, creating, or editing ANY code file (implementation OR tests),
ALWAYS ask the user first whether they want inline implementation. Never infer
permission from a mode switch, a planning conversation, or a completed plan
document. A plan being "ready" is not a request to implement it.

Permitted without asking: research, reading files, explaining, and writing
planning documents (e.g. `planning-docs/**`) when asked. Everything else -
including "red" TDD test files - requires an explicit yes first.

When the user invokes AI-DLC,
read and follow `.aidlc/aidlc-rules/aws-aidlc-rules/core-workflow.md` to start the workflow.

When presenting AI-DLC review/approval questions, do so inline in the chat -
override the AI-DLC rule that says "never ask questions in chat."
After the user answers each question,
update the corresponding plan document with the answer before presenting the next question.

## Slash commands

**When the user invokes a slash command** (e.g. `/agent-engine`, `/unit-status`), the FIRST and ONLY action is to read its definition from `.opencode/commands/<command>.md`. Never pattern-match the command name to any other flow, workflow, or phase - the command file is the single source of truth.

## GitHub URLs

**When the user gives a GitHub URL** (actions, PRs, issues, etc.), use `gh` CLI (`gh api`, `gh run view`, `gh pr view`, etc.) to fetch details - do **not** use `webfetch`. `webfetch` gets 404s on private repos and returns rendered HTML rather than structured data. `gh` CLI is authenticated and gives clean output.

**This does not license deployment-status checks.** Even with `gh` available, never use it to check deploy/CI status as part of debugging (see Debugging). Fetching details of a user-provided URL is the only sanctioned use.

## File search

**PREFER `ls` over `glob` for discovering files.** The `glob` tool has given false negatives (returns "No files found" when files exist). Use `ls` (or `ls -R` for recursive) to verify directory contents. Only use `glob` for pattern-matching across deep trees when `ls` would be impractical.

## Group session mode

This mode activates **only** when the facilitator explicitly starts a group session by saying "start group session" or "group session". Once active, batch responses from multiple participants sharing this OpenCode server:

1. **Non-DONE messages** - respond only with `ack`. Do not interpret or act on their content.
2. **DONE message** (case-insensitive, exact match) - treat all user messages since your last substantive response as compound input from distinct participants. Synthesize them into a single coherent response. Attribute points to participants if they identified themselves (e.g., `[Luke]: ...`).
3. **DONE with prefix** - e.g. `DONE. Summarize.` - process the compound input, then execute the instruction following DONE.
4. **Direct messages to you** - if a message starts with `HEY:` (case-insensitive), respond immediately without waiting for DONE.
5. **To end group mode** - the facilitator says "end group session". Resume normal single-prompt behavior.

## Error handling

**Silent fallbacks are absolutely forbidden.** If a required value (environment
variable, config, token, secret) is missing, raise loudly with a clear error
message naming the missing value. Never silently degrade to a less-functional
code path.

- Do not fall back to a default that silently changes behaviour (e.g. falling
  back to GITHUB_TOKEN when CONSTRUCTION_LOOP_TOKEN is required).
- Defaults are acceptable only when they produce equivalent behaviour, never
  degraded behaviour.
- If a secret/token is mandatory for an operation to work correctly, fail fast.

## Em dash ban

**The em dash character (U+2014) is forbidden anywhere in this repo**: in
source code, tests, docs, planning docs, commit messages, and any other
committed file. It has repeatedly produced Windows CI encoding failures
(the cp1252 vs UTF-8 class) and is never load-bearing. Use a comma, colon,
hyphen, or a restructured sentence instead. Everything committed is ASCII.

Enforced by `tests/test_repo_ascii.py` (every test run, CI included) and by
a pre-commit hook (`.pre-commit-config.yaml` via `tools/check_no_em_dash.py`,
active after `pre-commit install`).

## Consumer name ban

**NEVER mention a consumer by name anywhere in this repo, for any reason**:
code, tests, docs, planning docs, agent prompts, commit messages, tags,
branch names. Consumers are "the consumer" or "the consumer repo", never a
name. This rule applies to committed history too: a leaked name is scrubbed
out of history with a rebase (see planning-docs/026), not just edited at
the tip.

## Debugging

**Checking deployment status is absolutely forbidden.** Never run `gh run`,
`gh run view`, any CI/CD-status command, or any deployment-status check while
debugging - not to rule a hypothesis in, not to rule it out, not "just to be
thorough". Deploy state is never evidence about a bug, in either direction.
"The code may not be live yet" is never a hypothesis and never a check, even
after every other explanation is exhausted. Saying a scan, test, or log "may
have hit the old code" is lazy-infrastructure blame in disguise - forbidden
regardless of how stuck the debugging feels. Debug the code itself. If
deployment state ever genuinely matters, the user raises it; the agent does
not look for it.

**Language ban - zero exceptions.** Never write any sentence that asserts,
hedges, or asks about what is live in production: "may not be live yet",
"hit the old code", "that scan predates the deploy", "has the deploy
finished", or any variant. Deploy timing questions go the same way as
deploy-status checks - forbidden. If the agent needs fresh production
evidence, it asks for a scan, a log, or a response the user can observe -
never for deploy state.

**Lazy infrastructure blame is absolutely forbidden.** "Stale bundle", "cache
needs clearing", "not deployed", "try redeploying" and all variants are
**exceptional** explanations, not a debugging default. These things happen
rarely; reaching for them as the first hypothesis when something doesn't work
is lazy and wrong. Before ever suggesting one:

- Pull concrete evidence that it's true. If the evidence isn't there, the
  explanation isn't there. Debug the actual behaviour instead.

Saying "it might be a stale bundle" without that evidence is forbidden
regardless of how stuck the debugging feels.

**Blaming the user is absolutely forbidden.** The user is technically competent:
assume they have already discounted clipboard mishaps, typos, paste mistakes,
not-reloading-the-page, and every other self-inflicted cause before reporting a
bug. "User error", "wrong clipboard content", "didn't hard-refresh", "stale
deployment", "stale cache", "cosmic rays", "bad vibes" and every other
explanation that locates the fault outside the code are **never** the first
hypothesis - they are not hypotheses at all. The default and only starting
assumption when something fails is that the code is broken. Find the bug in the
code, reproduce it, and prove it with evidence; only then may non-code causes
be considered, and only with evidence. When a bug is found and fixed, own it
plainly - no caveats, no excuses, no re-blame.

**State only what you checked, and say which.** Before writing any claim about
what the system is doing, run the command that shows it - reproduce against the
live page, read the computed style, hit the endpoint. If you didn't check,
either check or don't write the sentence. Fluency is not evidence. Never assert
causation from co-occurrence; adjacent timing is not proof.

## Reverse engineering undocumented systems

Rules born from the 2026-09-29 Framer incident - a Delete button built on an invented store model severed 18 live component definitions.

**Prevent**

- **Never design destructive operations against an unvalidated model of an external system.** Before any mutation ships, record the *validated model* - each fact with the probe that established it - alongside the code that relies on it. If a fact was assumed rather than probed, it is not a fact.
- **Test fixtures must be built from real system dumps**, not from the developer's mental model. Fixtures encoding a fiction make wrong predicates pass their own tests.
- **Prove the undo path before enabling the do path.** A delete ships after its restore is demonstrated working, not before.

**Detect**

- **Post-mutation verification = intent AND blast radius.** "Did X happen" is half the check. Before mutating, enumerate what references the thing being touched; after mutating, verify every one of those references still resolves. An all-clear that only checks intent is not an all-clear.

**Process**

- **No all-clear without a written verification checklist** - each item ticked with its evidence, not asserted from intent.
- **Long-running repair/migration sessions: state "do not reload / do not edit / do not close" checkpoints explicitly.** Never assume the other side knows a reload would be destructive.

## Development workflow

**Always use TDD (test-driven development) - red, green, refactor.**

1. **Red** - write a failing test that defines the expected behaviour. Run it to confirm it fails.
2. **Green** - write the minimum code to make the test pass. Run the test to confirm it passes.
3. **Refactor** - clean up the code while keeping tests green. Remove duplication, improve names, simplify.

Never write implementation code before a test exists to validate it. Never move on from a passing test without considering whether the code needs refactoring.

## Backward compatibility

**When a validation or schema change would reject existing stored data, you MUST give explicit instructions to delete/recreate the old data before deploying.** Never tighten a constraint that existing DB rows would fail without telling the user what to delete first.

## Mock policy

**Do not use mocks unless absolutely unavoidable.** Mocks are acceptable ONLY for
external network boundaries (API calls, LLM invocations, SDK classes that make
HTTP requests). Never mock file I/O, pure functions, or local utilities - use
temporary directories and real implementations instead.

- Permitted: mocking `LLM`, `Agent`, `Conversation` (SDK classes that require
  network)
- Forbidden: mocking functions that read files or return deterministic
  results (e.g. `resolve_unit_docs`, `parse_verdict`, `build_context`)

## SDK compat shims

Import-time monkeypatches in `src/agentic_dev_loop/llm.py` paper over OpenHands
SDK bugs. They go stale silently when the SDK changes - **`COMPAT-SHIMS.md`**
is the registry (each shim's removal criterion, target SDK version, pinning
test) and `tests/test_sdk_compat.py` guards both directions: the version guard
fails on an SDK upgrade outside the tested range, and the canary fails when
the SDK self-heals so the dead shim gets removed. Follow `COMPAT-SHIMS.md`'s
upgrade procedure before bumping `openhands-sdk`.
