# Design workflow

## TL;DR

| Command | Where | Description |
|---------|-------|-------------|
| `/import-page <Page> --figma <URL>` | OpenCode | Pulls a Lit view into a Figma canvas as editable, Auto-Layout-faithful frames |
| `/export-page [path/to/index.html]` | OpenCode | Takes a Claude Design HTML export and brings it into Figma, validating Figma primitives first |
| `/import-opencode-tools` | Claude Code | Syncs the latest OpenCode commands and skills |
| `load-frontend-rules` | OpenCode | Reads the three core frontend files (SKILL.md, FigmaPrimitives.scss, lint) into context, activating all binding rules plus linter-awareness |

`/import-page --figma` now builds at **both** 1280px (desktop) and 375px (mobile) viewports. The mobile view is purely responsive - same markup, real Carbon component behaviour at the narrow width.

---

Two-way bridge between the codebase's Lit views and the designer's Figma canvas, built on the [Figma MCP server](https://developers.figma.com/docs/figma-mcp-server/). Supports both **OpenCode** and **Claude Code / Claude Design** as the AI agent driving the workflow.

## Setup

1. Figma MCP access is provided through Claude CLI's built-in Figma connection. The `opencode.jsonc` Figma config is reserved for OpenCode's own Figma bridge.

2. The `--figma` flag on `/import-page` takes a required Figma file URL - it does not auto-create or discover files.

## Dual-agent architecture

```
.opencode/                    .claude/
|-- commands/                 |-- CLAUDE.md
|-- skills/                   |-- commands/
|-- plans/                    |   `-- import-opencode-tools.md
|                             `-- skills/
|
|   import-opencode-tools.sh
|   +------------------------------+
`-->|  .claude-local/ (gitignored) |
    |  |-- commands/               |
    |  `-- skills/                 |
    +------------------------------+
```

| Directory | Purpose | Committed? | Agent |
|-----------|---------|------------|-------|
| `.opencode/commands/` | Source of truth - all commands | Yes | OpenCode |
| `.opencode/skills/` | Source of truth - all skills | Yes | OpenCode |
| `.claude/` | Claude Code project config (CLAUDE.md + import command) | Yes | Claude Code |
| `.claude-local/` | Exported copy of OpenCode commands and skills | No (`.gitignore`) | Claude Code |

**Workflow**: Edit commands and skills in `.opencode/` (the source of truth), then run `bash tools/import-opencode-tools.sh` to sync them into `.claude-local/`. Claude Code picks up commands and skills from `.claude-local/` automatically.

### Concept mapping

| OpenCode concept | Claude Code equivalent |
|------------------|------------------------|
| Command (`.md` with YAML frontmatter) | Slash command (`.md` with YAML frontmatter) |
| Skill (`SKILL.md` with `name`, `description`) | Skill (`SKILL.md` with `name`, `description`) |
| `opencode.jsonc` instructions | `.claude/CLAUDE.md` |
| `!`exec`` syntax | Natural-language: `Run `exec`` |

The import script (`tools/import-opencode-tools.sh`) handles these transformations automatically:
- Strips `!`exec`` syntax -> `Run `exec`` (Claude Code instructions)
- Removes OpenCode-specific `metadata:` blocks from skill frontmatter

### Setting up Claude Code

1. Run the export: `bash tools/import-opencode-tools.sh`
2. Claude Code will discover commands and skills in `.claude-local/`
3. To use the Figma MCP bridge from Claude Code, ensure Claude CLI's Figma MCP connection is enabled

## Commands

### Code -> Figma

| Command | Agent | Purpose |
|---------|-------|---------|
| `/import-page` | OpenCode / Claude Code | Import a Lit page into the design session - export as standalone HTML or capture into the shared Figma file |
| `/export-vanilla-html` | OpenCode / Claude Code | Export a Lit view as standalone vanilla HTML (no Figma). Used internally by `/import-page --html`. |

### Claude Design -> Figma

| Command | Agent | Purpose |
|---------|-------|---------|
| `/export-page` | OpenCode / Claude Code | Take a Claude Design HTML export and bring it into Figma. Validates Figma primitives first. |
| `/validate-figma-primitives` | OpenCode / Claude Code | Evaluate all utility classes in `FigmaPrimitives.scss` against actual Figma behaviour. |

### `/import-page` - code -> design session

```
/import-page LandingPage --html [path]        # dump as standalone vanilla HTML (to console or file)
/import-page LandingPage --figma <URL>        # capture into the given Figma file (required)
/import-page LandingPage                      # asks whether --html or --figma
```

| Flag | Behaviour |
|------|-----------|
| `--html [path]` | Produces a self-contained vanilla HTML page (Carbon CDN components, inlined SCSS, Lit plumbing stripped). Writes to file if path given, otherwise dumps to console. |
| `--figma <URL>` | (Required URL) Starts the Vite dev server, navigates to the page's route, captures the live UI at 1280px into the given Figma file via Claude CLI's `generate_figma_design`. Also dumps the HTML design spec to console. |
| (none) | Asks the user which format. Never guesses. |

After either export, the [frontend-development skill](.opencode/skills/frontend-development/SKILL.md) rules are loaded into context. All subsequent frontend work in the session follows those rules.

### `/export-page` - Claude Design -> Figma

Take a Claude Design HTML export and bring it into Figma.

```
/export-page                             # asks for the file path
/export-page design-docs/demo/index.html
```

| Step | Action |
|------|--------|
| 1. Validate | Parses the HTML and checks every CSS class against the FigmaPrimitives mapping tables in the [frontend-development skill](.opencode/skills/frontend-development/SKILL.md). Prints a compliance report listing compliant classes and violations. |
| 2. Review | If violations exist, asks the user whether to continue or abort. |
| 3. Capture | Serves the HTML with a static server, captures it into the shared Figma file at 1280px via `generate_figma_design`. |
| 4. Report | Prints a summary of what was captured and which violations remain. |

### `load-frontend-rules` - activate binding rules + linter awareness

Read the three core frontend files into the session context to activate every binding rule from the [frontend-development skill](.opencode/skills/frontend-development/SKILL.md):

| File | Purpose |
|------|---------|
| `.opencode/skills/frontend-development/SKILL.md` | Binding rules, naming convention, genuine mapping test, class reference tables, sanctioned element register |
| `frontend/src/FigmaPrimitives.scss` | Implementation of every documented utility class and component class |
| `frontend/scripts/lint-figma-primitives.mjs` | Linter enforcing SCSS <-> SKILL.md sync (13 checks) |

Produces full awareness of what the linter will catch (duplicate claims, phantom classes, fictional tokens, unsanctioned element rules, etc.), reducing lint failures on first pass.

After loading, all subsequent frontend work must follow every binding rule: no custom CSS, no inline styles, no element selectors outside the register, new classes must pass the genuine mapping test and be added to SKILL.md tables, and `npm run lint` in `frontend/` must be run after any SCSS or SKILL.md change.

## Shared Figma file

| Detail | Value |
|--------|-------|
| Name | `yosk-sitebuilder - design bridge` |
| Location | Your Figma drafts |
| URL stored in | `.opencode/figma-file.txt` (gitignored) |
| Created by | `create_new_file` MCP tool on first `--figma` export |

## Figma MCP tools used

| Tool | Used by | Purpose |
|------|---------|---------|
| `create_new_file` | Both agents | Create the shared Figma Design file if it doesn't exist |
| `generate_figma_design` | Both agents | Capture live UI into the shared Figma file |

## FigmaPrimitives compliance

All design work in this repo follows the [frontend-development skill](.opencode/skills/frontend-development/SKILL.md) binding rules:

- No inline styles, no custom CSS
- Carbon web components only
- All custom classes must follow the `t-`/`c-`/`i-`/`l-` naming convention mapped to real Figma Carbon Design Kit controls
- Every utility class must be documented in the SKILL.md mapping tables
- The `lint-figma-primitives` script enforces SCSS <-> SKILL.md sync in CI (`npm run lint` in `frontend/`)
