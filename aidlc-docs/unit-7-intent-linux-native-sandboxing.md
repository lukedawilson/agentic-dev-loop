# Unit 7 Intent (draft) - Linux-Native Sandboxing + Egress Protection

> **Status: INTENT ONLY** - split from the combined unit-6 intent by user directive 2026-10-25. No code written, no AI-DLC workflow started for this unit, no decisions committed. Revisit when unit-6 closes.

## Raw intent (user, 2026-10-25, verbatim)

- "Linux-native sandboxing (no docker)"
- "Linux-native egress protection"

## Split rationale (user, 2026-10-25, verbatim)

- "The third point (simple deployment) is probably a separate unit, that comes before the linux native unit (we need it to test the linux native stuff since I have a mac)"

## Relationship to existing units

- **unit-6 (cloud deployment):** comes FIRST and is this unit's test environment - the author develops on a Mac, so the cloud Linux box is where the native path is built and exercised.
- **unit-3 FD-2 (answer B):** Docker on macOS/Windows + native namespace isolation on Linux (bubblewrap/Landlock/seccomp). The native backend was deferred at unit-3 close (2026-10-19: "native bwrap/Landlock path dropped, Sandbox surface retained as extension seam"). This unit is where that backend lands.
- **unit-5 Q1/NFR-2 amendment (2026-10-25):** kernel-level egress enforcement was dropped from unit-5 and deferred to the future native Linux backend. That promise points here: the engine's native path carries kernel-level egress protection (the unit-5 egress proxy stays the macOS/Windows mechanism).
- **unit-5 residual R-2:** proxy enforcement is cooperation-dependent ("best we can do with Docker"). The native Linux path closes that residual for Linux hosts.
- **unit-5 config surface:** per-step mounts, egress allowlists, .git blindness and sync-back carry over unchanged - only the enforcement point (kernel vs proxy) differs.

## What the user wants (initial reading, to be refined at Functional Design)

1. **Linux-native sandboxing** - run agent/command steps without Docker on Linux: bubblewrap/Landlock/seccomp (unit-3 FD-2b answer A: host toolchain inside namespaces, tmpfs scratch, rw bind of workspace, no image).
2. **Linux-native egress protection** - kernel-level egress enforcement (Landlock network rules or equivalent), replacing the proxy on Linux; the proxy remains for macOS/Windows.

## Open questions (revisit at Functional Design)

1. Backend selection: does the native path replace Docker on Linux entirely (unit-3 FD-2c answer A: native-only, fail fast) or coexist behind the Sandbox surface with explicit selection?
2. Egress enforcement mechanism on the native path: Landlock network rules vs nftables-in-netns (both are now possible without Docker; unit-5 explored both and dropped them for the Docker path).
3. Mount granularity port: unit-5's rw/ro/absent mounts and .git blindness are Docker-bind-mount mechanics; what is the native equivalent (bwrap bind + Landlock FS rules)?

## Sources

- Unit-5 NFR approval session (this session, 2026-10-25)
- unit-6 combined intent (split by user directive 2026-10-25; see unit-6-intent-cloud-deployment.md)
- unit-3 functional design (FD-2, FD-2b, FD-2c - native backend decisions)
- unit-5 functional design Q1 amendment + NFR-2/NFR-6 supersede notes (proxy everywhere for now)
- `src/agentic_dev_loop/engine/isolation.py` (Sandbox surface, DockerBackend)
