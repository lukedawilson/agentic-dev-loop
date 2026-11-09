"""Regression: the runs page sections must be spaced and paneled.

Siblings inside #list-view and #detail-view (filters, notices, panels)
were plain block-flow children with no margins, so every element on the
page touched the next one. The views must be flex columns with a gap like
the main page shell, and the filters form must sit in its own panel with a
header, matching the main dev loop page convention.

Also guards the hidden-attribute interaction: the views get author
display rules, which would override the UA [hidden] rule, so styles.css
must carry an explicit [hidden] guard or both views would render at once.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "src" / "agentic_dev_loop" / "viz" / "assets"


def _read(name: str) -> str:
    return (ASSETS / name).read_text(encoding="utf-8")


def _collapse(css: str) -> str:
    return re.sub(r"\s+", " ", css)


def test_filters_form_is_a_panel_with_header():
    html = _read("runs.html")
    match = re.search(r'<form id="filters".*?</form>', html, re.S)
    assert match, "filters form missing from runs.html"
    form = match.group(0)
    assert '<div class="panel-h">' in form, (
        "filters form must carry a panel header like the main page panels")
    assert ">Filters<" in form, "filters panel header must be titled Filters"
    assert '<div class="filters-row">' in form, (
        "filters fields must sit in a padded row inside the panel")


def test_views_are_flex_columns_with_gap():
    css = _collapse(_read("styles.css"))
    assert "#list-view, #detail-view { display: flex;" in css, (
        "runs views must be flex columns so siblings get spaced")
    assert "gap: 12px;" in css


def test_hidden_guard_keeps_views_hideable():
    css = _collapse(_read("styles.css"))
    assert "[hidden] { display: none !important; }" in css, (
        "an explicit [hidden] guard must override author display rules, "
        "or list/detail views would both render at once")


def test_filters_panel_styles_exist():
    css = _collapse(_read("styles.css"))
    assert ".filters { display: flex; flex-direction: column;" in css, (
        "filters form must be panel-shaped (column, border, layer bg)")
    assert ".filters-row {" in css and "padding: 10px;" in css, (
        "filters row must be a padded wrap row")


def test_no_refresh_or_apply_buttons():
    html = _read("runs.html")
    assert 'id="refresh"' not in html, "Refresh button was dropped"
    assert 'id="apply"' not in html, "Apply button was dropped (filters auto-apply)"
    assert 'id="reset"' in html, "Reset button must remain"


def test_filters_row_stays_one_line_on_desktop():
    css = _collapse(_read("styles.css"))
    assert ".filters-row { display: flex; flex-wrap: nowrap;" in css, (
        "filters must not wrap onto stacked lines on desktop")
    assert (".runs-shell { padding: 12px; } "
            ".filters-row { flex-wrap: wrap; }") in css, (
        "filters may only wrap inside the 800px media query")
