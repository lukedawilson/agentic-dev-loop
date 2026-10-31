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


def _normalize(text: str) -> str:
    """Case-fold and collapse whitespace - the matching normal form."""
    return " ".join(text.split()).casefold()


def _normalize_line(line: str) -> str:
    """Normalize one digest line, stripping the decorations Part A permits.

    A compliant digest writes anchors as headings on their own line, wrapped
    in ``**`` and (for the four section headings) prefixed with ``##``. List
    bullets are tolerated; the whole-line comparison is what makes a mid-line
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
    and anchors are matched line-anchored (case-insensitive,
    whitespace-collapsed, ``**`` decoration stripped). Env facts are matched
    verbatim, exactly as handed to the brief agent. An empty list means the
    digest passes coverage."""
    missing: list[str] = []
    lines = {_normalize_line(line) for line in brief_text.splitlines()}
    for section in sections:
        if _normalize(section) not in lines:
            missing.append(section)
    for anchor in anchors:
        if anchor not in lines:
            missing.append(anchor)
    for fact in env_facts:
        if fact not in brief_text:
            missing.append(fact)
    return missing
