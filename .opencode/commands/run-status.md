---
description: Show the current (latest) pipeline run in this workspace as a mermaid graph with the current step highlighted, plus a bullet summary. Usage: /run-status
---

Report the status of the latest agentic-dev-loop run in this workspace. Render a mermaid flowchart of the pipeline graph with the current step highlighted, and list a brief bullet summary below it.

## Step 1: collect the run state

Run the script below with the repo's venv (`/Users/luke/dev/work/dev-tooling/agentic-dev-loop/.venv/bin/python`). It loads the pipeline config, reuses the engine's own topology renderer, reads the latest checkpoint from the run-state database, and prints the highlighted mermaid plus a summary. Pipe it via a heredoc; do not modify it.

```python
import sqlite3, sys, msgpack
from pathlib import Path
from agentic_dev_loop.core.config import load_config
from agentic_dev_loop import viz

PIPELINE = "agentic-dev-loop/pipeline.yaml"
cfg = load_config(PIPELINE)
mermaid, node_ids = viz.topology_mermaid(cfg)
step_names = [s.name for s in cfg.steps]
db_path = Path(cfg.state_dir).expanduser().resolve() / "loop-checkpoints.sqlite"

if not db_path.exists():
    print("NO_RUN"); sys.exit(0)

db = sqlite3.connect(str(db_path)); db.row_factory = sqlite3.Row
best = None
for r in db.execute("SELECT thread_id, checkpoint_id, checkpoint FROM checkpoints"):
    cp = msgpack.unpackb(r["checkpoint"], raw=False)
    ts = cp.get("ts", "")
    if best is None or ts > best[0]:
        best = (ts, r["thread_id"], r["checkpoint_id"], cp)
ts, tid, cid, cp = best
cv = cp.get("channel_values", {}) or {}

def pending_nodes(writes):
    out = []
    for (tp,) in writes:
        last = tp.rsplit(", ", 1)[-1] if ", " in tp else tp
        if not last.startswith("~") and last != "__start__":
            out.append(last)
    return sorted(set(out))

writes = db.execute("SELECT task_path FROM writes WHERE thread_id=? AND checkpoint_id=?", (tid, cid)).fetchall()
pending = pending_nodes(writes)
outcome = cv.get("outcome"); failed = cv.get("failed")
failed_step = cv.get("failed_step"); failure_class = cv.get("failure_class")
verdicts = cv.get("step_verdicts") or {}

if outcome == "success":
    status, current, status_txt = "DONE", "success", "success"
elif failed:
    status, current, status_txt = "FAILED", (failed_step or "unknown"), "failed"
elif pending:
    status, current, status_txt = "RUNNING", pending[0], "in progress"
else:
    status, current, status_txt = "RUNNING", step_names[0], "in progress"

def q(n): return n if n.replace("_", "").isalnum() else f'"{n}"'

done = [s for s in step_names if s in verdicts]
if status == "DONE":
    done += [s for s in step_names if s not in verdicts]

lines = ["classDef current fill:#ffd43b,stroke:#333,stroke-width:2px;",
         "classDef done fill:#2f9e44,stroke:#2f9e44,color:#fff;"]
if current in node_ids:
    lines.append(f"class {q(current)} current;")
d = [q(n) for n in done if n in node_ids and n != current]
if d:
    lines.append("class " + ",".join(d) + " done;")
out_mermaid = mermaid.rstrip("\n") + "\n\t" + "\n\t".join(lines) + "\n"

def step_label(s):
    if s in verdicts:
        return str(verdicts[s])
    if s == current:
        return "in progress"
    return "done" if status == "DONE" else "pending"

print("===MERMAID===")
print(out_mermaid, end="")
print("===SUMMARY===")
print(f"- Status: {status_txt}")
print(f"- Current step: {current}")
print(f"- Subject: {cv.get('subject')}")
print(f"- Thread: {tid}")
print(f"- Attempt: {cv.get('attempt')} / {cv.get('max_attempts')}")
print(f"- Last checkpoint: {ts}")
print(f"- Steps: " + ", ".join(f"{s}={step_label(s)}" for s in step_names))
if failed:
    print(f"- Failed at: {failed_step}")
if failure_class:
    print(f"- Failure class: {failure_class}")
for n in (cv.get("notes") or [])[-3:]:
    print(f"- Note: {n}")
```

## Step 2: render the result

- If the script printed `NO_RUN`: report "No run has been recorded for this workspace yet" and stop.
- Otherwise, present the `===MERMAID===` section verbatim inside a ` ```mermaid ` fenced block (strip the `===MERMAID===` marker line).
- Below it, present the `===SUMMARY===` section verbatim as a bulleted list (strip the marker line). The `Steps:` line is a single bullet; do not reformat it.

## Errors

- If `.venv/bin/python` is missing or cannot import `agentic_dev_loop`, report that the repo venv is not installed and stop (do not fall back to another interpreter).
- If `agentic-dev-loop/pipeline.yaml` is missing, report that and stop.
