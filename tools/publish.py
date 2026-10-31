#!/usr/bin/env python3
"""Release-cut script for /publish - the only place release files get edited.

Bumps `pyproject.toml` [project].version (the single source of truth),
updates CHANGELOG.md, commits all pending changes, tags v<version>, and
pushes the current branch plus the tag. The v* tag push triggers
.github/workflows/release.yml (test suite → PyPI trusted publishing →
GitHub Release), so pushing the tag is the publish action itself.

Invariants (enforced here, not by convention):
- tag == pyproject version == changelog heading (the release workflow
  extracts the changelog section by tag name)
- a version can only be cut once: same version is allowed only while the
  tag v<version> does not exist yet
- a release entry that references a consumer-adoption plan must carry a
  `### Consumer adoption` subsection (registry CONSUMER_ADOPTION_PLANS
  below); without it the release is refused
- any failure leaves the repository unchanged and exits non-zero, loudly
"""

from __future__ import annotations

import argparse
import datetime
import re
import subprocess
import sys
import tomllib
from pathlib import Path

from packaging.version import InvalidVersion, Version


class PublishError(Exception):
    """A loud, user-facing failure. The message is complete on its own."""


# Plan slugs whose completion obligates consumer repos to edit their own
# pipeline.yaml or agent prompt copies (verdict vocabulary, step caps,
# banned commands, qa prompt sections). A release entry that references
# one of these must carry a `### Consumer adoption` subsection. When a new
# plan adds consumer obligations, add its slug here.
CONSUMER_ADOPTION_PLANS = {
    "-qa-unverifiable-verdict": "plan 024",
    "-qa-environmental-verdict": "plan 031",
    "-finalize-nudge-and-verdict-cap": "plan 035",
    "-banned-commands-and-lane-boundary": "plan 036",
    "-network-level-egress-enforcement": "plan 038",
    "-egress-proxy-runtime-hardening": "plan 039",
}


def enforce_consumer_adoption(section: str) -> None:
    """Refuse a release section that references a consumer-adoption plan
    without a `### Consumer adoption` note."""
    if "### Consumer adoption" in section:
        return
    for marker, plan in CONSUMER_ADOPTION_PLANS.items():
        if marker in section:
            raise PublishError(
                f"the release entry references {plan}, which requires "
                "consumer repos to edit their pipeline.yaml or agent prompt "
                "copies: add a `### Consumer adoption` subsection to the "
                "entry naming exactly what consumer repos must change "
                "(see the plan's scope and sections) and re-run"
            )


def git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True
    )
    if proc.returncode != 0:
        raise PublishError(proc.stderr.strip() or f"git {' '.join(args)} failed")
    return proc.stdout


