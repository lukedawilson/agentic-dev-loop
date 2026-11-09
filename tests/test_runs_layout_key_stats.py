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
    assert "Details (" in js
    assert 'class="metric-detail-body"' in js
