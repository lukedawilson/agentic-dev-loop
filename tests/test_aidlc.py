"""AI-DLC document loader tests.

The loader contract: `--aidlc-unit` takes the unit DIRECTORY NAME verbatim
(`unit-3`, `support-for-windows`) and matches it by exact string equality
under `aidlc-docs/construction/`. No input mangling: `U3`, `3`, `004`,
`unit_3` and friends are NOT accepted unless a directory is literally
named that. Bare-run autodetect reads the slug from the Current Stage
line of `aidlc-docs/aidlc-state.md` (`Unit <slug>`).
"""

import argparse

import pytest

from agentic_dev_loop.engine.extensions.aidlc import (
    AidlcDocumentLoader, AidlcExtension, get_current_unit, resolve_unit_docs,
)
from agentic_dev_loop.engine.registry import Registry


class TestResolveUnitDocs:
    def test_loads_docs_for_unit(self, tmp_path):
        unit_dir = tmp_path / "construction" / "unit-2"
        unit_dir.mkdir(parents=True)
        (unit_dir / "design.md").write_text("# Design\nContent")
        (unit_dir / "tasks.md").write_text("# Tasks\nList")
        other_dir = tmp_path / "construction" / "unit-1"
        other_dir.mkdir(parents=True)
        (other_dir / "other.md").write_text("# Other")

        docs = resolve_unit_docs(tmp_path, "unit-2")

        assert "design.md" in docs
        assert "tasks.md" in docs
        assert "other.md" not in docs

    def test_u_prefixed_id_is_not_mangled(self, tmp_path):
        """The slug is the directory name - `U2` must NOT resolve `unit-2`."""
        unit_dir = tmp_path / "construction" / "unit-2"
        unit_dir.mkdir(parents=True)
        (unit_dir / "design.md").write_text("# Design")

        with pytest.raises(FileNotFoundError, match="unit-2"):
            resolve_unit_docs(tmp_path, "U2")

    def test_lowercase_u_prefixed_id_is_not_mangled(self, tmp_path):
        unit_dir = tmp_path / "construction" / "unit-3"
        unit_dir.mkdir(parents=True)
        (unit_dir / "design.md").write_text("# Design")

        with pytest.raises(FileNotFoundError, match="unit-3"):
            resolve_unit_docs(tmp_path, "u3")

    def test_raises_for_missing_unit_and_lists_available(self, tmp_path):
        """Unknown unit must fail fast - silently running the pipeline with
        zero design docs is a forbidden silent fallback. The error names the
        directories that DO exist."""
        (tmp_path / "construction" / "unit-1").mkdir(parents=True)
        (tmp_path / "construction" / "unit-2").mkdir(parents=True)

        with pytest.raises(FileNotFoundError, match="unit-99") as exc:
            resolve_unit_docs(tmp_path, "unit-99")
        assert "unit-1" in str(exc.value)
        assert "unit-2" in str(exc.value)

    def test_includes_inception_docs(self, tmp_path):
        unit_dir = tmp_path / "construction" / "unit-1"
        unit_dir.mkdir(parents=True)
        (unit_dir / "design.md").write_text("# Design")  # unit dirs may not be empty
        inception = tmp_path / "inception" / "application-design"
        inception.mkdir(parents=True)
        (inception / "architecture.md").write_text("# Arch")

        docs = resolve_unit_docs(tmp_path, "unit-1")

        assert "inception/architecture.md" in docs
        assert "Arch" in docs["inception/architecture.md"]

    def test_unit_and_inception_name_collision_kept_separate(self, tmp_path):
        """A unit doc and an inception doc with the same filename must both
        survive - inception docs are namespaced to avoid silent overwrite."""
        unit_dir = tmp_path / "construction" / "unit-1"
        unit_dir.mkdir(parents=True)
        (unit_dir / "architecture.md").write_text("unit version")
        inception = tmp_path / "inception" / "application-design"
        inception.mkdir(parents=True)
        (inception / "architecture.md").write_text("inception version")

        docs = resolve_unit_docs(tmp_path, "unit-1")

        assert docs["architecture.md"] == "unit version"
        assert docs["inception/architecture.md"] == "inception version"

    def test_loads_docs_recursively_from_subdirs(self, tmp_path):
        """Unit dirs may nest docs in subdirectories (unit-3 layout:
        functional-design/business-rules.md). Non-recursive loading silently
        injected zero unit docs - the 2026-07-19 U3 context bug."""
        unit_dir = tmp_path / "construction" / "unit-3"
        (unit_dir / "functional-design").mkdir(parents=True)
        (unit_dir / "functional-design" / "business-rules.md").write_text("# Rules")

        docs = resolve_unit_docs(tmp_path, "unit-3")

        assert docs["functional-design/business-rules.md"] == "# Rules"
        # Keys are contract addresses for agents (a Linux container): always
        # '/'-joined regardless of host OS - backslashes were the CI-red
        # KeyError on Windows.
        assert all("/" in k for k in docs)
        assert all("\\" not in k for k in docs)

    def test_raises_when_unit_dir_yields_no_docs(self, tmp_path):
        """A matched unit dir with no loadable files is a forbidden silent
        fallback - fail fast like a missing dir does."""
        (tmp_path / "construction" / "unit-3" / "functional-design").mkdir(parents=True)

        with pytest.raises(RuntimeError, match="no.*docs|zero"):
            resolve_unit_docs(tmp_path, "unit-3")

    def test_includes_code_generation_plans(self, tmp_path):
        """The unit's *-code-generation-plan.md files are the build spec."""
        unit_dir = tmp_path / "construction" / "unit-3"
        unit_dir.mkdir(parents=True)
        (unit_dir / "design.md").write_text("# Design")
        plans = tmp_path / "construction" / "plans"
        plans.mkdir(parents=True)
        (plans / "unit-3-scan-core-types-code-generation-plan.md").write_text("# Core types")
        (plans / "unit-3-scan-frontend-code-generation-plan.md").write_text("# Frontend")
        (plans / "unit-3-scan-nfr-design-plan.md").write_text("# NOT a code-gen plan")
        (plans / "unit-2-onboarding-code-generation-plan.md").write_text("# Wrong unit")

        docs = resolve_unit_docs(tmp_path, "unit-3")

        assert docs["plans/unit-3-scan-core-types-code-generation-plan.md"] == "# Core types"
        assert docs["plans/unit-3-scan-frontend-code-generation-plan.md"] == "# Frontend"
        assert not any("nfr-design" in k for k in docs)
        assert not any("unit-2" in k for k in docs)

    def test_loads_bare_slug_code_generation_plan(self, tmp_path):
        """`{slug}-code-generation-plan.md` (no infix) is the build spec -
        the repo's actual plan filenames, e.g. unit-3-code-generation-plan.md,
        must load, not only infixed variants like unit-3-scan-*."""
        unit_dir = tmp_path / "construction" / "unit-3"
        unit_dir.mkdir(parents=True)
        (unit_dir / "design.md").write_text("# Design")
        plans = tmp_path / "construction" / "plans"
        plans.mkdir(parents=True)
        (plans / "unit-3-code-generation-plan.md").write_text("# Build spec")

        docs = resolve_unit_docs(tmp_path, "unit-3")

        assert docs["plans/unit-3-code-generation-plan.md"] == "# Build spec"

    def test_missing_plans_dir_is_not_an_error(self, tmp_path):
        unit_dir = tmp_path / "construction" / "unit-2"
        unit_dir.mkdir(parents=True)
        (unit_dir / "design.md").write_text("# Design")

        assert resolve_unit_docs(tmp_path, "unit-2")["design.md"] == "# Design"


