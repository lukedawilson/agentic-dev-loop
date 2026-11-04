"""Plan document loader + selection-flow tests."""

import argparse
from pathlib import Path

import pytest

from agentic_dev_loop.engine.loaders import (
    PlanDocumentLoader, mark_plan_completed, resolve_plan_docs, select_loader,
)
from agentic_dev_loop.engine.registry import Registry, default_registry


class _Loader:
    def __init__(self, name, option, dest, defaultable=False):
        self.name = name
        self.option = option
        self.dest = dest
        self.defaultable = defaultable

    def add_cli_args(self, parser):
        parser.add_argument(self.option, dest=self.dest, default=None)

    def resolve(self, args):  # pragma: no cover
        raise NotImplementedError


def _args(**kw):
    return argparse.Namespace(**kw)


class TestResolvePlanDocs:
    def test_loads_plan_file_keyed_by_filename(self, tmp_path):
        plan = tmp_path / "001-scan-crawl.md"
        plan.write_text("# Plan\nDo the things.", encoding="utf-8")

        docs = resolve_plan_docs(plan)

        assert docs == {"001-scan-crawl.md": "# Plan\nDo the things."}

    def test_accepts_string_path(self, tmp_path):
        plan = tmp_path / "plan.md"
        plan.write_text("# Plan", encoding="utf-8")

        assert resolve_plan_docs(str(plan)) == {"plan.md": "# Plan"}

    def test_raises_for_missing_plan(self, tmp_path):
        """Missing plan file must fail fast - no silent fallback to empty docs."""
        with pytest.raises(FileNotFoundError, match="plan"):
            resolve_plan_docs(tmp_path / "nonexistent.md")


class TestPlanDocumentLoader:
    def test_contributes_plan_flag(self):
        reg = Registry()
        reg.add_document_loader(PlanDocumentLoader())
        assert reg.cli_option_owners["--plan"] == "plan"
        assert reg.cli_dests["plan"] == "plan"

    def test_resolve_returns_docs_and_plan_subject(self, tmp_path):
        plan = tmp_path / "009-export.md"
        plan.write_text("# Export", encoding="utf-8")

        docs, subject = PlanDocumentLoader().resolve(_args(plan=plan))

        assert docs == {"009-export.md": "# Export"}
        assert subject == "plan 009-export.md"

    def test_default_registry_preregisters_plan_loader(self):
        reg = default_registry()
        assert [l.name for l in reg.document_loaders] == ["plan"]


class TestSelectLoader:
    def _registry_with(self, *loaders):
        reg = Registry()
        for loader in loaders:
            reg.add_document_loader(loader)
        return reg

    def test_flag_selects_its_loader(self):
        plan = _Loader("plan", "--plan", "plan")
        aidlc = _Loader("ai-dlc", "--aidlc-unit", "aidlc_unit", defaultable=True)
        reg = self._registry_with(plan, aidlc)

        assert select_loader(reg, _args(plan=Path("p.md"), aidlc_unit=None)) is plan
        assert select_loader(reg, _args(plan=None, aidlc_unit="U3")) is aidlc

    def test_flags_from_two_loaders_error_naming_both(self):
        reg = self._registry_with(
            _Loader("plan", "--plan", "plan"),
            _Loader("ai-dlc", "--aidlc-unit", "aidlc_unit", defaultable=True))

        with pytest.raises(ValueError) as excinfo:
            select_loader(reg, _args(plan=Path("p.md"), aidlc_unit="U3"))

        message = str(excinfo.value)
        assert "plan" in message and "ai-dlc" in message

    def test_sole_defaultable_autoruns_with_no_flag(self):
        aidlc = _Loader("ai-dlc", "--aidlc-unit", "aidlc_unit", defaultable=True)
        reg = self._registry_with(_Loader("plan", "--plan", "plan"), aidlc)

        assert select_loader(reg, _args(plan=None, aidlc_unit=None)) is aidlc

    def test_none_defaultable_returns_none_with_no_flag(self):
        reg = self._registry_with(_Loader("plan", "--plan", "plan"))

        assert select_loader(reg, _args(plan=None)) is None

    def test_multiple_defaultables_error(self):
        reg = self._registry_with(
            _Loader("a", "--a", "a", defaultable=True),
            _Loader("b", "--b", "b", defaultable=True))

        with pytest.raises(ValueError, match="[Dd]efaultable"):
            select_loader(reg, _args(a=None, b=None))


