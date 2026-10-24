"""Verdict parsing tests - ported from test_verdicts.py to the typed
Result API (BR-2): decisions route as `Result.ok(Verdict)`; verdict lines
without a decision, or conflicting decisions, are typed errors that feed
the attempt-bounded retry path - never exceptions, never silent routing."""

from agentic_dev_loop.core.verdict import Verdict, VerdictError, parse_verdict

REVIEW_VERDICTS = {"APPROVED": "approved", "NEEDS CHANGES": "needs_changes"}
TEST_VERDICTS = {"PASS": "pass", "FAIL": "fail"}
QA_VERDICTS = {"PASS": "pass", "FAIL": "fail", "UNVERIFIABLE": "unverifiable"}


def _parse(text, verdicts):
    return Verdict.from_text(text, verdicts)


def _ok(text, verdicts, expected):
    r = _parse(text, verdicts)
    assert r.is_ok(), f"expected ok, got {r.error}"
    assert r.value.value == expected


def _err(text, verdicts, code):
    r = _parse(text, verdicts)
    assert r.is_err(), "expected error, got ok"
    assert r.error is code
    assert r.value is None


class TestVerdictParsing:
    def test_parse_approved(self):
        _ok("VERDICT: APPROVED\nAll looks good.", REVIEW_VERDICTS, "approved")

    def test_parse_needs_changes(self):
        _ok("VERDICT: NEEDS CHANGES\nFile X needs work.", REVIEW_VERDICTS, "needs_changes")

    def test_parse_case_insensitive(self):
        _ok("verdict: approved", REVIEW_VERDICTS, "approved")
        _ok("VERDICT: needs changes", REVIEW_VERDICTS, "needs_changes")

    def test_parse_unknown_returns_ambiguous(self):
        _err("Verdict: MAYBE", REVIEW_VERDICTS, VerdictError.AMBIGUOUS)

    def test_parse_no_verdict_line_returns_no_verdict(self):
        _err("No verdict here", REVIEW_VERDICTS, VerdictError.NO_VERDICT)

    def test_parse_verdict_on_own_line(self):
        _ok("preamble.\nVERDICT: APPROVED\ntrailing.", REVIEW_VERDICTS, "approved")

    def test_last_match_wins(self):
        """Agents weigh outcomes before concluding; the final VERDICT line is
        the decision, not the first mention - earlier mentions that were
        discussion do not conflict."""
        text = ("VERDICT: NEEDS CHANGES was considered.\n"
                "But after re-checking, all standards are met.\n"
                "VERDICT: APPROVED\n")
        _ok(text, REVIEW_VERDICTS, "approved")

    def test_test_parse_pass(self):
        _ok("VERDICT: PASS", TEST_VERDICTS, "pass")

    def test_test_parse_fail(self):
        _ok("VERDICT: FAIL\n3 tests failed.", TEST_VERDICTS, "fail")

    def test_test_parse_missing_returns_no_verdict(self):
        _err("No verdict", TEST_VERDICTS, VerdictError.NO_VERDICT)
        _err("", TEST_VERDICTS, VerdictError.NO_VERDICT)

    def test_test_parse_case_insensitive(self):
        _ok("verdict: pass", TEST_VERDICTS, "pass")
        _ok("VERDICT: fail", TEST_VERDICTS, "fail")

    def test_test_last_match_wins(self):
        _ok("VERDICT: FAIL initially.\nFixed the issues.\nVERDICT: PASS\n",
            TEST_VERDICTS, "pass")

    def test_test_parse_bold_verdict_with_prose(self):
        """QA run 004 attempts 1-2: test agent styled its verdict as bold
        with trailing prose - must still parse."""
        text = ("# CI Fix Report\n\n## Summary\n\n"
                "**VERDICT: PASS** \u2014 All 10/10 conformance checks pass after "
                "fixing code duplication in test files.\n")
        _ok(text, TEST_VERDICTS, "pass")

    def test_test_parse_heading_verdict(self):
        """QA run 004 attempt 5: test agent styled its verdict as a
        markdown heading - must still parse."""
        _ok("## VERDICT: PASS", TEST_VERDICTS, "pass")
        _ok("### VERDICT: FAIL", TEST_VERDICTS, "fail")

    def test_review_parse_bold_verdict(self):
        _ok("**VERDICT: NEEDS CHANGES**", REVIEW_VERDICTS, "needs_changes")
        _ok("**VERDICT: APPROVED**", REVIEW_VERDICTS, "approved")

    def test_test_parse_bold_value(self):
        _ok("VERDICT: **PASS**", TEST_VERDICTS, "pass")

    def test_parse_bullet_or_blockquote_verdict(self):
        _ok("- VERDICT: PASS", TEST_VERDICTS, "pass")
        _ok("> VERDICT: FAIL", TEST_VERDICTS, "fail")

    def test_parse_decorated_prose_still_rejected(self):
        """Decoration tolerance must not turn verdict-*discussion* into a
        verdict: a bare verdict keyword followed by prose is not a decision,
        and last-match-wins still applies over decorated mentions."""
        _ok("VERDICT: PASS was briefly considered.\n**VERDICT: FAIL**\n",
            TEST_VERDICTS, "fail")

    def test_parse_verdict_tolerates_trailing_period(self):
        """A trailing full stop is sentence punctuation, not prose - agents
        emit `VERDICT: PASS.` constantly and it must not read as unclear."""
        _ok("VERDICT: PASS.", TEST_VERDICTS, "pass")
        _ok("VERDICT: FAIL.", TEST_VERDICTS, "fail")
        _ok("VERDICT: NEEDS CHANGES.", REVIEW_VERDICTS, "needs_changes")

    def test_parse_verdict_period_then_prose_is_caveat(self):
        _ok("VERDICT: PASS. More discussion.", TEST_VERDICTS, "pass")
        _ok("VERDICT: NEEDS CHANGES. See the notes.", REVIEW_VERDICTS, "needs_changes")

    def test_caveat_prose_after_em_dash(self):
        _ok("VERDICT: APPROVED \u2014 pending the divider split", REVIEW_VERDICTS, "approved")
        _ok("VERDICT: NEEDS CHANGES \u2014 see issues below", REVIEW_VERDICTS, "needs_changes")
        _ok("VERDICT: FAIL \u2014 3 tests broke", TEST_VERDICTS, "fail")

    def test_caveat_prose_after_comma(self):
        _ok("VERDICT: APPROVED, but fix the README bullet", REVIEW_VERDICTS, "approved")
        _ok("VERDICT: PASS, with lint nits noted", TEST_VERDICTS, "pass")

    def test_caveat_prose_after_semicolon_or_colon(self):
        _ok("VERDICT: APPROVED; see notes", REVIEW_VERDICTS, "approved")
        _ok("VERDICT: NEEDS CHANGES: see issues below", REVIEW_VERDICTS, "needs_changes")

    def test_caveat_prose_after_hyphen(self):
        _ok("VERDICT: APPROVED - pending", REVIEW_VERDICTS, "approved")

    def test_decoration_between_verdict_and_colon(self):
        _ok("**VERDICT**: APPROVED", REVIEW_VERDICTS, "approved")
        _ok("VERDICT : APPROVED", REVIEW_VERDICTS, "approved")

    def test_bare_prose_still_rejected(self):
        """No delimiter before the prose means discussion, not a decision -
        typed AMBIGUOUS (a verdict line existed but carried no decision)."""
        _err("VERDICT: PASS was considered", TEST_VERDICTS, VerdictError.AMBIGUOUS)
        _err("VERDICT: APPROVED with nits", REVIEW_VERDICTS, VerdictError.AMBIGUOUS)

    def test_question_mark_rejected(self):
        _err("VERDICT: APPROVED?", REVIEW_VERDICTS, VerdictError.AMBIGUOUS)

    def test_bang_alone_allowed_but_not_with_prose(self):
        _ok("VERDICT: FAIL!", TEST_VERDICTS, "fail")
        _err("VERDICT: FAIL! The build broke.", TEST_VERDICTS, VerdictError.AMBIGUOUS)

    def test_not_keyword_inverts_on_two_key_sets(self):
        _ok("VERDICT: NOT APPROVED", REVIEW_VERDICTS, "needs_changes")
        _ok("VERDICT: NOT NEEDS CHANGES", REVIEW_VERDICTS, "approved")
        _ok("VERDICT: NOT PASS", TEST_VERDICTS, "fail")

    def test_not_keyword_with_decoration_prose_and_tenses(self):
        _ok("VERDICT: NOT **APPROVED**", REVIEW_VERDICTS, "needs_changes")
        _ok("VERDICT: NOT APPROVED \u2014 pending the divider split", REVIEW_VERDICTS, "needs_changes")
        _ok("VERDICT: DID NOT PASS", TEST_VERDICTS, "fail")
        _ok("VERDICT: DOES NOT PASS", TEST_VERDICTS, "fail")

    def test_negated_discussion_still_rejected(self):
        _err("VERDICT: NOT PASS was considered", TEST_VERDICTS, VerdictError.AMBIGUOUS)

    def test_negation_ambiguous_on_multi_key_sets(self):
        _err("VERDICT: NOT A", {"A": "a", "B": "b", "C": "c"}, VerdictError.AMBIGUOUS)

    def test_not_unknown_keyword_rejected(self):
        _err("VERDICT: NOT MAYBE", REVIEW_VERDICTS, VerdictError.AMBIGUOUS)

    def test_parse_unverifiable(self):
        _ok("VERDICT: UNVERIFIABLE", QA_VERDICTS, "unverifiable")
        _ok("VERDICT: UNVERIFIABLE - no docker in the sandbox",
            QA_VERDICTS, "unverifiable")
        _ok("verdict: unverifiable", QA_VERDICTS, "unverifiable")

    def test_parse_verdict_unverifiable_returns_canonical_value(self):
        assert parse_verdict("VERDICT: UNVERIFIABLE", QA_VERDICTS) == "unverifiable"

    def test_negated_unverifiable_stays_ambiguous_on_three_key_sets(self):
        _err("VERDICT: NOT UNVERIFIABLE", QA_VERDICTS, VerdictError.AMBIGUOUS)

    def test_conflicting_pass_and_unverifiable_is_ambiguous(self):
        _err("VERDICT: PASS\nVERDICT: UNVERIFIABLE", QA_VERDICTS,
             VerdictError.AMBIGUOUS)
        assert parse_verdict("VERDICT: PASS\nVERDICT: UNVERIFIABLE",
                             QA_VERDICTS) == "retry"


