#!/usr/bin/env python3
"""Yank PyPI releases - the same requests the pypi.org web UI makes.

PyPI has no public API for yanking releases: the JSON API is read-only
and there is no documented yank endpoint. Project owners can yank
releases only through the website, which is plain HTML forms over
warehouse routes. This script logs in the way a browser does and submits
the same "Yank release" form the UI submits, once per version.

The flow, per version:
1. GET the manage release page and parse the yank form. A release that
   is already fully yanked renders the "Un-yank" form instead; it is
   skipped with a note (re-runs are idempotent).
2. POST the form with confirm_yank_version=<version> (the warehouse view
   refuses a value that does not match the release version, bouncing
   back to the release page with a flash error) and, when --reason is
   given, yanked_reason=<reason> (pip displays it to users who install
   the yanked pin anyway).
3. PyPI demands a re-authentication (password, and a TOTP code when the
   account has 2FA) for destructive actions once per 30 minutes: the
   POST comes back with the re-auth form instead of a redirect. Submit
   it once and the whole remaining batch goes through.
4. A redirect back to the release page is treated as a refusal and
   reported loudly with the flash text; success goes to the releases
   list.
5. Verify against the DB-backed manage page that the release now renders
   the un-yank form before touching the next one; a failed verification
   aborts loudly.

Validated against warehouse source (the deployed pypi.org codebase):
- warehouse/manage/views/__init__.py, ManageProjectRelease:
  yank_project_release is a POST to the release URL triggered by
  request_param confirm_yank_version, require_reauth=True, reads
  yanked_reason from the POST body, sets release.yanked on success and
  redirects to the releases list; refusals HTTPSeeOther back to the
  release page. unyank_project_release (confirm_unyank_version) is the
  undo path on the same route, so yanking is fully reversible and the
  already-yanked state is visible on the manage page.
- warehouse/templates/manage/project/release.html: the yank section
  renders only when release.yanked is false, and the un-yank section
  otherwise; the confirm modal posts to the same release URL.
- The login, re-auth, two-factor, CSRF, and Origin-header plumbing is
  shared with tools/pypi_purge.py and was probe-validated against the
  live site in 2026-11: warehouse answers HTTP 400 on POSTs without an
  Origin header, before any credential check.

Safety:
- --dry-run fetches the release list and prints the plan, yanking
  nothing.
- The live run demands the project name typed at the prompt before the
  first yank.
- Keep versions absent from the release list are noted in the plan and
  skipped; the run proceeds. Nothing left to protect is not an error.
- Credentials are read from the terminal (getpass) and never stored.
- A yank is reversible (un-yank from the same page), but the script
  still refuses to act until you confirm.

Usage:
    python tools/pypi_yank.py --dry-run           # plan only, live PyPI list
    python tools/pypi_yank.py --from-json dump.json --dry-run   # offline plan
    python tools/pypi_yank.py --reason "superseded by 1.0.0a1"  # the real thing
"""

from __future__ import annotations

import argparse
import getpass
import html
import html.parser
import json
import sys
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from http.cookiejar import CookieJar
from pathlib import Path
from typing import Optional

from packaging.version import Version

PYPI = "https://pypi.org"
DEFAULT_PROJECT = "agentic-dev-loop"
USER_AGENT = "agentic-dev-loop pypi-yank (maintainer script)"
VERIFY_TRIES = 3
VERIFY_WAIT = 2.0
YANK_PAUSE = 0.5


class YankError(Exception):
    """A loud, user-facing failure. The message is complete on its own."""


def post_headers() -> dict:
    """Headers for form POSTs.

    The Origin header is load-bearing: pypi.org (warehouse on Pyramid >= 2)
    checks the CSRF origin on every POST and answers HTTP 400 when neither
    Origin nor Referer is present, before any credential or form validation
    happens. A bare urllib POST without it never gets to log in.
    """
    return {
        "User-Agent": USER_AGENT,
        "Content-Type": "application/x-www-form-urlencoded",
        "Origin": PYPI,
    }


def body_excerpt(page: str, limit: int = 600) -> str:
    """The response body, flattened, for loud failure messages."""
    return " ".join(page.split())[:limit]


def split_keeps(keep) -> list[str]:
    """One keep version or a comma-separated list of keeps, stripped."""
    return [k.strip() for k in keep.split(",")] if isinstance(keep, str) else list(keep)


def missing_keeps(releases: dict, keep) -> list[str]:
    """Keep versions that are not on PyPI (already gone, or never were)."""
    return [k for k in split_keeps(keep) if k not in releases]


