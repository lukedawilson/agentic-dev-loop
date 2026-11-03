---
description: Export a lit view (e.g. LandingPage.ts) as a standalone vanilla HTML page - Carbon web components kept, lit plumbing stripped. Usage: /export-vanilla-html LandingPage.ts
---

Export the frontend view `$ARGUMENTS` as a single self-contained vanilla HTML
page and dump the full document to the console in one ```html code block.
Do NOT write any file unless explicitly asked.

The page must "just work" when opened via a static server (like the demos in
`design-docs/demo/`): same markup, same web components, no build step, no lit.
`design-docs/demo/index.html` is a reference output of this exact process for
`LandingPage.ts` - mirror its structure.

## Gather inputs

1. Resolve the component: look in `frontend/src/views/` for `$ARGUMENTS`
   (accept with or without the `.ts` extension). Read it fully.
2. Read the matching `.scss` file next to it, if one exists.
3. Read `frontend/src/main.ts` (which Carbon components/styles the app loads
   and the SCSS import chain) and `frontend/package.json` (installed
   `@carbon/*` versions).
4. Read `frontend/src/FigmaPrimitives.scss` (shared token overrides, resets,
   and `t-`/`c-`/`l-` utility classes - these are NOT in the component's
   inline template anymore).

## Build the `<head>`

1. **Carbon styles** - the same stylesheet the app imports, via jsdelivr,
   pinned to the installed version:
   `https://cdn.jsdelivr.net/npm/@carbon/styles@<version>/css/styles.min.css`
   This provides IBM Plex @font-face, the `.cds--white`/`.cds--g100` theme
   classes, and the `:root` grid custom properties (`--cds-grid-columns` etc.)
   that the `cds-grid` shadow styles depend on. Do NOT use the s81c
   `themes.css`/`plex.css` artifacts - they use different theme-class names
   and have broken the page before.
2. **Spacing tokens** - `@carbon/styles` css does not ship `--cds-spacing-*`
   (that is why the app has `frontend/src/carbon-tokens.scss`). Emit them in a
   small `:root` style block (01=0.125rem, 02=0.25, 03=0.5, 04=0.75, 05=1,
   06=1.5, 07=2, 08=2.5, 09=3, 10=4, 11=5, 12=6, 13=10rem).
3. **Figma primitives** - `FigmaPrimitives.scss` provides all shared
   token overrides (`--cds-yellow-*`, `--hairline`, `--cds-motion-moderate`),
   global resets, and the `t-`/`c-`/`l-` utility classes. Emit them as their
   own `<style>` block after the spacing tokens block. Use the clean CSS
   format from the SCSS source - do NOT minify. The component's `render()`
   no longer contains an inline `<style>` block.
4. **Component JS** - one `<script type="module">` per Carbon component tag
   family actually used in the component's template, from IBM's no-build CDN:
   `https://1.www.s81c.com/common/carbon/web-components/tag/v2/latest/<name>.min.js`
   (e.g. `ui-shell`, `button`, `grid`, `stack`, `tile`). Only include what the
   markup uses.
5. **Compiled SCSS** - compile the component's `.scss` with the project's own
   sass and inline the output in a `<style>` block:
   `cd frontend && npx sass --no-source-map --load-path=node_modules src/views/<Page>.scss /tmp/<page>.css`
   Include any component overrides from that SCSS file (e.g. `cds-grid::part`,
   `cds-header`, `cds-tile`, media queries) - these are part of the page SCSS,
   not the shared Figma primitives.
6. Add an HTML comment at the top of head explaining what was generated from
   what (see the reference file).

## Build the `<body>`

Body classes come from `frontend/index.html` (currently `class="cds--white"`).
Wrap the component's rendered markup in its custom-element tag (e.g.
`<landing-page>...</landing-page>`) - the components use light DOM
(`createRenderRoot() { return this; }`), and the compiled scss targets
descendant selectors like `landing-page h1`.

Copy the `render()` template **verbatim**, transforming only the lit plumbing:

- `@click=${...}` bindings -> plain `onclick="..."` attributes. Put shared
  logic in small named helpers in a `<script>` at the end of body (e.g.
  `scrollToId(id)`, `signIn()`), mirroring the component's methods exactly
  (same destinations, no demo-only rewrites).
- `${iconLoader(SomeIcon, { class: '...' })}` -> inline `<svg>`. Get the exact
  descriptor from the installed package:
  `node -e "console.log(JSON.stringify(require('<repo>/frontend/node_modules/@carbon/icons/lib/<icon-name>/<size>.js')))"`
  Reproduce all attrs including `data-icon-path="inner-path"` and `opacity`,
  put the `class` from the iconLoader call on the `<svg>`, add
  `aria-hidden="true"`.
- `${ARRAY.map(...)}` loops -> expand every iteration statically using the
  data arrays defined in the component file.
- Any other `${...}` interpolation -> its static value.
- Keep HTML entities (`&#8217;` etc.), attribute order, and indentation as
  close to the source as practical.

## Verify before dumping

Serve the result and smoke-test it headlessly (playwright-core with the
cached chromium headless shell under `~/Library/Caches/ms-playwright/`, or
any available headless browser). Write the candidate page to a temp dir, then
check:

- no console/page errors, no failed network requests
- all `cds-*` tags used are defined (`customElements.get(...)`)
- grid columns lay out side-by-side at 1280px (hero columns must not stack)
- `document.fonts` contains IBM Plex entries
- `getComputedStyle(document.body).getPropertyValue('--cds-spacing-05')` is
  non-empty

Fix anything broken, then print the final complete HTML document to the
console in a single ```html block.
