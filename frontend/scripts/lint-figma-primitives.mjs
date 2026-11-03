#!/usr/bin/env node
// Lints the FigmaPrimitives utility-class system against the frontend-development
// skill rules (.opencode/skills/frontend-development/SKILL.md):
//
//   1. Every t-/c-/i-/l- class declared in frontend/src/**/*.scss must be
//      documented in a SKILL.md mapping table (tables whose header mentions Figma).
//   2. Every class documented in a SKILL.md mapping table must be declared in SCSS
//      (no stale documentation).
//   3. Every declared class must have at least one call site in frontend/src/**/*.ts
//      or frontend/index.html (dead-code rule).
//   4. No class may be declared more than once (duplicate-declaration rule).
//   5. Every custom property declared in frontend/src/**/*.scss must be either
//      referenced via var() in frontend/src, or be a genuine Carbon token
//      override consumed by Carbon's own stylesheet (dead-token rule).
//   6. Every declared --cds-* custom property must be a genuine Carbon token
//      name - emitted by @carbon/styles, or defined in @carbon/colors or
//      @carbon/themes (no fictional Carbon token names).
//   7. No .scss file may live under frontend/src/views/ - all view styles must
//      be in FigmaPrimitives.scss where the lint can see them.
//   8. Every t-/c-/i-/l- class used in frontend/src/**/*.ts or index.html must
//      be declared in SCSS (catches phantom classes like i-status).
//   9. Every style rule that declares properties must be sanctioned: it must
//      reference a class documented in SKILL.md (mapping tables or component
//      table), or - for element/attribute selectors with no class at all - be
//      listed in the SKILL.md "Sanctioned element rules" register. This closes
//      the element-selector backdoor.
//  10. Register <-> SCSS sync: every register entry must match a real element
//      rule (no stale register rows).
//  11. Component-table <-> SCSS sync: every component-table class must appear in
//      some SCSS selector (no stale component docs).
//  12. Duplicate-claim rule (escape hatch 3): no class-bearing rule may have
//      the same declaration set as a documented t-/c-/i-/l- utility - reuse
//      the utility at the call site instead.
//  13. Every @keyframes name must be mentioned in SKILL.md.
//  14. Union-claim rule (escape hatch 3, generalized): no class-bearing rule's
//      declaration set may be exactly the union of whole documented utility
//      bodies - a semantic name over a pure utility union is a hidden mapping
//      (the th-biz pattern). Compose the utilities at the call site.
//  15. Element-descendant type-claim rule: a selector that descends from a
//      class to a bare element (`.drill h5`, `.tablewrap th`) may not carry
//      declarations claimed by a documented t-/c- utility - type and colour
//      must be visible classes at the call site (the compare-page h1
//      anti-pattern).
//
// Checks 12, 14 and 15 compare declarations in canonical form, so spelling
// variants of the same claim - `background:` vs `background-color:`,
// `text-align: start` vs `left` - are caught rather than dodged.
//
// It cannot validate that a Figma label is genuine - that is covered by the
// "genuine mapping test" and the closed vocabulary in SKILL.md.

import { readFileSync, existsSync, readdirSync, statSync } from 'node:fs';
import { join, relative, dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const frontendDir = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const repoRoot = resolve(frontendDir, '..');
const skillPath = join(repoRoot, '.opencode/skills/frontend-development/SKILL.md');

const CLASS_RE = /\.([tcil]-[a-z0-9-]+)/g;

function walk(dir, ext, files = []) {
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) walk(full, ext, files);
    else if (full.endsWith(ext)) files.push(full);
  }
  return files;
}

function stripScssComments(source) {
  return source
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/\/\/[^\n]*/g, '');
}