class TestDirectSlugMatching:
    """The slug IS the directory name - no spelling tolerance, no numeric
    fallback, no prefix mangling."""

    def _unit(self, tmp_path, name):
        unit_dir = tmp_path / "construction" / name
        unit_dir.mkdir(parents=True)
        (unit_dir / "design.md").write_text("# Design")
        return unit_dir

    def test_number_alone_does_not_match_slugged_dir(self, tmp_path):
        """The numeric fallback is gone: `004` must not resolve
        `unit-004-preset-management`."""
        self._unit(tmp_path, "unit-004-preset-management")
        with pytest.raises(FileNotFoundError, match="004"):
            resolve_unit_docs(tmp_path, "004")

    def test_underscore_spelling_does_not_match(self, tmp_path):
        """The `unit_<id>` variant is gone: the slug must spell the
        directory exactly."""
        self._unit(tmp_path, "unit-3")
        with pytest.raises(FileNotFoundError, match="unit-3"):
            resolve_unit_docs(tmp_path, "unit_3")


class TestGetCurrentUnit:
    def _write_state(self, tmp_path, stage_line: str):
        state = tmp_path / "aidlc-state.md"
        state.write_text(f"# AI-DLC State Tracking\n\n{stage_line}\n", encoding="utf-8")
        return state

    def test_parses_slug_from_current_stage(self, tmp_path):
        self._write_state(
            tmp_path,
            "- **Current Stage**: CONSTRUCTION - Unit unit-3 (SCAN) - Code Generation in progress.")
        assert get_current_unit(tmp_path) == "unit-3"

    def test_parses_bare_slug_from_current_stage(self, tmp_path):
        self._write_state(
            tmp_path,
            "- **Current Stage**: CONSTRUCTION - Unit support-for-windows.")
        assert get_current_unit(tmp_path) == "support-for-windows"

    def test_parses_different_slug(self, tmp_path):
        self._write_state(
            tmp_path, "- **Current Stage**: CONSTRUCTION - Unit unit-12 (REPORT).")
        assert get_current_unit(tmp_path) == "unit-12"

    def test_ignores_unit_mentions_before_the_stage_line(self, tmp_path):
        """The state file accumulates history; only the Current Stage line
        decides the unit - an earlier 'Unit N' mention must not win."""
        state = tmp_path / "aidlc-state.md"
        state.write_text(
            "# AI-DLC State Tracking\n\n"
            "- Finished Unit 9 (REPORT) last week.\n"
            "- **Current Stage**: CONSTRUCTION - Unit unit-3 (SCAN) - Code Generation.\n",
            encoding="utf-8")
        assert get_current_unit(tmp_path) == "unit-3"

    def test_stage_line_without_unit_raises_despite_earlier_mention(self, tmp_path):
        """A 'Unit N' somewhere else in the file is not a Current Stage -
        falling back to it would silently construct the wrong unit."""
        state = tmp_path / "aidlc-state.md"
        state.write_text(
            "# AI-DLC State Tracking\n\n"
            "- Previously worked on Unit 9.\n"
            "- **Current Stage**: INCEPTION - requirements gathering.\n",
            encoding="utf-8")
        with pytest.raises(RuntimeError, match="current unit"):
            get_current_unit(tmp_path)

    def test_raises_when_no_unit_in_stage_line(self, tmp_path):
        """A stage line with no unit must fail fast - no silent default."""
        self._write_state(tmp_path, "- **Current Stage**: INCEPTION - requirements.")
        with pytest.raises(RuntimeError, match="current unit"):
            get_current_unit(tmp_path)

    def test_raises_when_state_file_missing(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="aidlc-state"):
            get_current_unit(tmp_path)