def repo_root(override: Path | None) -> Path:
    """Resolve the git repository root, failing loudly when there is none."""
    start = override.resolve() if override is not None else Path.cwd()
    proc = subprocess.run(
        ["git", "-C", str(start), "rev-parse", "--show-toplevel"],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise PublishError(f"{start} is not inside a git repository")
    return Path(proc.stdout.strip())


def current_version(pyproject: Path) -> Version:
    if not pyproject.is_file():
        raise PublishError(f"{pyproject} not found")
    try:
        raw = tomllib.loads(pyproject.read_text())["project"]["version"]
    except (OSError, tomllib.TOMLDecodeError, KeyError) as exc:
        raise PublishError(
            f"cannot read [project].version from {pyproject}: {exc}"
        ) from exc
    try:
        return Version(raw)
    except InvalidVersion as exc:
        raise PublishError(
            f"[project].version {raw!r} is not a valid PEP 440 version"
        ) from exc


def resolve_target(current: Version, args: argparse.Namespace, root: Path) -> Version:
    """The new version per the selector; raises PublishError on any reject."""
    if args.major:
        release = current.release
        if current.pre is not None and release[1:] == (0, 0):
            target = release  # an rc of x.0.0 finalises the major
        else:
            target = (release[0] + 1, 0, 0)
        return Version(".".join(map(str, target)))
    if args.minor:
        return Version(f"{current.release[0]}.{current.release[1] + 1}.0")
    if args.patch:
        r = current.release
        return Version(f"{r[0]}.{r[1]}.{r[2] + 1}")
    # Explicit version.
    raw = args.version.lstrip("vV")
    try:
        parsed = Version(raw)
    except InvalidVersion:
        raise PublishError(f"{args.version!r} is not a valid PEP 440 version")
    if str(parsed) != raw:
        raise PublishError(
            f"{args.version!r} is not canonical PEP 440; use {str(parsed)!r}"
        )
    if parsed < current:
        raise PublishError(
            f"target {parsed} is lower than the current version {current}"
        )
    if parsed == current and git(root, "tag", "--list", f"v{raw}").strip():
        raise PublishError(f"v{raw} is already tagged; cut a different version")
    return parsed


def bump_pyproject_text(text: str, new: str) -> str:
    """Replace the single `version = "..."` line; fail loud on any anomaly."""
    pattern = re.compile(r'(?m)^version\s*=\s*"[^"]*"\s*$')
    if len(pattern.findall(text)) != 1:
        raise PublishError(
            "expected exactly one `version =` line in pyproject.toml, found "
            f"{len(pattern.findall(text))}"
        )
    out, replaced = pattern.subn(f'version = "{new}"', text, count=1)
    if replaced != 1:
        raise PublishError("failed to replace the version line")
    data = tomllib.loads(out)
    if data.get("project", {}).get("version") != new:
        raise PublishError("pyproject.toml does not parse back with the new version")
    return out


UNRELEASED_HEADING = re.compile(r"(?m)^## \[Unreleased\]\s*\n")


def render_changelog(
    old: str,
    version: str,
    date: str,
    description: str | None,
    bullets: list[str],
) -> str:
    """Insert the release section. Source priority: description > moved
    Unreleased content > git-inferred bullets. With a description, the
    existing Unreleased body is kept in place (it holds notes for the next
    release); without one, a non-empty body moves into the new section."""
    match = UNRELEASED_HEADING.search(old)
    if match is None:
        raise PublishError("CHANGELOG.md has no `## [Unreleased]` heading")
    body_start = match.end()
    next_heading = re.search(r"(?m)^## \[", old[body_start:])
    body = old[body_start:body_start + next_heading.start()] if next_heading \
        else old[body_start:]
    tail = old[body_start + len(body):]  # drop the old body from the tail
    if description is not None:
        content = f"### Changed\n\n- {description}\n\n"
        unreleased_body = body.rstrip("\n") + "\n\n" if body.strip() else ""
    elif body.strip():
        content = body.rstrip("\n") + "\n\n"
        unreleased_body = ""
    else:
        if not bullets:
            raise PublishError(
                "no description given, `## [Unreleased]` is empty, and there "
                "is no git history to infer one from; pass --description"
            )
        content = "### Changed\n\n" + "\n".join(f"- {b}" for b in bullets) + "\n\n"
        unreleased_body = ""
    enforce_consumer_adoption(content)
    new_section = f"## [{version}] - {date}\n\n{content}"
    return old[:match.start()] + "## [Unreleased]\n\n" + unreleased_body \
        + new_section + tail


def git_bullets(root: Path) -> list[str]:
    """Commit subjects since the last tag, plus a summary of uncommitted work."""
    describe = subprocess.run(
        ["git", "-C", str(root), "describe", "--tags", "--abbrev=0"],
        capture_output=True, text=True,
    )
    commit_range = f"{describe.stdout.strip()}..HEAD" if describe.returncode == 0 \
        else "HEAD"
    bullets = [
        line for line in git(root, "log", commit_range, "--pretty=format:%s")
        .splitlines() if line.strip()
    ]
    dirty = sorted({
        line[3:].strip().split(" -> ")[-1]
        for line in git(root, "status", "--porcelain").splitlines()
        if line
    })
    if dirty:
        shown = ", ".join(dirty[:10]) + (", …" if len(dirty) > 10 else "")
        bullets.append(f"Uncommitted changes: {shown}")
    return bullets


def changelog_source(
    description: str | None, unreleased_body: str, bullets: list[str]
) -> str:
    if description is not None:
        return "description"
    if unreleased_body.strip():
        return "unreleased"
    if bullets:
        return "git history"
    return "none"


def main(argv: list[str] | None = None) -> int:
    # The release-cut report prints the → arrow (and friends); cp1252
    # consoles (Windows CI) cannot encode it - pin UTF-8 explicitly instead
    # of crashing at the final print.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(
        prog="publish",
        description="Cut and publish an agentic-dev-loop release.",
    )
    selector = parser.add_mutually_exclusive_group(required=True)
    selector.add_argument(
        "--major", action="store_true",
        help="bump to the next major (finalises an rc of x.0.0)",
    )
    selector.add_argument("--minor", action="store_true",
                          help="bump to the next minor")
    selector.add_argument("--patch", action="store_true",
                          help="bump to the next patch")
    selector.add_argument(
        "version", nargs="?",
        help="explicit canonical PEP 440 version (e.g. 0.3.0, 1.0.0rc1)",
    )
    parser.add_argument("-m", "--description",
                        help="release-note text for the changelog entry")
    parser.add_argument("--cwd", type=Path,
                        help="repository root (default: current directory)")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the plan without changing anything")
    args = parser.parse_args(argv)

    try:
        root = repo_root(args.cwd)
        pyproject = root / "pyproject.toml"
        changelog_path = root / "CHANGELOG.md"
        current = current_version(pyproject)
        target = resolve_target(current, args, root)
        tag = f"v{target}"

        if not changelog_path.is_file():
            raise PublishError(f"{changelog_path} not found")
        old_changelog = changelog_path.read_text()
        heading = UNRELEASED_HEADING.search(old_changelog)
        if heading is None:
            raise PublishError("CHANGELOG.md has no `## [Unreleased]` heading")
        body_start = heading.end()
        next_heading = re.search(r"(?m)^## \[", old_changelog[body_start:])
        unreleased_body = old_changelog[body_start:body_start + next_heading.start()] \
            if next_heading else old_changelog[body_start:]

        bullets = [] if args.description is not None or unreleased_body.strip() \
            else git_bullets(root)
        source = changelog_source(args.description, unreleased_body, bullets)
        date = datetime.date.today().isoformat()
        branch = git(root, "rev-parse", "--abbrev-ref", "HEAD").strip()
        if branch == "HEAD":
            raise PublishError("HEAD is detached; check out a branch first")

        if args.dry_run:
            print(f"Current version: {current}")
            print(f"Target version:  {target} (tag {tag})")
            print(f"Changelog entry: {source}")
            print(f"Commit:          release: {tag}")
            print(f"Push:            origin {branch}, origin {tag}")
            print("(dry run - nothing changed)")
            return 0

        new_changelog = render_changelog(
            old_changelog, str(target), date, args.description, bullets)
        (pyproject).write_text(
            bump_pyproject_text(pyproject.read_text(), str(target)))
        changelog_path.write_text(new_changelog)

        git(root, "add", "-A")
        git(root, "commit", "-m", f"release: {tag}")
        git(root, "tag", "-a", tag, "-m", f"release {tag}")
        git(root, "push", "origin", branch)
        git(root, "push", "origin", tag)
    except PublishError as exc:
        print(f"publish: error: {exc}", file=sys.stderr)
        return 1

    print(f"Released {tag}: committed, tagged, pushed origin {branch} + {tag}.")
    print("The tag push triggers release.yml (test suite → PyPI → GitHub Release).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
