# planning-docs/design

Design artefacts for the viz UI redesign: Carbon Web Components re-skinned to
the brand, with a horizontal pipeline that re-flows to fit.

| Path | What it is |
|---|---|
| `viz-redesign-spec.md` | **Start here.** Normative spec: tokens, component mapping, layout algorithm, the behaviour that must not change, acceptance checks |
| `tokens.css` | Dark and light theme tokens (`--cds-*` overrides + `--adl-*`) |
| `pipeline-layout.js` | Reference layout engine: fewest rows that fit, wrap connectors, crossing-free back-edge routing |
| `prototype/index.html` | Runnable prototype. Open it in a browser straight from disk. Use the bottom-right **Prototype** panel (or `?scenario=running\|success\|failure\|reconnecting&topology=full\|short&theme=dark\|light`) to switch state; **Replay events** streams the scenario through the same event types `/events` emits. Resize the window to watch the pipeline re-flow |
| `reference/run-view.dc.html` | Source of the design-canvas mock (informative) |

For agents: read the spec, then `pipeline-layout.js`, and use the prototype as
the visual oracle. Where the spec and the prototype disagree, the spec wins.
