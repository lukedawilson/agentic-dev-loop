"""Tests for tools/pypi_yank.py - the PyPI release yank script.

PyPI has no public API for yanking releases, so the tool drives the same
HTML forms the web UI submits. The HTTP session is thin glue; everything
decision-shaped (which versions to yank, which form to fill, whether a
re-auth is being demanded, whether the release is already yanked) lives in
pure functions tested here.

The release fixture is a trimmed dump of the real PyPI JSON API response
for agentic-dev-loop, fetched 2026-11-11 (filenames and yanked flags are
the real ones). The HTML fixtures mirror the rendered warehouse manage
pages (yank form, un-yank form, re-auth form, two-factor form) field for
field, per warehouse/manage/views/__init__.py (POST triggered on
confirm_yank_version, require_reauth) and
warehouse/templates/manage/project/release.html.
"""

from pathlib import Path

import pytest
from packaging.version import Version

REPO_ROOT = Path(__file__).resolve().parent.parent
YANK = REPO_ROOT / "tools" / "pypi_yank.py"

import importlib.util  # noqa: E402


def _load_yank():
    spec = importlib.util.spec_from_file_location("pypi_yank", YANK)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


yank = _load_yank()

# Trimmed real dump: https://pypi.org/pypi/agentic-dev-loop/json
# (2026-11-11). Every 0.x release carries a wheel and an sdist, all
# unyanked. 1.0.0a1 (the keep target) had not been published yet at dump
# time, which the absent-keep tests exercise.
REAL_RELEASES = {
    "0.3.0": [
        {"filename": "agentic_dev_loop-0.3.0-py3-none-any.whl",
         "yanked": False},
        {"filename": "agentic_dev_loop-0.3.0.tar.gz", "yanked": False},
    ],
    "0.10.0": [
        {"filename": "agentic_dev_loop-0.10.0-py3-none-any.whl",
         "yanked": False},
        {"filename": "agentic_dev_loop-0.10.0.tar.gz", "yanked": False},
    ],
    "0.15.7": [
        {"filename": "agentic_dev_loop-0.15.7-py3-none-any.whl",
         "yanked": False},
        {"filename": "agentic_dev_loop-0.15.7.tar.gz", "yanked": False},
    ],
}

# Warehouse manage release page, unyanked release: the Yank release form
# (confirm_yank_version text input, optional yanked_reason, csrf) plus a
# per-file delete form, which must NOT be mistaken for the yank form.
YANK_PAGE = """<!DOCTYPE html>
<html><body>
<form method="POST" action="/manage/project/agentic-dev-loop/release/0.15.7/">
  <input name="csrf_token" type="hidden" value="TOKEN123">
  <input name="confirm_yank_version" type="text" value="">
  <input name="yanked_reason" type="text" value="">
  <button type="submit">Yank release</button>
</form>
<form method="POST" action="/manage/project/agentic-dev-loop/release/0.15.7/file/agentic_dev_loop-0.15.7.tar.gz/">
  <input name="csrf_token" type="hidden" value="TOKEN777">
  <input name="confirm_delete_version" type="text" value="">
  <button type="submit">Delete file</button>
</form>
</body></html>"""

# Warehouse manage release page, yanked release: the Un-yank form replaces
# the yank form. This is both the post-yank verification signal and the
# idempotence signal on a re-run.
UNYANK_PAGE = """<!DOCTYPE html>
<html><body>
<form method="POST" action="/manage/project/agentic-dev-loop/release/0.15.7/">
  <input name="csrf_token" type="hidden" value="TOKEN123">
  <input name="confirm_unyank_version" type="text" value="">
  <button type="submit">Un-yank release</button>
</form>
</body></html>"""

