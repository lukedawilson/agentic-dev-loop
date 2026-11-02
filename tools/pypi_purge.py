#!/usr/bin/env python3
"""Purge PyPI releases - the same requests the pypi.org web UI makes.

PyPI has no public API for deleting releases: the JSON API is read-only
and there is no documented delete or yank endpoint. Project owners can
delete releases only through the website, which is plain HTML forms over
warehouse routes. This script logs in the way a browser does and submits
the same "Delete release" form the UI submits, once per version.

The flow, per version:
1. GET the manage release page and parse the delete form.
2. POST the form with confirm_delete_version=<version>.
3. PyPI demands a re-authentication (password, and a TOTP code when the
   account has 2FA) for destructive actions once per 30 minutes: the POST
   comes back with the re-auth form instead of a redirect. Submit it once
   and the whole remaining batch goes through.
4. The delete POST must carry the five acknowledgment checkboxes the form
   renders (warehouse refuses without them, bouncing back to the release
   page with a flash error). A redirect back to the release page is
   treated as a refusal and reported loudly with the flash text.
5. Verify against the DB-backed manage page that the release is actually
   gone before touching the next one; a failed verification aborts loudly.

Safety:
- --dry-run fetches the release list and prints the plan, deleting nothing.
- The live run demands the project name typed at the prompt before the
  first delete.
- Keep versions absent from the release list are noted in the plan and
  skipped; the run proceeds. Nothing left to protect is not an error.
- Credentials are read from the terminal (getpass) and never stored.
- Deleted versions can never be re-uploaded to PyPI: this script cannot
  undo that, so it refuses to act until you confirm.

Usage:
    python tools/pypi_purge.py --dry-run          # plan only, live PyPI list
    python tools/pypi_purge.py --from-json dump.json --dry-run   # offline plan
    python tools/pypi_purge.py                    # the real thing
"""

from __future__ import annotations

import argparse
import getpass
import html
import html.parser
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from http.cookiejar import CookieJar
from pathlib import Path
from typing import Optional

from packaging.version import Version

PYPI = "https://pypi.org"
DEFAULT_PROJECT = "agentic-dev-loop"
DEFAULT_KEEP = "0.3.0"
USER_AGENT = "agentic-dev-loop pypi-purge (maintainer script)"
VERIFY_TRIES = 3
VERIFY_WAIT = 2.0
DELETE_PAUSE = 0.5

#: Acknowledgments the user must check to delete a release, mirroring
#: warehouse.utils.project.DELETE_RELEASE_ACKNOWLEDGMENTS. The server
#: refuses the delete (303 back to the release page with a flash error)
#: unless every one of these fields is present and truthy in the POST.
DELETE_RELEASE_ACKNOWLEDGMENTS = (
    "acknowledge_delete_files",
    "acknowledge_install_break",
    "acknowledge_no_reupload",
    "acknowledge_irreversible",
    "acknowledge_admins_cannot_undo",
)


class PurgeError(Exception):
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


def versions_to_delete(releases: dict, keep) -> list[str]:
    """Every version with at least one file except the keeps, oldest first.

    Keep versions absent from the release list are simply nothing to
    protect: they are noted in the plan and deletion proceeds.
    """
    keeps = split_keeps(keep)
    versions = [v for v in releases if releases[v] and v not in keeps]
    return sorted(versions, key=Version)


def plan_text(releases: dict, keep: str, project: str) -> str:
    versions = versions_to_delete(releases, keep)
    total_files = sum(len(releases[v]) for v in versions)
    lines = [
        f"PyPI purge plan for {project}",
        f"keep: {keep}",
        f"delete: {len(versions)} releases ({total_files} files):",
    ]
    for version in missing_keeps(releases, keep):
        lines.append(f"note: keep {version!r} is not on PyPI; nothing to protect")
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
                    raise PurgeError(f"redirect without Location from {url}")
                url = urllib.parse.urljoin(url, location)
                continue
            return status, url, page
        raise PurgeError(f"too many redirects following {url}")

    def csrf(self, page: str) -> str:
        forms = parse_forms(page)
        token = None
        for form in forms:
            if "csrf_token" in form["inputs"]:
                token = form["inputs"]["csrf_token"]
        if not token:
            raise PurgeError("no csrf_token found on the page")
        return token


