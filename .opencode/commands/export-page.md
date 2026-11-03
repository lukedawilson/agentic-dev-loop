---
description: Export a Claude Design HTML export to Figma. Validates Figma primitives first. Usage: /export-page [path/to/index.html]
---

Export a Claude Design HTML export to the shared Figma file.

## Resolve the input

If `$ARGUMENTS` is empty, ask the user for the path to the HTML file using
the `question` tool. Read the HTML file fully.

## Validate Figma primitives (REQUIRED - do NOT skip)

Before any Figma work, evaluate every CSS class in the HTML against the
frontend-development skill's mapping tables:

1. Parse the HTML and extract every class name used.
2. Read `.opencode/skills/frontend-development/SKILL.md`.
3. For each class, determine whether it is a genuine Figma mapping:
   - Is it a `t-`/`c-`/`i-`/`l-` class documented in the SKILL.md tables?
   - Is it a Carbon component class (e.g. `cds--*`) that maps to a Carbon
     Design Kit component?
   - If neither, it is a violation.
4. Print a violation report with this format:

```
## Figma compliance report

### Compliant classes
| Class | Figma mapping |
|-------|---------------|
| .t-heading-04 | Text Style: Fluid Heading 04 |
| .l-flex | Auto Layout -> Horizontal |
| ...

### Violations
| Class | Reason |
|-------|--------|
| .my-custom-margin | No Figma control or Carbon token - custom CSS |
| .arbitrary-blue | Literal hex - use c-* class with a Carbon token |
| ...
```

If violations exist, present them and ask the user whether to continue
(noting violations) or abort.

## Export to Figma

This requires Claude CLI's Figma MCP connection.

### Capture

1. Serve the HTML file with a simple static server (e.g. `npx serve` or
   Python's `http.server`).
2. Use Claude CLI's Figma MCP `generate_figma_design` tool to capture the
   page into the shared Figma file (URL from `.opencode/figma-file.txt`).
3. Capture at 1280px width.
4. Stop the static server.

### Report

Print a summary of what was captured and which violations (if any) remain
in the Figma output. This is the designer's checklist for manual cleanup.

## After export

Read `.opencode/skills/frontend-development/SKILL.md` and apply its binding
rules to the current context. All subsequent frontend work in this session
must follow those rules.
