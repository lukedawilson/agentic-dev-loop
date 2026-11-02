"""Tests for tools/pypi_purge.py - the PyPI release purge script.

PyPI has no public API for deleting releases, so the tool drives the same
HTML forms the web UI submits. The HTTP session is thin glue; everything
decision-shaped (which versions to delete, which form to fill, whether a
re-auth is being demanded) lives in pure functions tested here.

The release fixture is a trimmed dump of the real PyPI JSON API response
for agentic-dev-loop, fetched 2026-11-02 (filenames and upload times are
the real ones). The HTML fixtures mirror the rendered warehouse manage
pages (delete form, re-auth form, two-factor form) field for field.
"""

from pathlib import Path

import pytest
from packaging.version import Version

REPO_ROOT = Path(__file__).resolve().parent.parent
PURGE = REPO_ROOT / "tools" / "pypi_purge.py"

import importlib.util  # noqa: E402


def _load_purge():
    spec = importlib.util.spec_from_file_location("pypi_purge", PURGE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


purge = _load_purge()

# Trimmed real dump: https://pypi.org/pypi/agentic-dev-loop/json (2026-11-02).
REAL_RELEASES = {
    "0.2.0": [
        {"filename": "agentic_dev_loop-0.2.0-py3-none-any.whl",
         "upload_time": "2026-10-19T18:24:29"},
        {"filename": "agentic_dev_loop-0.2.0.tar.gz",
         "upload_time": "2026-10-19T18:24:29"},
    ],
    "0.3.0": [
        {"filename": "agentic_dev_loop-0.3.0-py3-none-any.whl",
         "upload_time": "2026-10-19T19:02:59"},
        {"filename": "agentic_dev_loop-0.3.0.tar.gz",
         "upload_time": "2026-10-19T19:02:59"},
    ],
    "1.0.0a1": [
        {"filename": "agentic_dev_loop-1.0.0a1-py3-none-any.whl",
         "upload_time": "2026-10-20T12:30:07"},
        {"filename": "agentic_dev_loop-1.0.0a1.tar.gz",
         "upload_time": "2026-10-20T12:30:07"},
    ],
    "1.0.0a2": [
        {"filename": "agentic_dev_loop-1.0.0a2-py3-none-any.whl",
         "upload_time": "2026-10-20T13:47:49"},
    ],
    "1.0.0a16": [
        {"filename": "agentic_dev_loop-1.0.0a16-py3-none-any.whl",
         "upload_time": "2026-10-24T18:38:22"},
    ],
    "1.0.0a51": [
        {"filename": "agentic_dev_loop-1.0.0a51-py3-none-any.whl",
         "upload_time": "2026-10-31T22:26:21"},
        {"filename": "agentic_dev_loop-1.0.0a51.tar.gz",
         "upload_time": "2026-10-31T22:26:21"},
    ],
}

# Warehouse manage release page: the Delete release form.
DELETE_PAGE = """<!DOCTYPE html>
<html><body>
<form method="POST" action="/manage/project/agentic-dev-loop/release/1.0.0a51/">
  <input name="csrf_token" type="hidden" value="TOKEN123">
  <input name="confirm_delete_version" type="text" value="">
  <button type="submit">Delete release</button>
</form>
</body></html>"""

# Warehouse re-auth form, rendered when a destructive POST needs a password.
REAUTH_PAGE = """<!DOCTYPE html>
<html><body>
<form method="POST" action="/account/reauthenticate/">
  <input name="csrf_token" type="hidden" value="TOKEN456">
  <input name="next_route" type="hidden" value="manage.project.release">
  <input name="next_route_matchdict" type="hidden"
         value="{&quot;project_name&quot;: &quot;agentic-dev-loop&quot;,
                 &quot;version&quot;: &quot;1.0.0a51&quot;}">
  <input name="next_route_query" type="hidden" value="{}">
  <input name="password" type="password" value="">
  <button type="submit">Confirm password</button>
</form>
</body></html>"""

# Warehouse two-factor form, rendered at login when the account has 2FA.
TWO_FACTOR_PAGE = """<!DOCTYPE html>
<html><body>
<form method="POST" action="/account/two-factor/">
  <input name="csrf_token" type="hidden" value="TOKEN789">
  <input name="totp_value" type="text" value="">
  <button type="submit">Verify</button>
</form>
</body></html>"""


class TestVersionSelection:
    def test_keep_survives_everything_else_is_selected(self):
        plan = purge.versions_to_delete(REAL_RELEASES, "0.3.0")
        assert "0.3.0" not in plan
        assert "0.2.0" in plan
        assert "1.0.0a1" in plan
        assert "1.0.0a51" in plan
        assert len(plan) == 5

    def test_plan_is_oldest_first(self):
        plan = purge.versions_to_delete(REAL_RELEASES, "0.3.0")
        assert plan == sorted(plan, key=Version)

    def test_missing_keep_refuses_loudly(self):
        with pytest.raises(purge.PurgeError, match="not on PyPI"):
            purge.versions_to_delete(REAL_RELEASES, "0.1.0")

    def test_empty_release_lists_are_skipped(self):
        releases = {**REAL_RELEASES, "1.0.0a99": []}
        plan = purge.versions_to_delete(releases, "0.3.0")
        assert "1.0.0a99" not in plan

    def test_keep_is_the_only_release(self):
        plan = purge.versions_to_delete({"0.3.0": REAL_RELEASES["0.3.0"]}, "0.3.0")
        assert plan == []


class TestFormParsing:
    def test_delete_form_is_found(self):
        forms = purge.parse_forms(DELETE_PAGE)
        form = purge.find_form(forms, "confirm_delete_version")
        assert form is not None
        assert form["action"] == "/manage/project/agentic-dev-loop/release/1.0.0a51/"
        assert form["inputs"]["csrf_token"] == "TOKEN123"

    def test_reauth_detection(self):
        forms = purge.parse_forms(REAUTH_PAGE)
        assert purge.needs_reauth(forms)
        form = purge.find_form(forms, "password")
        assert form is not None
        assert form["inputs"]["next_route"] == "manage.project.release"
        assert form["action"] == "/account/reauthenticate/"

    def test_reauth_not_detected_on_plain_pages(self):
        assert not purge.needs_reauth(purge.parse_forms(DELETE_PAGE))

    def test_totp_form_is_found(self):
        forms = purge.parse_forms(TWO_FACTOR_PAGE)
        form = purge.find_form(forms, "totp_value")
        assert form is not None
        assert form["inputs"]["csrf_token"] == "TOKEN789"

    def test_entities_in_inputs_are_unescaped(self):
        html = ('<form method="POST" action="/x?a=1&amp;b=2">'
                '<input name="csrf_token" type="hidden" value="&quot;q&quot;">'
                "</form>")
        form = purge.parse_forms(html)[0]
        assert form["action"] == "/x?a=1&b=2"
        assert form["inputs"]["csrf_token"] == '"q"'


class TestPlanText:
    def test_plan_names_every_version_to_delete(self):
        text = purge.plan_text(REAL_RELEASES, "0.3.0", "agentic-dev-loop")
        assert "agentic-dev-loop" in text
        assert "keep: 0.3.0" in text
        for version in ("0.2.0", "1.0.0a1", "1.0.0a2", "1.0.0a16", "1.0.0a51"):
            assert version in text


class TestDryRunCLI:
    def test_dry_run_from_saved_dump_changes_nothing(self, tmp_path):
        import json
        import subprocess
        import sys

        dump = tmp_path / "dump.json"
        dump.write_text(json.dumps({"releases": REAL_RELEASES}))
        proc = subprocess.run(
            [sys.executable, str(PURGE), "--dry-run",
             "--from-json", str(dump), "--keep", "0.3.0"],
            capture_output=True, text=True,
        )
        assert proc.returncode == 0, proc.stderr
        assert "dry run" in proc.stdout.lower()
        assert "1.0.0a51" in proc.stdout
        assert "0.3.0" in proc.stdout
        assert "0.2.0" in proc.stdout

    def test_dry_run_with_wrong_keep_fails_loud(self, tmp_path):
        import json
        import subprocess
        import sys

        dump = tmp_path / "dump.json"
        dump.write_text(json.dumps({"releases": REAL_RELEASES}))
        proc = subprocess.run(
            [sys.executable, str(PURGE), "--dry-run",
             "--from-json", str(dump), "--keep", "0.1.0"],
            capture_output=True, text=True,
        )
        assert proc.returncode != 0
        assert "not on PyPI" in proc.stderr