def login(session: Session) -> None:
    """Browser-style login, including the two-factor page when needed."""
    status, _, page = session.follow(f"{PYPI}/account/login/")
    if status != 200:
        raise PurgeError(f"login page returned HTTP {status}")
    csrf = session.csrf(page)
    username = input("PyPI username: ").strip()
    if not username:
        raise PurgeError("no username given")
    status, headers, page = session.request(
        "POST", f"{PYPI}/account/login/",
        {"csrf_token": csrf, "username": username,
         "password": getpass.getpass("PyPI password: ")},
    )
    if status not in (301, 302, 303):
        print(f"login POST returned HTTP {status}: {body_excerpt(page)}",
              file=sys.stderr)
        if "Invalid username or password" in page or status == 200:
            raise PurgeError("login failed (bad credentials)")
        raise PurgeError(f"login returned HTTP {status}")
    location = headers.get("Location") or headers.get("location") or ""
    url = urllib.parse.urljoin(f"{PYPI}/account/login/", location)
    if "/two-factor/" in url or "two-factor" in url:
        submit_two_factor(session, url)
    status, url, page = session.follow(f"{PYPI}/manage/projects/")
    if status != 200 or "manage/projects" not in url:
        raise PurgeError("login did not land on the manage projects page")


def submit_two_factor(session: Session, url: str) -> None:
    for attempt in range(3):
        status, _, page = session.follow(url)
        if status != 200:
            raise PurgeError(f"two-factor page returned HTTP {status}")
        form = find_form(parse_forms(page), "totp_value")
        if form is None:
            raise PurgeError("two-factor page has no TOTP form")
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
    raise PurgeError("two-factor authentication failed three times")


def reauthenticate(session: Session, page: str) -> None:
    """Submit the re-auth form PyPI renders when a delete needs a password."""
    forms = parse_forms(page)
    if not needs_reauth(forms):
        raise PurgeError("expected a re-auth form but none was found")
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
    raise PurgeError("re-authentication failed three times")


def release_gone(session: Session, project: str, version: str) -> bool:
    """True when the DB-backed manage page 404s for the release.

    The JSON API is backed by the search index and can lag a successful
    delete; the manage release page is served from the database, so a 404
    there is authoritative.
    """
    url = f"{PYPI}/manage/project/{project}/release/{version}/"
    for attempt in range(VERIFY_TRIES):
        status, _, page = session.follow(url)
        if status == 404:
            return True
        if status != 200:
            raise PurgeError(
                f"verification for {version} returned HTTP {status}"
            )
        if is_login_page(parse_forms(page)):
            raise PurgeError(
                f"session expired while verifying {version}; "
                "re-run the script (already-deleted versions are skipped)"
            )
        time.sleep(VERIFY_WAIT)
    return False


def delete_post_body(form: dict, version: str) -> dict:
    """The exact POST body the delete form needs: the form's own hidden
    fields, the typed version, and every acknowledgment checkbox checked."""
    body = dict(form["inputs"])
    body["confirm_delete_version"] = version
    for field in DELETE_RELEASE_ACKNOWLEDGMENTS:
        body[field] = "on"
    return body


def delete_redirects_back(location: str, project: str, version: str) -> bool:
    """True when a 3xx sends us back to the release page instead of away.

    The delete view answers refusals with HTTPSeeOther to the same release
    page (carrying a flash error), while success goes to the project
    releases list. A redirect back therefore means nothing was deleted.
    """
    path = urllib.parse.urlsplit(location).path
    return path.rstrip("/") == f"/manage/project/{project}/release/{version}"


