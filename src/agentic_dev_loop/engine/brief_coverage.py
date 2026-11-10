"""Mechanical coverage gate for the brief step's section-anchored digest (041).

The brief agent's prompt contract (Part A) promises a structure-preserving
digest: every top-level section or category name survives on its own line.
Prompt compliance is not a guarantee, so this module derives the required
anchors from the include files themselves and checks the captured digest
against them. The brief node owns the retry budget; these functions stay pure
and file-agnostic.
"""

from __future__ import annotations

import re
import unicodedata


_GLYPH_FOLD = str.maketrans({
    "\u2010": "-", "\u2011": "-", "\u2012": "-",
    "\u2013": "-", "\u2014": "-", "\u2015": "-",
    "\u2018": "'", "\u2019": "'",
    "\u201c": '"', "\u201d": '"',
})


def _fold_glyphs(text: str) -> str:
    """Fold compatibility glyphs and collapse whitespace, preserving case."""
    folded = unicodedata.normalize("NFKC", text).translate(_GLYPH_FOLD)
    return " ".join(folded.split())


def _normalize(text: str) -> str:
    """Case-folded, glyph-folded, whitespace-collapsed matching form."""
    return _fold_glyphs(text).casefold()


def _normalize_line(line: str) -> str:
    """Normalize one digest line, stripping the decorations Part A permits.

    A compliant digest writes anchors as headings on their own line, wrapped
    in ``**`` and (for the four section headings) prefixed with ``##``. List
    bullets are tolerated; the line-prefix comparison is what makes a mid-line
    prose mention fail."""
    stripped = line.strip().replace("**", "")
    stripped = re.sub(r"^#+\s*", "", stripped)
    stripped = re.sub(r"^[-*+]\s+", "", stripped)
    return _normalize(stripped)


def extract_anchors(content: str) -> list[str]:
    """Derive the mechanical anchor set from one include file.

    Markdown contributes every ``## `` heading. YAML contributes each
    ``- name:`` value under a ``categories:`` block plus the
    ``enforcement_levels`` key. Anchors are normalized once, at extraction,
    and de-duplicated in first-seen order."""
    anchors: list[str] = []
    in_categories = False
    for raw in content.splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped:
            continue
        if stripped == "enforcement_levels:":
            anchors.append(_normalize("enforcement_levels"))
        if stripped == "categories:":
            in_categories = True
            continue
        if in_categories:
            if not line[:1].isspace():
                in_categories = False
            else:
                match = re.match(r"-\s*name:\s*(.+)$", stripped)
                if match:
                    anchors.append(_normalize(match.group(1).strip()))
        if line.startswith("## "):
            anchors.append(_normalize(line[3:].strip()))
    return list(dict.fromkeys(anchors))


def missing_anchors(brief_text: str, anchors: list[str],
                    env_facts: list[str], sections: list[str]) -> list[str]:
    """Return every required item the captured brief fails to cover.

    Required section headings (from the configured ``brief_inputs.sections``)
    and anchors are matched as line prefixes (case-insensitive,
    glyph-folded, whitespace-collapsed, decoration stripped): an item is
    covered when it starts a line, so content may follow on that line. A
    mid-line mention does not count. Env facts are matched as glyph-folded,
    whitespace-collapsed, case-sensitive substrings. An empty list means the
    digest passes coverage."""
    missing: list[str] = []
    lines = [_normalize_line(line) for line in brief_text.splitlines()]
    lines = [line for line in lines if line]

    def covered(item: str) -> bool:
        return any(line.startswith(item) for line in lines)

    for section in sections:
        if not covered(_normalize(section)):
            missing.append(section)
    for anchor in anchors:
        if not covered(anchor):
            missing.append(anchor)
    folded_brief = _fold_glyphs(brief_text)
    for fact in env_facts:
        if _fold_glyphs(fact) not in folded_brief:
            missing.append(fact)
    return missing