def fully_yanked(files: list) -> bool:
    """True when every file of a release carries yanked=true in the JSON.

    A planning hint only: the search index can lag, so the live run
    re-checks the DB-backed manage page and skips there instead.
    """
    return bool(files) and all(f.get("yanked") for f in files)


def versions_to_yank(releases: dict, keep) -> list[str]:
    """Every version with at least one file except the keeps, oldest first.

    Keep versions absent from the release list are simply nothing to
    protect: they are noted in the plan and yanking proceeds.
    """
    keeps = split_keeps(keep)
    versions = [v for v in releases if releases[v] and v not in keeps]
    return sorted(versions, key=Version)


def plan_text(releases: dict, keep: str, project: str) -> str:
    versions = versions_to_yank(releases, keep)
    total_files = sum(len(releases[v]) for v in versions)
    lines = [
        f"PyPI yank plan for {project}",
        f"keep: {keep}",
        f"yank: {len(versions)} releases ({total_files} files):",
    ]
    for version in missing_keeps(releases, keep):
        lines.append(f"note: keep {version!r} is not on PyPI; nothing to protect")
    for version in versions:
        if fully_yanked(releases[version]):
            lines.append(f"note: {version} is already fully yanked on the "
                         "index; the run will confirm and skip it")
    lines += [f"  - {v} ({len(releases[v])} files)" for v in versions]
    return "\n".join(lines)


class _FormParser(html.parser.HTMLParser):
    """Collect every <form> with its named inputs, values entity-unescaped."""

    def __init__(self) -> None:
        super().__init__()
        self.forms: list[dict] = []
        self._form: Optional[dict] = None

    def handle_starttag(self, tag: str, attrs: list) -> None:
        a = dict(attrs)
        if tag == "form":
            self._form = {
                "action": html.unescape(a.get("action", "")),
                "inputs": {},
            }
        elif tag in ("input", "button") and self._form is not None:
            name = a.get("name")
            if name:
                self._form["inputs"][html.unescape(name)] = html.unescape(
                    a.get("value", "")
                )

    def handle_endtag(self, tag: str) -> None:
        if tag == "form" and self._form is not None:
            self.forms.append(self._form)
            self._form = None


def parse_forms(page: str) -> list[dict]:
    parser = _FormParser()
    parser.feed(page)
    return parser.forms


def find_form(forms: list[dict], field: str) -> Optional[dict]:
    for form in forms:
        if field in form["inputs"]:
            return form
    return None


def needs_reauth(forms: list[dict]) -> bool:
    """True when the page demands a password re-authentication form."""
    form = find_form(forms, "password")
    return form is not None and "next_route" in form["inputs"]


def already_yanked(forms: list[dict]) -> bool:
    """True when the page renders the un-yank form instead of the yank
    form: the release is already fully yanked, so skip it."""
    return find_form(forms, "confirm_yank_version") is None \
        and find_form(forms, "confirm_unyank_version") is not None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never follow redirects: the caller inspects every 3xx."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Session:
    """Cookie-keeping HTTP client that stops at every redirect."""

    def __init__(self) -> None:
        self.jar = CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar), _NoRedirect()
        )

    def request(self, method: str, url: str, data: Optional[dict] = None) -> tuple:
        body = None
        headers = {"User-Agent": USER_AGENT}
        if data is not None:
            body = urllib.parse.urlencode(data).encode("utf-8")
            headers = post_headers()
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            resp = self.opener.open(req)
            page = resp.read().decode("utf-8", errors="replace")
            return resp.status, dict(resp.headers), page
        except urllib.error.HTTPError as exc:
            page = exc.read().decode("utf-8", errors="replace")
            return exc.code, dict(exc.headers), page

    def follow(self, url: str, hops: int = 5) -> tuple:
        """GET following 3xx redirects manually, up to `hops` of them."""
        for _ in range(hops):
            status, headers, page = self.request("GET", url)
            if status in (301, 302, 303, 307, 308):
                location = headers.get("Location") or headers.get("location")
                if not location:
                    raise YankError(f"redirect without Location from {url}")
                url = urllib.parse.urljoin(url, location)
                continue
            return status, url, page
        raise YankError(f"too many redirects following {url}")

    def csrf(self, page: str) -> str:
        forms = parse_forms(page)
        token = None
        for form in forms:
            if "csrf_token" in form["inputs"]:
                token = form["inputs"]["csrf_token"]
        if not token:
            raise YankError("no csrf_token found on the page")
        return token