// Minimal SCSS rule parser: returns every rule that declares at least one
// property, with its selector, ancestor selectors (nesting), enclosing
// @keyframes name (if any), and line number. @media wrappers are transparent.
function parseScssRules(source) {
  source = source.replace(/#\{([^}]*)\}/g, '#($1)'); // neutralize interpolation braces
  const rules = [];
  const stack = [];
  let buf = '';
  let line = 1;
  for (let i = 0; i < source.length; i++) {
    const ch = source[i];
    if (ch === '\n') line++;
    if (ch === '{') {
      const prelude = buf.replace(/\s+/g, ' ').trim();
      buf = '';
      let type = 'rule';
      if (prelude.startsWith('@keyframes')) type = 'keyframes';
      else if (prelude.startsWith('@')) type = 'media'; // @media/@each/@for/@supports...: transparent control frame
      stack.push({ type, prelude, line, decls: [] });
    } else if (ch === ';') {
      const decl = buf.trim();
      buf = '';
      const top = stack[stack.length - 1];
      if (top && top.type !== 'media' && decl) top.decls.push(decl);
    } else if (ch === '}') {
      const tail = buf.trim();
      buf = '';
      const frame = stack.pop();
      if (!frame) continue;
      if (tail) frame.decls.push(tail);
      if (frame.type === 'rule' && frame.decls.length > 0) {
        const keyframes = stack.find((f) => f.type === 'keyframes');
        rules.push({
          selector: frame.prelude,
          ancestors: stack.filter((f) => f.type === 'rule').map((f) => f.prelude),
          inKeyframes: keyframes ? keyframes.prelude.replace('@keyframes', '').trim() : null,
          decls: frame.decls,
          line: frame.line,
        });
      }
    } else {
      buf += ch;
    }
  }
  return rules;
}

// ---- 1. collect declared classes from SCSS ----------------------------------
const declared = new Map(); // class -> [{file, line}]
const declaredProps = new Map(); // custom property -> {file, line}
const parsedRules = []; // every SCSS rule with declarations, all files
const PROP_RE = /(--[a-z][a-z0-9-]*)\s*:/g;
for (const file of walk(join(frontendDir, 'src'), '.scss')) {
  const stripped = stripScssComments(readFileSync(file, 'utf8'));
  const rel = relative(repoRoot, file);
  for (const rule of parseScssRules(stripped)) {
    parsedRules.push({ ...rule, file: rel });
  }
  const lines = stripped.split('\n');
  lines.forEach((line, i) => {
    for (const match of line.matchAll(CLASS_RE)) {
      const cls = match[1];
      if (!declared.has(cls)) declared.set(cls, []);
      declared.get(cls).push({ file: rel, line: i + 1 });
    }
    for (const match of line.matchAll(PROP_RE)) {
      if (!declaredProps.has(match[1])) {
        declaredProps.set(match[1], { file: rel, line: i + 1 });
      }
    }
  });
}

// ---- 2. collect documented classes from SKILL.md mapping tables --------------
// A mapping table is any markdown table whose header row contains "Figma".
// The anti-patterns table deliberately omits "Figma" from its header so the
// fictional classes it records are not treated as documentation.
const documented = new Map(); // class -> line number
const componentClasses = new Set(); // component table (| Class | Description |)
const registerSelectors = new Map(); // sanctioned element selector -> SKILL line
const skillSource = readFileSync(skillPath, 'utf8');
{
  const lines = skillSource.split('\n');
  let inMappingTable = false;
  let inComponentTable = false;
  let inRegisterTable = false;
  lines.forEach((line, i) => {
    const isRow = /^\s*\|/.test(line);
    if (isRow && !inMappingTable && !inComponentTable && !inRegisterTable) {
      if (/Figma/i.test(line)) inMappingTable = true;
      else if (/\|\s*Class\s*\|\s*Description\s*\|/.test(line)) inComponentTable = true;
      else if (/\|\s*Selector\s*\|/.test(line)) inRegisterTable = true;
    } else if (!isRow) {
      inMappingTable = false;
      inComponentTable = false;
      inRegisterTable = false;
    }
    if (inMappingTable && isRow) {
      for (const match of line.matchAll(/`\.([tcil]-[a-z0-9-]+)`/g)) {
        if (!documented.has(match[1])) documented.set(match[1], i + 1);
      }
    }
    if (inComponentTable && isRow) {
      for (const match of line.matchAll(/`[^`]*`/g)) {
        for (const cls of match[0].matchAll(/\.([a-z][a-z0-9_-]*)/g)) {
          componentClasses.add(cls[1]);
        }
      }
    }
    if (inRegisterTable && isRow && !/^\s*\|\s*-+/.test(line) && !/\|\s*Selector\s*\|/.test(line)) {
      const cell = line.match(/`([^`]+)`/);
      if (cell) registerSelectors.set(cell[1].replace(/\s+/g, ' ').trim(), i + 1);
    }
  });
}

// ---- 3. collect usage corpus -------------------------------------------------
const usageFiles = [
  ...walk(join(frontendDir, 'src'), '.ts'),
  join(frontendDir, 'index.html'),
].map((file) => ({ file: relative(repoRoot, file), text: readFileSync(file, 'utf8') }));

function isUsed(cls) {
  const re = new RegExp(`(^|[^\\w-])${cls}([^\\w-]|$)`);
  return usageFiles.some(({ text }) => re.test(text));
}

// ---- 4. collect genuine Carbon token names -----------------------------------
// A --cds-* custom property name is genuine if @carbon/styles emits it, or if
// it is a @carbon/colors palette colour or @carbon/themes token (Carbon v11
// does not emit those as custom properties, but the names are still Carbon's).
const carbonCss = readFileSync(
  join(frontendDir, 'node_modules/@carbon/styles/css/styles.min.css'),
  'utf8',
);
const kebab = (name) => name.replace(/([a-z])([A-Z0-9])/g, '$1-$2').toLowerCase();
const carbonTokenNames = new Set(
  [...carbonCss.matchAll(/--cds-[a-z0-9-]+/g)].map((m) => m[0]),
);
const [colors, themes] = await Promise.all([
  import('@carbon/colors'),
  import('@carbon/themes'),
]);
for (const key of Object.keys(colors)) carbonTokenNames.add(`--cds-${kebab(key)}`);
for (const theme of Object.values(themes.themes ?? {})) {
  for (const key of Object.keys(theme)) carbonTokenNames.add(`--cds-${kebab(key)}`);
}

const propUsageCorpus = [
  ...usageFiles.map(({ text }) => text),
  ...walk(join(frontendDir, 'src'), '.scss').map((file) => readFileSync(file, 'utf8')),
];
const isPropUsed = (prop) => propUsageCorpus.some((text) => text.includes(`var(${prop}`));

// ---- checks -------------------------------------------------------------------
const errors = [];

// 7a. Forbid .scss files under src/views/
const viewsDir = join(frontendDir, 'src', 'views');
if (existsSync(viewsDir)) {
  for (const entry of readdirSync(viewsDir)) {
    if (entry.endsWith('.scss')) {
      errors.push(
        `VIEW BACKDOOR: frontend/src/views/${entry} - per-view SCSS files are forbidden. ` +
          `View styles must live in FigmaPrimitives.scss where the lint can see them.`,
      );
    }
  }
}

for (const [cls, sites] of declared) {
  if (!documented.has(cls)) {
    errors.push(
      `UNDOCUMENTED: .${cls} (${sites[0].file}:${sites[0].line}) is not in any SKILL.md mapping table. ` +
        `Add it with a genuine Figma mapping, or delete it.`,
    );
  }
  if (!isUsed(cls)) {
    errors.push(
      `UNUSED: .${cls} (${sites[0].file}:${sites[0].line}) has no call site in frontend/src/**/*.ts ` +
        `or frontend/index.html. Dead classes must be deleted.`,
    );
  }
  if (sites.length > 1) {
    errors.push(
      `DUPLICATE: .${cls} declared ${sites.length} times: ` +
        sites.map((s) => `${s.file}:${s.line}`).join(', '),
    );
  }
}

for (const [cls, line] of documented) {
  if (!declared.has(cls)) {
    errors.push(
      `STALE DOC: .${cls} (SKILL.md:${line}) is documented but not declared in any frontend/src SCSS file.`,
    );
  }
}

// 8. Catch used-but-undeclared [tcil]- classes (phantom classes)
{
  const usedClasses = new Map(); // class -> file
  for (const { file, text } of usageFiles) {
    const lines = text.split('\n');
    lines.forEach((line) => {
      for (const match of line.matchAll(/class\s*=\s*"([^"]*)"/g)) {
        const classList = match[1];
        for (const cls of classList.matchAll(CLASS_RE)) {
          const name = cls[1];
          if (!usedClasses.has(name)) usedClasses.set(name, file);
        }
      }
    });
    // Also catch programmatic class references: { class: 'i-status-error' }
    for (const match of text.matchAll(/\{[^}]*class\s*:\s*'([^']+)'/g)) {
      const classList = match[1];
      for (const cls of classList.matchAll(/[tcil]-[a-z0-9-]+/g)) {
        const name = cls[0];
        if (!usedClasses.has(name)) usedClasses.set(name, file);
      }
    }
  }

  for (const [cls, file] of usedClasses) {
    if (!declared.has(cls)) {
      errors.push(
        `PHANTOM CLASS: .${cls} (used in ${file}) is referenced in a class attribute or ` +
          `programmatic class argument but never declared in any SCSS file. ` +
          `Either declare the class or remove it from the call site.`,
      );
    }
  }
}

// ---- 9-13. rule-level sanctioning ---------------------------------------------
{
  const docAll = new Set([...documented.keys(), ...componentClasses]);
  const isDocumentedClass = (cls) =>
    cls.startsWith('cds--') || // Carbon's own class namespace (generated from @carbon packages)
    docAll.has(cls) ||
    docAll.has(cls.split('--')[0]);
  const normSel = (s) => s.replace(/\s+/g, ' ').trim();
  // Canonical declaration form for claim comparison: shorthand/longhand and
  // logical/physical keyword equivalents make the same Figma claim, so a
  // hidden mapping cannot dodge detection by spelling a claim differently
  // (`background:` for `background-color:`, `text-align: start` for `left`).
  const canonDecl = (d) => {
    const norm = d.replace(/\s+/g, ' ').trim();
    const bg = norm.match(/^background:\s*(\S+)$/);
    if (bg) return `background-color: ${bg[1]}`;
    if (norm === 'text-align: start') return 'text-align: left';
    if (norm === 'text-align: end') return 'text-align: right';
    return norm;
  };
  const normBody = (decls) => decls.map(canonDecl).filter(Boolean).sort().join('; ');

  // Utility bodies: documented [tcil] single-class rules, for the duplicate-claim check.
  const utilityBodies = new Map(); // normalized body -> utility class name
  const utilityDeclSets = new Map(); // utility class name -> [normalized declarations]
  for (const rule of parsedRules) {
    const m = rule.selector.match(/^\.([tcil]-[a-z0-9-]+)$/);
    if (m && documented.has(m[1]) && rule.ancestors.length === 0 && !rule.inKeyframes) {
      const body = normBody(rule.decls);
      if (!utilityBodies.has(body)) utilityBodies.set(body, m[1]);
      utilityDeclSets.set(m[1], rule.decls.map(canonDecl).filter(Boolean));
    }
  }
  // t-/c- claim lookup for the element-descendant check: normalized declaration
  // -> the t-/c- utility that makes that claim.
  const tcClaims = new Map();
  for (const [name, decls] of utilityDeclSets) {
    if (!/^[tc]-/.test(name)) continue;
    for (const d of decls) if (!tcClaims.has(d)) tcClaims.set(d, name);
  }
  // Largest bodies first, so union partitioning consumes multi-declaration
  // utilities before their single-declaration overlaps (l-flex vs l-flex-between).
  const utilityBodiesBySize = [...utilityDeclSets.entries()].sort(
    (a, b) => b[1].length - a[1].length,
  );
  const ELEMENT_DESCENDANT_RE = /\.[A-Za-z][\w-]*(?:\s+|\s*>\s*)[a-z][a-z0-9]*\b/;

  const seenRegisterSelectors = new Set();
  const allSelectorClasses = new Set();

  for (const rule of parsedRules) {
    const full = normSel([...rule.ancestors, rule.selector].join(' '));
    const classTokens = [...full.matchAll(/\.([A-Za-z][A-Za-z0-9_-]*)/g)].map((m) => m[1]);
    for (const cls of classTokens) allSelectorClasses.add(cls);

    if (rule.inKeyframes) {
      if (!skillSource.includes(rule.inKeyframes)) {
        errors.push(
          `UNDOCUMENTED KEYFRAMES: @keyframes ${rule.inKeyframes} (${rule.file}:${rule.line}) ` +
            `is not mentioned anywhere in SKILL.md.`,
        );
      }
      continue;
    }
    if (normSel(rule.selector) === ':root') continue;

    if (classTokens.length > 0) {
      if (!classTokens.some(isDocumentedClass)) {
        errors.push(
          `UNDOCUMENTED COMPONENT: "${full}" (${rule.file}:${rule.line}) - none of its classes ` +
            `appear in a SKILL.md mapping or component table. Document the class or delete the rule.`,
        );
      }
      const dup = utilityBodies.get(normBody(rule.decls));
      if (dup && normSel(rule.selector) !== `.${dup}`) {
        errors.push(
          `DUPLICATE CLAIM: "${full}" (${rule.file}:${rule.line}) has the same declarations as ` +
            `documented utility .${dup} - reuse the utility at the call site instead (escape hatch 3).`,
        );
      }
      // 14. Union claim: declaration set is exactly the union of whole
      // documented utility bodies - a semantic name hiding utility composition.
      const isUtilityRuleItself =
        rule.ancestors.length === 0 && /^\.[tcil]-[a-z0-9-]+$/.test(normSel(rule.selector));
      if (!isUtilityRuleItself && rule.decls.length > 1) {
        const remaining = new Set(rule.decls.map(canonDecl).filter(Boolean));
        const composed = [];
        for (const [name, decls] of utilityBodiesBySize) {
          if (decls.length > 0 && decls.every((d) => remaining.has(d))) {
            composed.push(name);
            for (const d of decls) remaining.delete(d);
          }
        }
        if (remaining.size === 0 && composed.length > 1) {
          errors.push(
            `UNION CLAIM: "${full}" (${rule.file}:${rule.line}) is exactly the union of documented ` +
              `utilities ${composed.map((c) => `.${c}`).join(' + ')} - a semantic name over a pure ` +
              `utility union is a hidden mapping (escape hatch 3). Compose the utilities at the call site.`,
          );
        }
      }
      // 15. Element-descendant type claim: t-/c- utility declarations on a
      // bare element descended from a class - type belongs on a visible class
      // at the call site (the compare-page h1 anti-pattern).
      if (ELEMENT_DESCENDANT_RE.test(full)) {
        for (const d of rule.decls.map(canonDecl).filter(Boolean)) {
          const claim = tcClaims.get(d);
          if (claim) {
            errors.push(
              `ELEMENT-DESCENDANT TYPE CLAIM: "${full}" (${rule.file}:${rule.line}) carries ` +
                `"${d}" - the claim of documented utility .${claim} - on an element descendant. ` +
                `Put .${claim} on the element at the call site; type must be visible in the markup.`,
            );
          }
        }
      }
    } else {
      const sel = normSel(rule.selector);
      if (registerSelectors.has(sel)) {
        seenRegisterSelectors.add(sel);
      } else {
        errors.push(
          `UNSANCTIONED ELEMENT RULE: "${full}" (${rule.file}:${rule.line}) declares properties but ` +
            `contains no documented class and is not in the SKILL.md sanctioned element rules register. ` +
            `Use documented classes at the call site, or register the selector with a justification.`,
        );
      }
    }
  }

  for (const [sel, line] of registerSelectors) {
    if (!seenRegisterSelectors.has(sel)) {
      errors.push(
        `STALE REGISTER: "${sel}" (SKILL.md:${line}) has no matching element rule in frontend/src SCSS.`,
      );
    }
  }

  for (const cls of componentClasses) {
    if (!allSelectorClasses.has(cls)) {
      errors.push(
        `STALE COMPONENT DOC: .${cls} (SKILL.md component table) appears in no SCSS selector. ` +
          `Delete the row or restore the class.`,
      );
    }
  }
}

for (const [prop, site] of declaredProps) {
  if (prop.startsWith('--cds-') && !carbonTokenNames.has(prop)) {
    errors.push(
      `FICTIONAL TOKEN: ${prop} (${site.file}:${site.line}) is not a Carbon token name ` +
        `(not in @carbon/styles, @carbon/colors, or @carbon/themes). Use the real Carbon token name.`,
    );
  }
  const isCarbonConsumed = carbonCss.includes(`var(${prop}`);
  if (!isPropUsed(prop) && !isCarbonConsumed) {
    errors.push(
      `UNUSED TOKEN: ${prop} (${site.file}:${site.line}) has no var() reference in frontend/src ` +
        `and is not consumed by @carbon/styles. Dead custom properties must be deleted.`,
    );
  }
}

if (errors.length > 0) {
  console.error(`figma-primitives lint: ${errors.length} error(s)\n`);
  for (const error of errors) console.error(`  ${error}`);
  process.exit(1);
}

console.log(
  `figma-primitives lint: OK (${declared.size} classes declared, ${documented.size} documented, ` +
    `${parsedRules.length} rules sanctioned, ${registerSelectors.size} registered element rules, ` +
    `${declaredProps.size} custom properties, all used, no duplicates, no duplicate claims, no fictional tokens)`,
);
