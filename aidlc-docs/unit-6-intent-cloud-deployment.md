# Unit 6 Intent (draft) - Cloud Deployment (remote loop + viz access)

> **Status: INTENT ONLY** - split from the combined unit-6 intent by user directive 2026-10-25. No code written, no AI-DLC workflow started for this unit, no decisions committed. Revisit when unit-5 closes.

## Raw intent (user, 2026-10-25, verbatim)

- "A simple way to deploy this all (e.g. to a Hetzner VPS or something like Render) and have the viz accessible to me (model would be build the plans locally, run the loop in the cloud)"

## Split rationale (user, 2026-10-25, verbatim)

- "The third point (simple deployment) is probably a separate unit, that comes before the linux native unit (we need it to test the linux native stuff since I have a mac)"

## Relationship to existing units

- **unit-7 (Linux-native sandboxing + egress protection):** this unit comes FIRST. The author develops on a Mac; the cloud Linux box is the natural environment to build and test the native Linux path. Unit 7's design assumptions (a Linux host with the engine installed) are exercised here first.
- **Viz (unit-3 scope, src/agentic_dev_loop/viz/):** cloud deployment makes viz remotely accessible to the author - the first hosted deployment of the loop, distinct from the local-first desktop model of units 1-5.
- **unit-4 withdrawn ingest-token design:** its token-authenticated ingest is a precedent for remote viz access.

## What the user wants (initial reading, to be refined at Functional Design)

1. A simple deployment path (Hetzner VPS, Render, or similar) where the loop runs remotely.
2. Viz accessible to the author from the local machine.
3. Working model: plans built locally, the loop runs in the cloud.

## Open questions (revisit at Functional Design)

1. Deployment shape: how much is engine code vs packaging/ops (systemd unit, container image for the cloud box, provisioning script, SSH vs HTTPS)?
2. Viz remote access: auth model, TLS, and how the local author reaches a remote loop (unit-4's withdrawn ingest-token design is a precedent).
3. Local vs cloud split: which parts stay local (plan building, repo state, secrets) and which move to the cloud (execution, viz, state_dir)?
4. Repo/state transfer: how does the local author's workspace reach the cloud loop (push via git, rsync, archive upload)?
5. Target platform breadth: one blessed platform (e.g. Hetzner Ubuntu) vs a portable story across several.

## Sources

- Unit-5 NFR approval session (this session, 2026-10-25)
- unit-6 combined intent (split by user directive 2026-10-25; see unit-7-intent-linux-native-sandboxing.md)
- `src/agentic_dev_loop/viz/` (viz server scope, unit-3)
