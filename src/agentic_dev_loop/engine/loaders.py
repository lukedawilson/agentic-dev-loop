"""Built-in `plan` document loader + the loader-selection flow."""

import argparse
import re
from pathlib import Path

from agentic_dev_loop.core.config import PipelineConfig
from .registry import DocumentLoader, Registry


def resolve_plan_docs(plan_path: Path | str) -> dict[str, str]:
    """Load a single plan doc as the pipeline's full context - plan mode is
    self-contained (no unit/inception docs; shared files like coding
    standards come from the pipeline's `additional_files:`, appended by the
    runner)."""
    path = Path(plan_path)
    if not path.is_file():
        raise FileNotFoundError(f"Plan doc not found: {path}")
    return {path.name: path.read_text(encoding="utf-8")}


# A status line at the top of a planning doc, in any recorded form:
# `> **Status: COMPLETED** (date)`, `**Status:** ...`, `- **Status**: ...`,
# or bare `Status: ...`.
_STATUS_LINE = re.compile(r"^\s*(?:>\s*)?(?:[-*]\s+)?\*{0,2}Status\*{0,2}\s*:")
# The status only counts as "recorded at the top" when it appears early -
# buried history (e.g. `## Status history` or a later `Status: ...` line)
# is not the doc's current status.
_STATUS_SCAN_LINES = 10
_STAMP = "> **Status: COMPLETED**"


def mark_plan_completed(plan_path: Path | str) -> None:
    """Stamp the plan doc as COMPLETED after a successful run.

    Replaces the first status line found within the top of the doc, or
    inserts the stamp right after the `# ` title (blank separators around
    it) - or at the very top when there is no title. Trailing-newline
    presence is preserved."""
    path = Path(plan_path)
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    trailing = "\n" if text.endswith("\n") else ""

    for idx in range(min(len(lines), _STATUS_SCAN_LINES)):
        if _STATUS_LINE.match(lines[idx]):
            lines[idx] = _STAMP
            break
    else:
        title_idx = next((i for i, line in enumerate(lines)
                          if line.startswith("# ")), None)
        insert_at = 0 if title_idx is None else title_idx + 1
        stamp_block = [_STAMP]
        if insert_at > 0 and lines[insert_at - 1] != "":
            stamp_block.insert(0, "")   # blank separator above the stamp
        if insert_at < len(lines) and lines[insert_at] != "":
            stamp_block.append("")      # blank separator below the stamp
        lines[insert_at:insert_at] = stamp_block
    path.write_text("\n".join(lines) + trailing, encoding="utf-8")


class PlanDocumentLoader:
    name = "plan"
    defaultable = False  # needs a path - never autodetects

    def add_cli_args(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--plan", type=Path, default=None,
                            help="Path to a single plan doc")

    def resolve(self, args: argparse.Namespace) -> tuple[dict[str, str], str]:
        docs = resolve_plan_docs(args.plan)
        return docs, f"plan {args.plan.name}"

    def sources(self, args: argparse.Namespace) -> dict[str, Path]:
        """Source path for every resolved doc key (033): the regeneration
        gate re-reads these from disk so a mid-run plan edit is detected."""
        path = Path(args.plan)
        return {path.name: path}


def select_loader(registry: Registry, args: argparse.Namespace) -> DocumentLoader | None:
    """The document-loader coexistence model - load many, invoke one:

    1. A loader flag present in argv (parsed value not None) selects its
       loader; other loaders are bypassed. Flags from two different loaders
       in one invocation → error naming both (genuine ambiguity).
    2. No loader flag → the sole `defaultable` loader runs (e.g. aidlc
       autodetects the current unit). Multiple defaultables → error.
    3. None defaultable → None: run with no injected docs; the subject is
       the pipeline's `name:`.

    Convention: loader flags must default to None so "present" is decidable.
    """
    invoked = {loader_name for dest, loader_name in registry.cli_dests.items()
               if getattr(args, dest, None) is not None}
    if len(invoked) > 1:
        raise ValueError(
            f"Flags from multiple document loaders in one invocation: "
            f"{sorted(invoked)}")
    if invoked:
        name = invoked.pop()
        return next(l for l in registry.document_loaders if l.name == name)
    defaultable = [l for l in registry.document_loaders if l.defaultable]
    if len(defaultable) > 1:
        raise ValueError(
            f"Multiple defaultable document loaders: "
            f"{[l.name for l in defaultable]}")
    return defaultable[0] if defaultable else None


def resolve_docs(cfg: PipelineConfig, base: Path, registry: Registry,
                 args) -> tuple[DocumentLoader | None, dict[str, str], str,
                                dict[str, str]]:
    """Select and invoke the document loader, then append the pipeline's
    additional_files (resolved relative to the pipeline file's dir;
    listed-but-missing fails fast). Returns (loader, docs, subject,
    doc_sources) - the source path for every loader/additional_files doc key
    so the brief regeneration gate can re-read them from disk (033 D4)."""
    loader = select_loader(registry, args)
    sources: dict[str, str] = {}
    if loader is not None:
        docs, subject = loader.resolve(args)
        if not docs:
            # U3 policy: an empty load silently degraded to a context the
            # agents couldn't act on. Fail fast at the call site instead.
            raise RuntimeError(
                f"Document loader {loader.name!r} returned no docs - "
                "refusing to build context from nothing")
        loader_sources = getattr(loader, "sources", None)
        if callable(loader_sources):
            for name, path in loader_sources(args).items():
                sources[name] = str(Path(path).resolve())
    else:
        docs, subject = {}, cfg.name
    for rel in cfg.additional_files:
        path = base / rel
        if not path.is_file():
            raise FileNotFoundError(f"additional_files entry not found: {path}")
        docs[path.name] = path.read_text(encoding="utf-8")
        sources[path.name] = str(path.resolve())
    return loader, docs, subject, sources
