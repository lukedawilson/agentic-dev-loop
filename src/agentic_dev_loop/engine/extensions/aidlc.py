"""AI-DLC document-loader extension. Loading IS choosing: name
`agentic_dev_loop.engine.extensions.aidlc:AidlcExtension` in the pipeline's
`extensions:` list and the pipeline gains `--aidlc-unit` - the unit
directory name under `aidlc-docs/construction/` (e.g. `unit-3`,
`support-for-windows`), matched verbatim - plus bare-run autodetect of
the current unit from the Current Stage line of
`aidlc-docs/aidlc-state.md`.

No input mangling. AI-DLC's own ruleset imposes no naming convention on
unit directories (`{unit-name}` is a free placeholder), so the extension
imposes none either: the slug is the directory name, exactly. `U3`, `3`,
`004` and friends are NOT accepted unless a directory is literally named
that. The Current Stage line names the unit as its directory name
("Unit unit-3", "Unit support-for-windows") for autodetect.
"""

import argparse
import re
from pathlib import Path

_DOC_SUFFIXES = (".md", ".yaml", ".yml", ".txt")


def resolve_unit_docs_with_sources(
        docs_dir: Path | str, unit_slug: str
        ) -> tuple[dict[str, str], dict[str, Path]]:
    """Resolve one AI-DLC unit into (docs, sources). ``docs`` maps each
    doc's workspace-relative key to its content; ``sources`` maps the same
    key to its on-disk file so the brief regeneration gate can re-read it
    (033 D4)."""
    docs: dict[str, str] = {}
    sources: dict[str, Path] = {}
    docs_dir = Path(docs_dir)
    unit_slug = unit_slug.strip()

    construction_dir = docs_dir / "construction"
    unit_dir: Path | None = None
    if construction_dir.is_dir():
        for entry in sorted(construction_dir.iterdir()):
            if entry.is_dir() and entry.name == unit_slug:
                unit_dir = entry
                break

    if unit_dir is None:
        available = ""
        if construction_dir.is_dir():
            names = sorted(p.name for p in construction_dir.iterdir() if p.is_dir())
            available = f" (available: {', '.join(names)})"
        raise FileNotFoundError(
            f"No unit directory named '{unit_slug}' under {construction_dir}"
            f"{available or ' (no unit directories found)'}")

    for file in sorted(unit_dir.rglob("*")):
        if file.is_file() and file.suffix in _DOC_SUFFIXES:
            key = file.relative_to(unit_dir).as_posix()
            docs[key] = file.read_text(encoding="utf-8")
            sources[key] = file

    # At this point docs holds only unit-dir files - an empty load means the
    # pipeline would build context from inception docs alone (the 2026-07-19
    # U3 silent-degradation bug). Fail fast instead.
    if not any(not k.startswith("inception/") for k in docs):
        raise RuntimeError(
            f"Unit dir {unit_dir} matched but yielded no docs - "
            "refusing to build context from inception docs alone")

    # Namespaced keys so an inception doc can never silently overwrite a
    # unit doc with the same filename.
    inception = docs_dir / "inception" / "application-design"
    if inception.is_dir():
        for file in sorted(inception.iterdir()):
            if file.is_file() and file.suffix in (".md", ".txt"):
                docs[f"inception/{file.name}"] = file.read_text(encoding="utf-8")
                sources[f"inception/{file.name}"] = file

    # The unit's code-generation plans are the build spec - absent is fine,
    # present is how the spec reaches agents.
    plans_dir = docs_dir / "construction" / "plans"
    if plans_dir.is_dir():
        for file in sorted(plans_dir.glob(f"{unit_slug}-*-code-generation-plan.md")):
            docs[f"plans/{file.name}"] = file.read_text(encoding="utf-8")
            sources[f"plans/{file.name}"] = file

    return docs, sources


def resolve_unit_docs(docs_dir: Path | str, unit_slug: str) -> dict[str, str]:
    docs, _ = resolve_unit_docs_with_sources(docs_dir, unit_slug)
    return docs


def get_current_unit(docs_dir: Path | str) -> str:
    """Read the current unit slug from the aidlc-state.md Current Stage line.

    The stage line must name the unit as its directory name ("Unit unit-3",
    "Unit support-for-windows"); the slug is returned verbatim for a direct
    directory match.
    """
    state_file = Path(docs_dir) / "aidlc-state.md"
    if not state_file.is_file():
        raise FileNotFoundError(f"aidlc-state file not found: {state_file}")
    # Anchor to the Current Stage line - the file accumulates history, so an
    # unanchored search could silently pick up an earlier "Unit N" mention.
    match = re.search(r"Current Stage[^\n]*Unit ([\w-]+(?:\.[\w-]+)*)", state_file.read_text(encoding="utf-8"))
    if not match:
        raise RuntimeError(
            f"Could not determine current unit from {state_file} "
            "(no 'Unit <slug>' in the Current Stage line)")
    return match.group(1)


class AidlcDocumentLoader:
    """`--aidlc-unit` (a unit directory name, matched verbatim), or bare-run
    autodetect from the Current Stage line of aidlc-state.md."""

    name = "ai-dlc"
    defaultable = True

    def __init__(self, docs_dir: Path | str = "aidlc-docs"):
        self.docs_dir = Path(docs_dir)

    def add_cli_args(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--aidlc-unit", metavar="SLUG", default=None,
                        help="AI-DLC unit: the unit directory name under "
                             "aidlc-docs/construction/ (e.g. unit-3 or "
                             "support-for-windows), matched verbatim; "
                             "defaults to the current unit in "
                             "aidlc-docs/aidlc-state.md")

    def resolve(self, args: argparse.Namespace) -> tuple[dict[str, str], str]:
        unit = args.aidlc_unit or get_current_unit(self.docs_dir)
        return resolve_unit_docs(self.docs_dir, unit), f"unit {unit}"

    def sources(self, args: argparse.Namespace) -> dict[str, Path]:
        """Source path for every resolved doc key (033): the regeneration
        gate re-reads these from disk so a mid-run unit-plan edit is detected."""
        unit = args.aidlc_unit or get_current_unit(self.docs_dir)
        _, sources = resolve_unit_docs_with_sources(self.docs_dir, unit)
        return sources


class AidlcExtension:
    name = "ai-dlc"

    def register(self, registry) -> None:
        registry.add_document_loader(AidlcDocumentLoader())