def is_login_page(forms: list[dict]) -> bool:
    """True when the page is the login form rather than a manage page.

    Used by verification to tell "the session died and we were bounced to
    the login page" apart from "the release is still there".
    """
    form = find_form(forms, "password")
    return form is not None and "next_route" not in form["inputs"]


def delete_version(session: Session, project: str, version: str) -> None:
    """Delete one release via its manage page, mirroring the browser."""
    page_url = f"{PYPI}/manage/project/{project}/release/{version}/"
    for attempt in range(2):
        status, _, page = session.follow(page_url)
        if status != 200:
            raise PurgeError(f"manage page for {version} returned HTTP {status}")
        form = find_form(parse_forms(page), "confirm_delete_version")
        if form is None:
            raise PurgeError(
                f"manage page for {version} has no delete form"
            )
        status, headers, page = session.request(
            "POST", urllib.parse.urljoin(PYPI, form["action"]),
            delete_post_body(form, version),
        )
        if status in (301, 302, 303):
            location = headers.get("Location") or headers.get("location") or ""
            if delete_redirects_back(location, project, version):
                # The server refused: follow the bounce once so the flash
                # error it planted in the session lands in the page body.
                _, _, page = session.follow(urllib.parse.urljoin(PYPI, location))
                raise PurgeError(
                    f"PyPI refused to delete {version}: {body_excerpt(page)}"
                )
            break
        if needs_reauth(parse_forms(page)):
            reauthenticate(session, page)
            continue  # now re-fetch the manage page and re-POST the delete
        raise PurgeError(
            f"delete for {version} was refused "
            f"(HTTP {status}, no re-auth form): {body_excerpt(page)}"
        )
    else:
        raise PurgeError(f"delete for {version} never went through")
    if not release_gone(session, project, version):
        raise PurgeError(
            f"{version} is still listed on PyPI after deletion; aborting"
        )
    print(f"deleted {version}")
    time.sleep(DELETE_PAUSE)


def fetch_releases(project: str, from_json: Optional[str]) -> dict:
    if from_json:
        try:
            data = json.loads(Path(from_json).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise PurgeError(f"cannot read {from_json}: {exc}") from exc
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
            raise PurgeError(f"cannot fetch {project} from PyPI: {exc}") from exc
    releases = data.get("releases")
    if not isinstance(releases, dict):
        raise PurgeError(f"{project} JSON has no releases map")
    return releases


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="pypi_purge",
        description="Permanently delete PyPI releases, except one keep version.",
    )
    parser.add_argument("--project", default=DEFAULT_PROJECT,
                        help=f"PyPI project name (default: {DEFAULT_PROJECT})")
    parser.add_argument("--keep", default=DEFAULT_KEEP,
                        help="comma-separated versions to keep "
                             f"(default: {DEFAULT_KEEP})")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the plan and delete nothing")
    parser.add_argument("--from-json", metavar="FILE",
                        help="read the release list from a saved JSON dump "
                             "instead of the live API (planning and tests)")
    args = parser.parse_args(argv)

    try:
        releases = fetch_releases(args.project, args.from_json)
        print(plan_text(releases, args.keep, args.project))
        if args.dry_run:
            print("(dry run - nothing deleted)")
            return 0
        versions = versions_to_delete(releases, args.keep)
        if not versions:
            print("Nothing to delete.")
            return 0
        confirm = input(
            f"Permanently delete {len(versions)} releases from "
            f"{args.project}? Type the project name to confirm: "
        ).strip()
        if confirm != args.project:
            raise PurgeError(f"confirmation {confirm!r} != project name; aborting")
        session = Session()
        login(session)
        for version in versions:
            delete_version(session, args.project, version)
        print(f"Done. {len(versions)} releases deleted; {args.keep} remains.")
        return 0
    except (PurgeError, KeyboardInterrupt) as exc:
        print(f"pypi_purge: error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