# Warehouse login page, for the session-expired guard in verification.
LOGIN_PAGE = """<!DOCTYPE html>
<html><body>
<form method="POST" action="/account/login/">
  <input name="csrf_token" type="hidden" value="TOKEN000">
  <input name="username" type="text" value="">
  <input name="password" type="password" value="">
  <button type="submit">Log in</button>
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
                 &quot;version&quot;: &quot;0.15.7&quot;}">
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
        plan = yank.versions_to_yank(REAL_RELEASES, "1.0.0a1")
        assert "1.0.0a1" not in plan
        assert "0.3.0" in plan
        assert "0.10.0" in plan
        assert "0.15.7" in plan
        assert len(plan) == 3

    def test_plan_is_oldest_first(self):
        plan = yank.versions_to_yank(REAL_RELEASES, "1.0.0a1")
        assert plan == sorted(plan, key=Version)

    def test_multiple_keeps_all_survive(self):
        plan = yank.versions_to_yank(REAL_RELEASES, "0.3.0,1.0.0a1")
        assert "0.3.0" not in plan
        assert "1.0.0a1" not in plan
        assert plan == ["0.10.0", "0.15.7"]

    def test_absent_keep_is_noted_and_ignored(self):
        plan = yank.versions_to_yank(REAL_RELEASES, "1.0.0a1")
        assert plan == ["0.3.0", "0.10.0", "0.15.7"]
        assert yank.missing_keeps(REAL_RELEASES, "1.0.0a1") == ["1.0.0a1"]

    def test_partially_missing_keep_list_keeps_the_rest(self):
        plan = yank.versions_to_yank(REAL_RELEASES, "0.3.0,9.9.9")
        assert "0.3.0" not in plan
        assert "0.15.7" in plan
        assert yank.missing_keeps(REAL_RELEASES, "0.3.0,9.9.9") == ["9.9.9"]

    def test_empty_release_lists_are_skipped(self):
        releases = {**REAL_RELEASES, "0.15.8": []}
        plan = yank.versions_to_yank(releases, "1.0.0a1")
        assert "0.15.8" not in plan

    def test_keep_is_the_only_release(self):
        plan = yank.versions_to_yank(
            {"1.0.0a1": [{"filename": "agentic_dev_loop-1.0.0a1.tar.gz",
                          "yanked": False}]},
            "1.0.0a1")
        assert plan == []


class TestFormParsing:
    def test_yank_form_is_found(self):
        forms = yank.parse_forms(YANK_PAGE)
        form = yank.find_form(forms, "confirm_yank_version")
        assert form is not None
        assert form["action"] == \
            "/manage/project/agentic-dev-loop/release/0.15.7/"
        assert form["inputs"]["csrf_token"] == "TOKEN123"

    def test_per_file_delete_form_is_not_the_yank_form(self):
        forms = yank.parse_forms(YANK_PAGE)
        form = yank.find_form(forms, "confirm_yank_version")
        assert form["inputs"]["csrf_token"] == "TOKEN123"
        assert "confirm_delete_version" not in form["inputs"]

    def test_already_yanked_detected_via_unyank_form(self):
        assert yank.already_yanked(yank.parse_forms(UNYANK_PAGE))

    def test_already_yanked_false_on_yank_page(self):
        assert not yank.already_yanked(yank.parse_forms(YANK_PAGE))

    def test_reauth_detection(self):
        forms = yank.parse_forms(REAUTH_PAGE)
        assert yank.needs_reauth(forms)
        form = yank.find_form(forms, "password")
        assert form is not None
        assert form["inputs"]["next_route"] == "manage.project.release"
        assert form["action"] == "/account/reauthenticate/"

    def test_reauth_not_detected_on_plain_pages(self):
        assert not yank.needs_reauth(yank.parse_forms(YANK_PAGE))

    def test_totp_form_is_found(self):
        forms = yank.parse_forms(TWO_FACTOR_PAGE)
        form = yank.find_form(forms, "totp_value")
        assert form is not None
        assert form["inputs"]["csrf_token"] == "TOKEN789"

    def test_entities_in_inputs_are_unescaped(self):
        html = ('<form method="POST" action="/x?a=1&amp;b=2">'
                '<input name="csrf_token" type="hidden" value="&quot;q&quot;">'
                "</form>")
        form = yank.parse_forms(html)[0]
        assert form["action"] == "/x?a=1&b=2"
        assert form["inputs"]["csrf_token"] == '"q"'


class TestPostHeaders:
    def test_posts_carry_the_origin_header(self):
        # Load-bearing: warehouse checks the CSRF origin on every POST and
        # answers HTTP 400 when neither Origin nor Referer is present,
        # before any credential validation happens. Sending the Origin
        # header is what makes the login POST survive long enough to ask
        # for credentials.
        headers = yank.post_headers()
        assert headers["Origin"] == "https://pypi.org"
        assert headers["Content-Type"] == "application/x-www-form-urlencoded"
        assert headers["User-Agent"].startswith("agentic-dev-loop")


class TestYankPostBody:
    def test_body_carries_version_reason_and_csrf(self):
        form = yank.find_form(yank.parse_forms(YANK_PAGE),
                              "confirm_yank_version")
        body = yank.yank_post_body(form, "0.15.7", "superseded")
        assert body["confirm_yank_version"] == "0.15.7"
        assert body["yanked_reason"] == "superseded"
        assert body["csrf_token"] == "TOKEN123"

    def test_body_without_reason_sends_empty_string(self):
        form = yank.find_form(yank.parse_forms(YANK_PAGE),
                              "confirm_yank_version")
        body = yank.yank_post_body(form, "0.15.7", "")
        assert body["confirm_yank_version"] == "0.15.7"
        assert body["yanked_reason"] == ""
        assert body["csrf_token"] == "TOKEN123"


class TestRedirectBackDetection:
    def test_redirect_back_to_release_page_is_a_refusal(self):
        location = "/manage/project/agentic-dev-loop/release/0.15.7/"
        assert yank.yank_redirects_back(location, "agentic-dev-loop", "0.15.7")

    def test_redirect_away_is_success(self):
        location = "/manage/project/agentic-dev-loop/releases/"
        assert not yank.yank_redirects_back(location, "agentic-dev-loop",
                                            "0.15.7")

    def test_absolute_location_is_parsed(self):
        location = ("https://pypi.org/manage/project/agentic-dev-loop/"
                    "release/0.15.7/")
        assert yank.yank_redirects_back(location, "agentic-dev-loop", "0.15.7")


class TestLoginPageDetection:
    def test_login_page_detected(self):
        assert yank.is_login_page(yank.parse_forms(LOGIN_PAGE))

    def test_reauth_page_is_not_login_page(self):
        assert not yank.is_login_page(yank.parse_forms(REAUTH_PAGE))

    def test_yank_page_is_not_login_page(self):
        assert not yank.is_login_page(yank.parse_forms(YANK_PAGE))


class TestPlanText:
    def test_plan_names_every_version_to_yank(self):
        text = yank.plan_text(REAL_RELEASES, "1.0.0a1", "agentic-dev-loop")
        assert "agentic-dev-loop" in text
        assert "keep: 1.0.0a1" in text
        for version in ("0.3.0", "0.10.0", "0.15.7"):
            assert version in text

    def test_plan_notes_absent_keeps(self):
        text = yank.plan_text(REAL_RELEASES, "0.3.0,9.9.9",
                              "agentic-dev-loop")
        assert "note: keep '9.9.9' is not on PyPI" in text

    def test_plan_notes_already_yanked_versions(self):
        releases = {**REAL_RELEASES,
                    "0.15.0": [{"filename": "agentic_dev_loop-0.15.0.tar.gz",
                                "yanked": True}]}
        text = yank.plan_text(releases, "1.0.0a1", "agentic-dev-loop")
        assert "already fully yanked" in text
        assert "0.15.0" in text


class TestDefaultKeep:
    def test_default_keep_is_the_pyproject_version(self):
        import tomllib

        data = tomllib.loads(
            (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        assert yank.default_keep() == data["project"]["version"]


class TestDryRunCLI:
    def test_dry_run_from_saved_dump_changes_nothing(self, tmp_path):
        import json
        import subprocess
        import sys

        dump = tmp_path / "dump.json"
        dump.write_text(json.dumps({"releases": REAL_RELEASES}))
        proc = subprocess.run(
            [sys.executable, str(YANK), "--dry-run",
             "--from-json", str(dump), "--keep", "1.0.0a1"],
            capture_output=True, text=True,
        )
        assert proc.returncode == 0, proc.stderr
        assert "dry run" in proc.stdout.lower()
        assert "0.15.7" in proc.stdout
        assert "0.3.0" in proc.stdout
        assert "1.0.0a1" in proc.stdout

    def test_dry_run_with_absent_keep_still_plans(self, tmp_path):
        import json
        import subprocess
        import sys

        dump = tmp_path / "dump.json"
        dump.write_text(json.dumps({"releases": REAL_RELEASES}))
        proc = subprocess.run(
            [sys.executable, str(YANK), "--dry-run",
             "--from-json", str(dump), "--keep", "9.9.9"],
            capture_output=True, text=True,
        )
        assert proc.returncode == 0, proc.stderr
        assert "not on PyPI" in proc.stdout
        assert "0.15.7" in proc.stdout