class TestAidlcDocumentLoader:
    def _unit_docs(self, tmp_path):
        unit_dir = tmp_path / "construction" / "unit-3"
        unit_dir.mkdir(parents=True)
        (unit_dir / "design.md").write_text("# Design")

    def test_resolve_with_explicit_slug(self, tmp_path):
        self._unit_docs(tmp_path)
        loader = AidlcDocumentLoader(docs_dir=tmp_path)

        docs, subject = loader.resolve(argparse.Namespace(aidlc_unit="unit-3"))

        assert docs["design.md"] == "# Design"
        assert subject == "unit unit-3"

    def test_resolve_without_unit_autodetects_current(self, tmp_path):
        self._unit_docs(tmp_path)
        (tmp_path / "aidlc-state.md").write_text(
            "# State\n\n- **Current Stage**: CONSTRUCTION - Unit unit-3 (SCAN).\n",
            encoding="utf-8")
        loader = AidlcDocumentLoader(docs_dir=tmp_path)

        docs, subject = loader.resolve(argparse.Namespace(aidlc_unit=None))

        assert docs["design.md"] == "# Design"
        assert subject == "unit unit-3"

    def test_autodetect_resolves_slugged_dir(self, tmp_path):
        unit_dir = tmp_path / "construction" / "unit-004-preset-management"
        unit_dir.mkdir(parents=True)
        (unit_dir / "design.md").write_text("# Design")
        (tmp_path / "aidlc-state.md").write_text(
            "# State\n\n- **Current Stage**: CONSTRUCTION - Unit unit-004-preset-management ADL-01.\n",
            encoding="utf-8")
        loader = AidlcDocumentLoader(docs_dir=tmp_path)

        docs, subject = loader.resolve(argparse.Namespace(aidlc_unit=None))

        assert docs["design.md"] == "# Design"
        assert subject == "unit unit-004-preset-management"

    def test_extension_registers_loader_and_flag(self):
        reg = Registry()
        AidlcExtension().register(reg)

        assert [l.name for l in reg.document_loaders] == ["ai-dlc"]
        assert reg.cli_option_owners["--aidlc-unit"] == "ai-dlc"
        assert reg.cli_dests["aidlc_unit"] == "ai-dlc"
