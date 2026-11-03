---
name: frontend-development
description: Binding rules for ANY change under frontend/ - Carbon web components only, custom CSS forbidden, Figma-compatible t-/c-/i-/l- utility class naming. Use whenever creating or editing frontend markup, styles, Lit views, or SCSS, or when naming a new utility class.
---

# Frontend Development Rules

- ALWAYS make your designs responsive and mobile-friendly
- ALWAYS use native Carbon web components and utility classes
- Semantic HTML is REQUIRED where possible
- Inline styles are FORBIDDEN
- Custom CSS is FORBIDDEN
- Compatibility with Figma is REQUIRED, and custom CSS makes it IMPOSSIBLE
- Heavier markup (more nesting, etc.) is BETTER THAN custom CSS
- Finding idiomatic alternatives for the same semantic content within the design system is BETTER THAN custom CSS
- Using tables for layout is BETTER THAN custom CSS
- Any new utility class you mint MUST be added to the tables below in the same change
- Any new utility class MUST pass the genuine mapping test (below) - inventing a plausible-sounding Figma label is FORBIDDEN
- Any new utility class MUST have at least one call site in the same change; zero-usage classes MUST be deleted
- Element selectors (rules with no class) are FORBIDDEN outside the sanctioned element rules register below - a new register entry requires a written justification in the same change
- A class-bearing rule whose declarations exactly duplicate a documented t-/c-/i-/l- utility is FORBIDDEN - reuse the utility at the call site (escape hatch 3). The generalization is equally forbidden: a rule whose declarations are exactly a UNION of documented utilities is a semantic name hiding utility composition - compose the utilities at the call site
- t-/c- declarations on element descendants of a class are FORBIDDEN - type and colour must be visible classes at the call site. Spelling variants count as the same claim: the lint canonicalises `background:` -> `background-color:` and `text-align: start`/`end` -> `left`/`right` before comparing
- Run `npm run lint` in `frontend/` after any SCSS or SKILL.md change - it enforces SCSS <-> SKILL.md table sync (mapping tables, component table, element-rule register), call-site usage, duplicate declarations, rule-level sanctioning, duplicate-claim detection, union-claim detection, and element-descendant type-claim detection (also runs in CI as the required `frontend-lint` check)
- PREFER web components over utility classes
- PREFER Carbon utility classes over FigmaPrimitives utility classes

## Unavoidable custom CSS: Figma-compatible naming convention

When custom CSS is unavoidable (e.g., `<progress>` element styling, icon fills,
text alignment not supported by Carbon components), all class names MUST encode
their Figma Carbon Design Kit mapping using these prefixes:

| Prefix | Figma panel | Carbon token / property | Example |
|--------|------------|------------------------|---------|
| `t-` | Text properties | Text Styles panel or type tokens | t-code-01, t-uppercase, t-tabular |
| `c-` | Fill / Color | Carbon color tokens | c-text-secondary, c-support-error, c-link-primary |
| `i-` | Fill (icons) | Carbon color tokens (SVG) | i-status-warning, i-status-success |
| `l-` | Layout / Auto Layout | CSS layout properties | l-flex, l-gap-05, l-sticky |
| (none) | Design components | Named for the UI pattern | eyebrow, pricelist, meter |

## Minting a new utility class

Every new class MUST pass the **genuine mapping test**:

> Name the exact control a designer can operate in Figma's Design panel on the
> Carbon Design Kit - panel -> control -> value - that this class reproduces.

If you cannot, you are writing a CSS-mechanics class. Do NOT invent a
plausible-sounding Figma label; use an escape hatch below instead.

### Sanctioned Figma vocabulary (closed list)

A mapping may only cite these Figma affordances. A label citing anything else
is fictional by definition:

| Figma source | Covers |
|--------------|--------|
| Text Styles panel / type tokens | Body 01, Code 02, Fluid Heading 04, ... |
| Text properties | Transform, Align, Weight, Style, Line height, Numbers, Resize |
| Fill (Carbon color tokens) | Text Secondary, Layer 01, Background Brand, ... |
| Stroke / Border | width, color |
| Auto Layout | direction, gap, padding, alignment, wrap |
| Frame | width, height, corner radius |
| Layer order | Bring to front / Bring forward |
| Prototype | Scroll behavior, Overflow |
| Position | X/Y offset (absolute-positioned children only) |
| Component instance | Variant / component property selection (e.g. Text Input -> Field 01) |

### Escape hatches for CSS mechanics

When the behaviour you need has no Figma control:

1. **Global element reset** - if you are fixing an element default (a
   web-component host default or a UA-stylesheet default the design system
   never uses) rather than making a design decision, add an element selector
   to the resets block in `FigmaPrimitives.scss` and mint no class.
   Precedents: `cds-checkbox { margin-block-end: 0 }`, `cds-grid { display: block }`,
   `em { font-style: normal }` (Carbon type styles have no italic; Figma has
   no italics to undo).
2. **Implementation note** - when CSS mechanics diverge from the Figma concept
   a class implements, keep the honest Figma label and state the divergence in
   the table row.
3. **One implementation per Figma claim** - if a Figma command is already
   mapped by an existing class, reuse or extend it. Never mint a second class
   making the same claim with a different implementation.

### Anti-patterns (real failures from the originating repo - do not repeat)

| Class | Claimed mapping | Why it was fictional |
|-------|-----------------|----------------------|
| `l-block` / `l-inline` | "Frame -> Block/Inline" | No such control exists in Figma; both were redundant or replaceable at every call site |
| `l-self-start` | "Auto Layout child -> Align self: start" | Figma children cannot override the container's cross-axis alignment; it was also a no-op at every call site (parents were not flex containers) |
| `l-front` | "Layer order -> Bring to front" | Duplicated `l-order-front`'s claim with a conflicting implementation |
| `l-round` (old label) | "Frame -> Corner radius: 100%" | Figma corner radius takes px, not percentages - the label described the CSS, not Figma |
| `c-field-01` (old label) | "Fill: Field 01" | Choosing a component variant is not applying a fill token; the honest label names the variant choice |
| `l-inline-flex` | "Auto Layout -> Inline" | Figma auto layout is Horizontal/Vertical/Wrap - there is no "Inline"; inline flow is CSS mechanics. Replaced by `l-flex` (flex children hug content by default, wrapping the call site in an `l-flex` parent where needed) |
| `t-normal` | "Text -> Style: Normal" | Figma's style dropdown has "Regular", not "Normal", and the class existed to undo the UA's `em` italics - an element reset (escape hatch 1), not a design decision |
| `th-biz` | "SemiBold business names" | Exact duplicate of `t-semibold`'s declarations under a semantic name - escape hatch 3 violation, now caught by the DUPLICATE CLAIM lint |
| `compare-page h1` | "Text Style: Fluid Heading 04" | The claim was already mapped by `t-heading-04` et al.; applying it via element selector hid the mapping from the markup and dodged the linter - type must be a visible class at the call site |
| `.rec__meta` | "Layout pattern name" | Exactly the union of documented l- utilities under a semantic name - escape hatch 3 violation that dodged the exact-duplicate lint by combining claims; now caught by the UNION CLAIM lint and composed at the call sites |
| `score-page .drill h5` | (type styles on element descendants) | The compare-page h1 dodge one level down: `t-uppercase`/`t-semibold`/`c-text-secondary` claims applied to elements descended from a documented class, invisible in the markup - now caught by the ELEMENT-DESCENDANT TYPE CLAIM lint; the classes live on the elements at the call sites |
| `report-page .tablewrap th { background }` | (colour fills on element descendants) | The same dodge repeated with colour: `c-bg-layer-01` fill claims on element descendants, spelled `background:` to slip the `background-color:` claim string match - the fills moved to `c-bg-*` classes at the call sites, and the lint now canonicalises shorthand spellings before comparing claims |

## Class reference

This repo's class reference tables start empty (restored 2026-10-12). Every
utility class minted from this point must be added to the tables below in the
same change, pass the genuine mapping test, and have at least one call site.
The lint enforces SCSS <-> table sync in both directions.

### Text property classes (`t-`)

| Class | CSS | Figma |
|-------|-----|-------|

### Color fill classes (`c-`)

| Class | Token | Figma fill |
|-------|-------|------------|

### CSS custom property overrides

| Class | CSS | Figma |
|-------|-----|-------|

### Icon fill classes (`i-`)

| Class | Token | Figma fill |
|-------|-------|------------|

### Layout classes (`l-`)

| Class | CSS | Figma |
|-------|-----|-------|
| `.l-flex` | display: flex | Auto Layout -> direction: Horizontal |
| `.l-space-between` | justify-content: space-between | Auto Layout -> distribution: Space between |
| `.l-align-center` | align-items: center | Auto Layout -> alignment: Center |
| `.l-gap-03` | gap: 0.5rem | Auto Layout -> Gap -> 8 (spacing-03) |

### Component classes (no prefix)