def login(session: Session) -> None:
    """Browser-style login, including the two-factor page when needed."""
    status, _, page = session.follow(f"{PYPI}/account/login/")
    if status != 200:
        raise YankError(f"login page returned HTTP {status}")
    csrf = session.csrf(page)
    username = input("PyPI username: ").strip()
    if not username:
        raise YankError("no username given")
    status, headers, page = session.request(
        "POST", f"{PYPI}/account/login/",
        {"csrf_token": csrf, "username": username,
         "password": getpass.getpass("PyPI password: ")},
    )
    if status not in (301, 302, 303):
        print(f"login POST returned HTTP {status}: {body_excerpt(page)}",
              file=sys.stderr)
        if "Invalid username or password" in page or status == 200:
            raise YankError("login failed (bad credentials)")
        raise YankError(f"login returned HTTP {status}")
    location = headers.get("Location") or headers.get("location") or ""
    url = urllib.parse.urljoin(f"{PYPI}/account/login/", location)
    if "/two-factor/" in url or "two-factor" in url:
        submit_two_factor(session, url)
    status, url, page = session.follow(f"{PYPI}/manage/projects/")
    if status != 200 or "manage/projects" not in url:
        raise YankError("login did not land on the manage projects page")


def submit_two_factor(session: Session, url: str) -> None:
    for attempt in range(3):
        status, _, page = session.follow(url)
        if status != 200:
            raise YankError(f"two-factor page returned HTTP {status}")
        form = find_form(parse_forms(page), "totp_value")
        if form is None:
            raise YankError("two-factor page has no TOTP form")
        code = getpass.getpass("PyPI TOTP code: ").strip()
        status, _, page = session.request(
            "POST", urllib.parse.urljoin(PYPI, form["action"]),
            {**form["inputs"], "totp_value": code},
        )
        if status in (301, 302, 303):
            return
        print(f"two-factor POST returned HTTP {status}: {body_excerpt(page)}",
              file=sys.stderr)
        print("Two-factor rejected; retry.", file=sys.stderr)
    raise YankError("two-factor authentication failed three times")


def reauthenticate(session: Session, page: str) -> None:
    """Submit the re-auth form PyPI renders when a yank needs a password."""
    forms = parse_forms(page)
    if not needs_reauth(forms):
        raise YankError("expected a re-auth form but none was found")
    form = find_form(forms, "password")
    for attempt in range(3):
        status, _, page = session.request(
            "POST", urllib.parse.urljoin(PYPI, form["action"]),
            {**form["inputs"],
             "password": getpass.getpass("PyPI password (re-auth): ")},
        )
        if status in (301, 302, 303):
            return
        print(f"re-auth POST returned HTTP {status}: {body_excerpt(page)}",
              file=sys.stderr)
        print("Re-auth rejected; retry.", file=sys.stderr)
    raise YankError("re-authentication failed three times")


def release_yanked(session: Session, project: str, version: str) -> bool:
    """True when the DB-backed manage page shows the un-yank form (or the
    release is gone). The JSON API is backed by the search index and can
    lag a successful yank; the manage release page is served from the
    database, so it is authoritative.
    """
    url = f"{PYPI}/manage/project/{project}/release/{version}/"
    for attempt in range(VERIFY_TRIES):
        status, _, page = session.follow(url)
        forms = parse_forms(page)
        if status == 404:
            return True
        if status != 200:
            raise YankError(
                f"verification for {version} returned HTTP {status}"
            )
        if is_login_page(forms):
            raise YankError(
                f"session expired while verifying {version}; "
                "re-run the script (already-yanked versions are skipped)"
            )
        if find_form(forms, "confirm_yank_version") is None:
            return True
        time.sleep(VERIFY_WAIT)
    return False


def yank_post_body(form: dict, version: str, reason: str) -> dict:
    """The POST body the yank view needs: the form's own hidden fields, the
    typed version, and the optional reason pip shows to pinned installers."""
    body = dict(form["inputs"])
    body["confirm_yank_version"] = version
    body["yanked_reason"] = reason
    return body


def yank_redirects_back(location: str, project: str, version: str) -> bool:
    """True when a 3xx sends us back to the release page instead of away.

    The yank view answers refusals (missing or mismatched confirmation
    version, quarantine) with HTTPSeeOther to the same release page
    (carrying a flash error), while success goes to the project releases
    list. A redirect back therefore means nothing was yanked.
    """
    path = urllib.parse.urlsplit(location).path
    return path.rstrip("/") == f"/manage/project/{project}/release/{version}"


def is_login_page(forms: list[dict]) -> bool:
    """True when the page is the login form rather than a manage page.

    Used by verification to tell "the session died and we were bounced to
    the login page" apart from "the release is still yankable".
    """
    form = find_form(forms, "password")
    return form is not None and "next_route" not in form["inputs"]


