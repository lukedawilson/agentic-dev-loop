---
description: Load frontend binding rules + all referenced implementation docs into context. Use /frontend-rules at the start of any frontend work.
---

# Load Frontend Development Rules

Read these files in full to load the complete frontend development rule set
into the current session:

1. **`.opencode/skills/frontend-development/SKILL.md`** - the authoritative
   binding rules: naming convention, genuine mapping test, escape hatches,
   anti-patterns, complete class reference tables (t-/c-/i-/l- utilities,
   component classes, CSS custom property overrides, sanctioned element rules
   register).

2. **`frontend/src/FigmaPrimitives.scss`** - the implementation of every
   documented utility class. Read this so you know exactly what each class
   does and which Carbon tokens it consumes.

3. **`frontend/scripts/lint-figma-primitives.mjs`** - the linter that enforces
   SCSS <-> SKILL.md sync (mapping-table sync, component-table sync, element-rule
   register sync, call-site usage, duplicate declarations, rule-level
   sanctioning, duplicate-claim detection, custom-property validity). Read this
   to understand what the linter will catch so you don't trigger violations.

After reading all three files, announce that the frontend rules are active.
All subsequent frontend work in this session must follow every binding rule
from the SKILL.md. In particular:

- Custom CSS is FORBIDDEN
- Inline styles are FORBIDDEN
- Element selectors outside the sanctions register are FORBIDDEN
- New utility classes MUST pass the genuine mapping test
- New utility classes MUST be added to SKILL.md tables in the same change
- A class-bearing rule duplicating an existing utility is FORBIDDEN (escape hatch 3)
- Run `npm run lint` in `frontend/` after any SCSS or SKILL.md change
- PREFER Carbon web components over utility classes
- PREFER Carbon utility classes over FigmaPrimitives utility classes