| Class | Description |
|-------|-------------|
| `.portal-main` | The preview shell's main region: a flex row below the fixed 3rem header plus 3rem toolbar, holding the panel and the canvas, constrained to the viewport height so panel content scrolls internally instead of stretching the page. |
| `.portal-canvas` | The canvas region that fills the remaining width beside (or under) the panel, with 1rem padding around the preview. |
| `.portal-panel` | The Dashboard/Agent side panel: fixed 20rem width beside the canvas on desktop, laid out as a non-scrolling flex row so its rail and content children stretch full height; a full-height drawer overlaying the canvas below 66rem (BR-97). |
| `.portal-rail` | The Dashboard panel's internal subrail (Pages / Assets / Export / Sites): a fixed 4rem icon-over-label strip. |
| `.portal-rail-entry` | One rail entry: a native button stacking an icon over a label, with the selected-entry background. Disabled (dimmed, not-allowed) when the entry needs a selected site and none is selected - only Sites stays enabled, it is where a site gets created. Native because cds-button labels are max-content with no truncation and cannot stack. |
| `.portal-rail-sites` | Pins the Sites rail entry at the bottom of the rail (BR-92). |
| `.portal-panel-content` | The Dashboard panel's content pane beside the subrail - the panel's internal scroll container, so the rail stays pinned while a long category scrolls. |
| `.portal-toolbar` | The fixed 3rem toolbar row under the header: panel icons left, the edit-mode control right (no Save/Publish - the editor's in-canvas Save/Cancel bar is the single save surface). Hidden below 66rem, where the controls move into the header panel (BR-97). |
| `.portal-header-status` | The header's site context (site name + status pill), carrying the auto margin that right-aligns it and the logout action in the slot-less cds-header. |
| `.panel-row` | One list row in a panel category: a flex line holding the row main and its action buttons. |
| `.panel-row-main` | The row's growing region: the select control stacked over its meta line, shrinking so the action buttons stay inside the panel. |
| `.panel-row-select` | The row's select control: a native button whose label truncates with ellipsis (cds-button labels cannot - inline-size max-content, no text-overflow), styled as the row's link, bold current-page/site state via aria-current. |
| `.panel-row-name` | The asset row's name span: truncates with ellipsis like `.panel-row-select`, but as a plain span - assets are not selectable. Long names must not push the row past the panel. |
| `.asset-preview-media` | The accordion preview body's image/video/audio element, capped at the panel content width (intrinsic media sizes would overflow the 16rem content pane). |
| `.asset-preview-specimen` | The accordion preview body's font specimen line, set in the previewed font via a runtime-registered FontFace under the `yosk-asset-preview-font` alias (uploads are dynamic - no authored @font-face; Text property divergence noted here). |

### Sanctioned element rules (register)

Rules with no class at all escape the class-naming regime, so each one must be
registered here with a justification. The linter fails on any element rule not
in this register (and on any stale register row). Adding an entry is a design
decision that must be justified in the same change - the register makes the
former backdoor a reviewed, visible ledger.

| Selector | Why it cannot be a class |
|----------|--------------------------|
| `*, *::before, *::after` | box-sizing reset - UA default fix (escape hatch 1) |
| `[hidden]` | honour the HTML `hidden` attribute over utility display values - UA behaviour, not design |
| `cds-checkbox` | web-component host margin default (escape hatch 1 precedent) |
| `cds-grid` | web-component host display default (escape hatch 1 precedent) |
| `cds-inline-notification` | web-component host max-inline-size cap (18rem base, breakpoint-scaled) - banners should fill container width; no attribute exposes this (escape hatch 1, cds-grid precedent) |
| `em` | Carbon type styles have no italic; Figma has no italics to undo (escape hatch 1 precedent) |
| `svg` | UA inline-SVG baseline alignment fix (escape hatch 1) |
| `body` | UA margin reset + app-level Background fill and Plex type base |
| `h1, h2, h3, h4, h5, p, pre` | UA margin reset (type comes from Text Style classes at call sites; `pre` blocks take their spacing from the parent's auto-layout gap, same as `p`) |
| `a` | links are Link Primary app-wide - UA default colour replacement, not a per-call-site choice |
| `a:hover` | Link Primary Hover - UA default replacement paired with `a` |
| `iframe` | iframe UA default is 300x150; the preview shell's canvas needs the iframe to fill its padded container - a single scrolling surface instead of window+iframe double scrollbars - and no Carbon component exposes iframe sizing (escape hatch 1) |
| `cds-accordion-item::part(title)` | the accordion title's flex-shrink floor (min-inline-size auto = min-content) refuses to shrink under an unbreakable filename, so the title overflows 32px left of the heading in Carbon's row-reverse layout - zero the floor via Carbon's sanctioned ::part styling hook (escape hatch 1, cds-grid precedent) |