class TestConflictingDecisions:
    """BR-2.4: conflicting *decisions* are never silently resolved."""

    def test_conflicting_decisions_are_ambiguous(self):
        _err("VERDICT: PASS\nbut wait\nVERDICT: FAIL", TEST_VERDICTS,
             VerdictError.AMBIGUOUS)

    def test_conflicting_review_decisions_are_ambiguous(self):
        _err("VERDICT: APPROVED\nVERDICT: NEEDS CHANGES", REVIEW_VERDICTS,
             VerdictError.AMBIGUOUS)

    def test_repeated_same_decision_is_fine(self):
        _ok("VERDICT: PASS\nstill passing\nVERDICT: PASS", TEST_VERDICTS, "pass")

    def test_negation_conflict_is_ambiguous(self):
        _err("VERDICT: NOT PASS\nVERDICT: PASS", TEST_VERDICTS, VerdictError.AMBIGUOUS)


class TestVerdictShape:
    def test_verdict_is_frozen_value(self):
        v = _parse("VERDICT: PASS", TEST_VERDICTS).value
        assert v.value == "pass"
        assert v == Verdict(value="pass")

    def test_verdict_equality(self):
        a = _parse("VERDICT: PASS", TEST_VERDICTS).value
        b = _parse("VERDICT: **PASS**", TEST_VERDICTS).value
        assert a == b