class TestMarkPlanCompleted:
    def _write(self, tmp_path, text):
        plan = tmp_path / "plan.md"
        plan.write_text(text, encoding="utf-8")
        return plan

    @pytest.mark.parametrize("status_line", [
        "> **Status: COMPLETED** (2026-10-23)",
        "> **Status: DONE**",
        "Status: COMPLETED - shipped",
        "**Status:** COMPLETED (2026-10-10)",
        "- **Status**: COMPLETED - all stages done",
    ])
    def test_replaces_existing_status_line(self, tmp_path, status_line):
        plan = self._write(tmp_path, f"# Plan\n\n{status_line}\n\nBody\n")

        mark_plan_completed(plan)

        assert plan.read_text(encoding="utf-8") == "# Plan\n\n> **Status: COMPLETED**\n\nBody\n"

    def test_replaces_inline_bold_status_line_entirely(self, tmp_path):
        """The 013-style line with trailing prose is replaced wholesale."""
        plan = self._write(
            tmp_path,
            "# Plan\n\n"
            "**Status**: Completed - shipped: ... **Status: COMPLETED** (2026-10-23).\n\n"
            "Body\n")

        mark_plan_completed(plan)

        assert plan.read_text(encoding="utf-8") == "# Plan\n\n> **Status: COMPLETED**\n\nBody\n"

    def test_inserts_after_title_when_no_status(self, tmp_path):
        plan = self._write(tmp_path, "# Plan\n\nBody\n")

        mark_plan_completed(plan)

        assert plan.read_text(encoding="utf-8") == "# Plan\n\n> **Status: COMPLETED**\n\nBody\n"

    def test_inserts_at_top_when_no_title(self, tmp_path):
        plan = self._write(tmp_path, "THE PLAN")

        mark_plan_completed(plan)

        assert plan.read_text(encoding="utf-8") == "> **Status: COMPLETED**\n\nTHE PLAN"

    def test_title_without_blank_separator_still_gets_separators(self, tmp_path):
        plan = self._write(tmp_path, "# Plan\nBody\n")

        mark_plan_completed(plan)

        assert plan.read_text(encoding="utf-8") == "# Plan\n\n> **Status: COMPLETED**\n\nBody\n"

    def test_status_heading_is_not_treated_as_status_line(self, tmp_path):
        plan = self._write(tmp_path, "# Plan\n\n## Status history\n\nBody\n")

        mark_plan_completed(plan)

        assert plan.read_text(encoding="utf-8") == (
            "# Plan\n\n> **Status: COMPLETED**\n\n## Status history\n\nBody\n")

    def test_deep_status_line_is_ignored_and_stamp_inserted_after_title(self, tmp_path):
        """Only the top of the doc is scanned; a status buried beyond the
        first ten lines is not the 'existing status recorded' - the stamp
        goes in after the title and the deep line stays untouched."""
        filler = "\n".join(f"line {i}" for i in range(1, 15))
        plan = self._write(tmp_path,
                           f"# Plan\n\n{filler}\n> **Status: IN PROGRESS**\n")

        mark_plan_completed(plan)

        text = plan.read_text(encoding="utf-8")
        assert text.startswith("# Plan\n\n> **Status: COMPLETED**\n\n")
        assert "> **Status: IN PROGRESS**" in text

    def test_idempotent_rerun(self, tmp_path):
        plan = self._write(tmp_path, "# Plan\n\n> **Status: DONE**\n\nBody\n")

        mark_plan_completed(plan)
        once = plan.read_text(encoding="utf-8")
        mark_plan_completed(plan)

        assert once == "# Plan\n\n> **Status: COMPLETED**\n\nBody\n"
        assert plan.read_text(encoding="utf-8") == once

    def test_preserves_trailing_newline_absence(self, tmp_path):
        plan = self._write(tmp_path, "# Plan\n\nBody")  # no trailing newline

        mark_plan_completed(plan)

        assert plan.read_text(encoding="utf-8") == "# Plan\n\n> **Status: COMPLETED**\n\nBody"

    def test_preserves_trailing_newline(self, tmp_path):
        plan = self._write(tmp_path, "# Plan\n\nBody\n")

        mark_plan_completed(plan)

        assert plan.read_text(encoding="utf-8") == "# Plan\n\n> **Status: COMPLETED**\n\nBody\n"

    def test_accepts_string_path(self, tmp_path):
        plan = self._write(tmp_path, "THE PLAN")

        mark_plan_completed(str(plan))

        assert plan.read_text(encoding="utf-8") == "> **Status: COMPLETED**\n\nTHE PLAN"
