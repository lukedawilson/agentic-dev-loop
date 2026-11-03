"""Banned-command deny-list (036 D1, revised by 056).

The sandbox terminal enforces a deny-list before any shell spawn: a command
whose argv matches a step's ``forbidden_commands:`` regex (merged with the
engine defaults) returns a structured observation instead of reaching the
sandbox. Command steps are unaffected - they are declared infrastructure and
already fail fast on their exit code.

The defaults are run invariants only: git mutations and docker.
Tool-specific rules are not engine defaults - a step declares them with
``forbidden_commands`` (the bundled onboarding recipes carry the engine's
own toolchain guidance). The deny-list is network-independent by design;
network is a sandbox egress concern.

The default list carries human-readable labels; custom entries use their
regex text as the label.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class ForbiddenRule:
    label: str
    regex: re.Pattern[str]


_DEFAULT_PATTERNS: tuple[tuple[str, str], ...] = (
    ("git commit", r"\bgit\s+commit\b"),
    ("git push", r"\bgit\s+push\b"),
    ("git rebase", r"\bgit\s+rebase\b"),
    ("git checkout", r"\bgit\s+checkout\b"),
    ("git reset", r"\bgit\s+reset\b"),
    ("git tag", r"\bgit\s+tag\b"),
    ("docker", r"\bdocker\b"),
)


def _compile(label: str, pattern: str) -> ForbiddenRule:
    return ForbiddenRule(label=label, regex=re.compile(pattern, re.IGNORECASE))


DEFAULT_FORBIDDEN_RULES: tuple[ForbiddenRule, ...] = tuple(
    _compile(label, pattern) for label, pattern in _DEFAULT_PATTERNS)


def resolve_forbidden_rules(
        custom_patterns: list[str] | tuple[str, ...] | None,
) -> tuple[ForbiddenRule, ...]:
    """Merge the engine defaults with a step's custom regex list.

    Custom entries are appended after the defaults, so the first match wins
    on overlap; there is no disable escape hatch for the engine defaults.
    """
    rules = list(DEFAULT_FORBIDDEN_RULES)
    for pattern in custom_patterns or ():
        rules.append(_compile(pattern, pattern))
    return tuple(rules)


def match_forbidden_command(argv: list[str],
                            rules: tuple[ForbiddenRule, ...] | None) -> str | None:
    """Return the label of the first matching rule for ``argv``, else None.

    Match is a regex search over the space-joined argv, so shell-wrapped
    commands (``/bin/sh -c "git commit ..."``) still match. Empty argv never
    matches.
    """
    if not argv:
        return None
    text = " ".join(argv)
    for rule in rules or ():
        if rule.regex.search(text):
            return rule.label
    return None
