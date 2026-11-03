---
description: Import a Lit page view into the design session - export as Figma or vanilla HTML. Usage: /import-page LandingPage [--figma <URL>|--html [path]]
---

Import the frontend page `$ARGUMENTS` into the design session.

## Resolve the page and format

1. Parse `$ARGUMENTS` - extract the page name and optional `--figma <URL>` /
   `--html [path]` flag. `--figma` takes a required link to a Figma file.
   `--html` takes an optional file path; defaults to `design-docs/demo/index.html`.
2. If no flag is given, ask the user: "Export to Figma or vanilla HTML?" with
   the `question` tool. Do NOT guess.
3. Resolve the page: look in `frontend/src/views/` for the page name (accept
   with or without the `.ts` extension). Verify the file exists and the
   component is registered in `frontend/src/router.ts`. Read it fully.

## HTML export (`--html [path]`)

Execute the `export-vanilla-html` command's process exactly - produce a
standalone vanilla HTML page. The page must "just work" when opened via a
static server: Carbon web components via CDN, Lit plumbing stripped, compiled
SCSS inlined.

All pages live in a single file at `design-docs/demo/index.html` (the default).
The file uses hash-based screen switching (`.screen.is-on` pattern) for
navigation, matching the approach in `frontend/index.html`. When adding a new
page to an existing file, preserve all existing pages and add the new one as an
additional screen. If a path is given, write to that file instead.

Never dump HTML to the console - always write to a file.

## Figma export (`--figma <URL>`)

This path requires Claude CLI's Figma MCP connection ("claude.ai Figma",
https://mcp.figma.com/mcp). The editable build is done with its `use_figma`
tool, invoked via `claude -p` - it is not available in OpenCode's own tool
list, and there is no `generate_figma_design` tool (that name appears only
in prose/skill docs).

**NEVER create a screenshot.** The ENTIRE POINT of the Figma export is an
EDITABLE Figma file a designer can work on - real frames with Auto Layout,
real text nodes with type styles, real fills/strokes mapped from the design
system classes. A flat bitmap (screenshot, image fill, rasterized capture of
the page) is worthless for this and must never be produced, uploaded, or
placed into the Figma file - not even as a "reference" alongside the editable
build. Do not take screenshots with playwright, `get_screenshot`, or any
other means as part of this export (the only exception is the
`export-vanilla-html` smoke test, which never touches Figma). If the editable
path is unavailable, stop and ask the user instead of falling back to an
image.

### 1. Preflight: verify the Carbon kit in the target file

Before anything else, check the target Figma file (via `claude -p` with the
Figma MCP tools):

