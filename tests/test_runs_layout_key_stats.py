"""Layout and register coverage for the runs key-stat reorganisation.

Every new class the key-stat band and drill-down expander ship must exist in
styles.css and in frontend/viz-register.md in the same change: the register
lint (test_viz_asset_register_lint) is the mechanical guard, and these tests
pin the layout expectations the spec names (three across on desktop,
stacking inside the existing 800px media query).
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "src" / "agentic_dev_loop" / "viz" / "assets"

NEW_CLASSES = [
    ".stat-band",
    ".stat",
    ".stat-label",
    ".stat-value",
    ".stat-basis",
    ".stat-gauge",
    ".stat-mix",
    ".stat-bars",
    ".stat-big",
    ".metric-expander",
    ".metric-summary",
    ".metric-detail-body",
    ".metric-summary-label",
    ".metric-summary-less",
    ".metric-summary-count",
]


def _read(name: str) -> str:
    return (ASSETS / name).read_text(encoding="utf-8")


def _collapse(css: str) -> str:
    return re.sub(r"\s+", " ", css)


def test_new_classes_are_declared():
    css = _read("styles.css")
    for cls in NEW_CLASSES:
        assert re.search(re.escape(cls) + r"\s*\{", css), (
            f"{cls} is missing from styles.css")


def test_new_classes_are_registered():
    register = (ROOT / "frontend" / "viz-register.md").read_text(
        encoding="utf-8")
    for cls in NEW_CLASSES:
        assert f"`{cls}`" in register, (
            f"{cls} is missing from frontend/viz-register.md")


def test_stat_band_is_three_across_desktop():
    css = _collapse(_read("styles.css"))
    assert ".stat-band { display: grid;" in css
    assert "repeat(3, minmax(0, 1fr))" in css


def test_stat_band_stacks_in_mobile_media_query():
    css = _collapse(_read("styles.css"))
    match = re.search(r"@media \(max-width: 800px\) \{(.*)\}\s*$", css)
    assert match, "800px media query missing"
    block = match.group(1)
    assert ".stat-band { grid-template-columns: 1fr; }" in block


def test_expander_markup_is_details_with_summary():
    js = _read("runs.js")
    assert '<details class="metric-expander"' in js
    assert '<summary class="metric-summary">' in js
    assert "Show more..." in js
    assert "Show less" in js
    assert "metric-summary-label metric-summary-less" in js
    assert 'class="metric-chevron"' not in js
    assert 'class="metric-summary-count' in js
    assert 'class="metric-detail-body"' in js


def test_expander_label_swaps_when_open():
    css = _collapse(_read("styles.css"))
    assert ".metric-summary-less { display: none" in css
    assert ".metric-expander[open] > .metric-summary" in css
    assert ":not(.metric-summary-less)" in css


def test_expander_label_is_carbon_link_styled():
    css = _collapse(_read("styles.css"))
    # Carbon link: link-primary color, no default underline, underline on
    # hover, and a focus-visible outline for keyboard users.
    assert ".metric-summary-label { flex: none; color: var(--cds-link-primary)" in css
    assert "text-decoration: none" in css
    assert ".metric-summary-label:hover { text-decoration: underline" in css
    assert ".metric-summary:focus-visible { outline: 2px solid var(--cds-focus)" in css