def yank_version(session: Session, project: str, version: str,
                 reason: str) -> None:
    """Yank one release via its manage page, mirroring the browser."""
    page_url = f"{PYPI}/manage/project/{project}/release/{version}/"
    for attempt in range(2):
        status, _, page = session.follow(page_url)
        if status != 200:
            raise YankError(f"manage page for {version} returned HTTP {status}")
        forms = parse_forms(page)
        if already_yanked(forms):
            print(f"{version} is already yanked; skipping")
            return
        form = find_form(forms, "confirm_yank_version")
        if form is None:
            raise YankError(
                f"manage page for {version} has no yank form "
                "(already yanked, quarantined, or the page changed)"
            )
        status, headers, page = session.request(
            "POST", urllib.parse.urljoin(PYPI, form["action"]),
            yank_post_body(form, version, reason),
        )
        if status in (301, 302, 303):
            location = headers.get("Location") or headers.get("location") or ""
            if yank_redirects_back(location, project, version):
                # The server refused: follow the bounce once so the flash
                # error it planted in the session lands in the page body.
                _, _, page = session.follow(urllib.parse.urljoin(PYPI, location))
                raise YankError(
                    f"PyPI refused to yank {version}: {body_excerpt(page)}"
                )
            break
        if needs_reauth(parse_forms(page)):
            reauthenticate(session, page)
            continue  # now re-fetch the manage page and re-POST the yank
        raise YankError(
            f"yank for {version} was refused "
            f"(HTTP {status}, no re-auth form): {body_excerpt(page)}"
        )
    else:
        raise YankError(f"yank for {version} never went through")
    if not release_yanked(session, project, version):
        raise YankError(
            f"{version} still renders the yank form after yanking; aborting"
        )
    print(f"yanked {version}")
    time.sleep(YANK_PAUSE)


def fetch_releases(project: str, from_json: Optional[str]) -> dict:
    if from_json:
        try:
            data = json.loads(Path(from_json).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise YankError(f"cannot read {from_json}: {exc}") from exc
    else:
        try:
            with urllib.request.urlopen(
                urllib.request.Request(
                    f"{PYPI}/pypi/{project}/json",
                    headers={"User-Agent": USER_AGENT},
                ),
                timeout=30,
            ) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.URLError as exc:
            raise YankError(f"cannot fetch {project} from PyPI: {exc}") from exc
    releases = data.get("releases")
    if not isinstance(releases, dict):
        raise YankError(f"{project} JSON has no releases map")
    return releases


def default_keep() -> str:
    """The keep version: the project's own current version, so a release
    run never yanks the release it was cut from. Fails loudly when the
    version cannot be read (no silent fallback)."""
    pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        return str(data["project"]["version"])
    except (OSError, tomllib.TOMLDecodeError, KeyError) as exc:
        raise YankError(
            f"cannot read [project].version from {pyproject}: {exc}"
        ) from exc


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="pypi_yank",
        description="Yank PyPI releases, except the keep versions.",
    )
    parser.add_argument("--project", default=DEFAULT_PROJECT,
                        help=f"PyPI project name (default: {DEFAULT_PROJECT})")
    parser.add_argument("--keep", default=None,
                        help="comma-separated versions to keep "
                             "(default: the project's own version)")
    parser.add_argument("--reason", default="",
                        help="yanked_reason stored on PyPI and displayed "
                             "by pip to users installing the yanked pin")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the plan and yank nothing")
    parser.add_argument("--from-json", metavar="FILE",
                        help="read the release list from a saved JSON dump "
                             "instead of the live API (planning and tests)")
    args = parser.parse_args(argv)

    try:
        keep = args.keep if args.keep is not None else default_keep()
        releases = fetch_releases(args.project, args.from_json)
        print(plan_text(releases, keep, args.project))
        if args.dry_run:
            print("(dry run - nothing yanked)")
            return 0
        versions = versions_to_yank(releases, keep)
        if not versions:
            print("Nothing to yank.")
            return 0
        confirm = input(
            f"Yank {len(versions)} releases from "
            f"{args.project}? Type the project name to confirm: "
        ).strip()
        if confirm != args.project:
            raise YankError(f"confirmation {confirm!r} != project name; aborting")
        session = Session()
        login(session)
        for version in versions:
            yank_version(session, args.project, version, args.reason)
        print(f"Done. {len(versions)} releases yanked; keep: {keep}.")
        return 0
    except (YankError, KeyboardInterrupt) as exc:
        print(f"pypi_yank: error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