1. `get_libraries` on the fileKey - a Carbon library (e.g. "IBM Carbon
   Design System") must appear in the file's **added/enabled** libraries
   (not merely "available to add"). Also **warn if any other libraries are
   enabled** (e.g. Material 3, iOS kits) - foreign kits could interfere
   with the Carbon build (wrong components/styles getting matched); list
   them and recommend disabling.
2. `get_metadata` / `get_design_context` on the target page - the Carbon
   **example component instances** must be present on the page (instances
   whose nested components link to carbondesignsystem.com docs, e.g. a
   modal "Size=Large" instance).

If either check fails, **warn the user** stating exactly what was not found
(library not enabled / example components missing), then **ask what to do**
with the `question` tool (e.g. proceed without the library, wait while they
enable it and re-check, or abort). Do NOT silently proceed.

### 2. Produce the design spec

First, execute the `export-vanilla-html` process to produce the standalone
vanilla HTML page and dump it to the console. This serves as the design
spec - the designer can read it to understand every class, token, and
component used.

### 3. Capture into Figma

1. Start the Vite dev server in `frontend/`: `npm run dev`.
2. Navigate to the page's route (from `frontend/src/router.ts`).
3. Read [the frontend development skill](../skills/frontend-development/SKILL.md) -
   it defines how the 'Figma primitive' CSS classes map to Figma properties.
4. Build the page as **editable Figma nodes** with the `use_figma` tool (see
   below) at **two viewport widths**:
   a. **Desktop**: reconstruct at **1280px** width (root frame name
      `{Page} - 1280`).
   b. **Mobile**: reconstruct at **375px** width (root frame name
      `{Page} - 375`).

   The mobile view is NOT a new design - it is the SAME markup and classes with
   Carbon's real responsive behaviour applied at the narrow viewport:

   - **Grid columns**: use the `sm` breakpoint span (<= 672px). A column with
     `sm="4" md="8" lg="9"` renders at full width (`sm="4"` = 4 of 4 columns).
     Columns stack vertically.
   - **Flex wrapping**: `l-wrap` containers stack children vertically when the
     available width is too narrow. A row of 3 tiles becomes a column of 3.
   - **Header nav**: hidden at <= 672px (`cds-header-nav { display: none }`) per
     the page's SCSS. The header only shows the brand and auth buttons.
   - **Fluid type**: Carbon fluid tokens resolve to their narrow-viewport size.
     e.g. `fluid-heading-06` at 375px ~ 32.6px (vs 47.25px at 1280px).
   - **Padding**: utility classes like `l-py-10`/`l-py-12` still resolve to the
     same spacing tokens (CSS custom properties are not viewport-dependent). Do
     not shrink padding for mobile unless the source SCSS has a media query for
     it.
   - **Never invent**: do not add, remove, or rearrange sections. Do not change
     the order of content. Do not create a "mobile-specific" layout - only
     reproduce what the real components do when the browser window is 375px wide.

5. Stop the dev server.

#### How to use `use_figma`

`use_figma` executes JavaScript against the Figma Plugin API (the `figma`
global) inside the target file - that is how the editable frames get built.

- **Inputs**: `fileKey` (required - from the `--figma` URL,
  `figma.com/design/:fileKey/...`), `code` (required - the JS to run, max
  50,000 chars), `description` (required - plain-English summary of what the
  code does), `skillNames` (optional - pass
  `"figma-use,figma-generate-design"` on the first call so the tool's own
  usage skills are loaded; follow any instructions they return).
- **Invocation**: run Claude CLI non-interactively, e.g.
  `claude -p "<prompt instructing it to call use_figma with the code>"`.
  The tool is deferred - the prompt must tell Claude to find it via tool
  search and call it against the given fileKey.
- **Build editable nodes** with the Plugin API:
  - `figma.createFrame()` + `layoutMode: "VERTICAL"|"HORIZONTAL"`,
    `itemSpacing`, `padding{Top,Bottom,Left,Right}`,
    `primaryAxisSizingMode`/`counterAxisSizingMode` - this is Auto Layout,
    mapped from the `l-` classes.
  - `figma.createText()`; `await figma.loadFontAsync({family, style})`
    **before** setting `characters`, `fontSize`, `lineHeight`,
    `letterSpacing` - mapped from the `t-` classes/type tokens.
   - `fills`/`strokes` with solid RGB from the Carbon token hex values -
     mapped from the `c-`/`i-` classes and theme (white/g100).
   - Fonts: IBM Plex Sans (Regular, SemiBold), IBM Plex Mono (Regular).
- **Fill discipline (CRITICAL - the #1 source of bugs)**: Only frames that
  explicitly carry a theme class get a background fill. `cds--g100` frames
  get fill `#161616`. `cds--white` frames get fill `#FFFFFF`. The root page
  frame gets fill `#FFFFFF`. Every intermediate structural wrapper (cds-grid,
  cds-column, cds-stack, flex containers, spacer frames) MUST have null/empty
  fills - they are TRANSPARENT, inheriting the nearest ancestor's theme fill.
  NEVER give a wrapper frame an opaque fill unless it has a theme class in its
  name. Buttons follow the same rule: ONLY primary buttons get a background
  fill (`#0F62FE`). Tertiary buttons have NO fill (identity from stroke +
  text). Ghost buttons have NO fill and NO stroke (identity from text color
  only). Disabled primary buttons in g100 get fill `#525252` with text
  `#8D8D8D`. When in doubt: NO fill is safer than a wrong fill.
- **Chunk the work**: one `use_figma` call per page section (header, hero,
  stats, ...) per viewport. Desktop first, then mobile. Each call looks up the
  root frame by name (`figma.currentPage.findOne`) and appends to it. Wrap
  code in an async IIFE and `figma.notify`/`console.log` a result summary.
- **Verify** with `get_metadata` or `get_design_context` (node tree, names,
  sizes) on both root frames - NEVER `get_screenshot`.

### 4. Report

Print a summary table mapping every `t-`/`c-`/`i-`/`l-` class used on the
page to its Figma control (Text property, Fill, Auto Layout, etc.), sourced
from `.opencode/skills/frontend-development/SKILL.md`. Flag any class that
does not appear in the SKILL.md tables as a potential violation.

## After export (both paths)

After the export is complete, read
`.opencode/skills/frontend-development/SKILL.md` and apply its binding rules
to the current context. All subsequent frontend work in this session must
follow those rules.
