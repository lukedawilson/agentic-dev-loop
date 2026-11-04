"""Tests for the generic Result value type (NFR-1.1, TS-1)."""

import pytest

from agentic_dev_loop.core.results import Result


class TestResultOk:
    def test_ok_carries_value(self):
        r = Result.ok("done")
        assert r.is_ok()
        assert not r.is_err()
        assert r.value == "done"
        assert r.error is None

    def test_ok_none_value_is_valid(self):
        r = Result.ok(None)
        assert r.is_ok()
        assert r.value is None

    def test_unwrap_returns_value(self):
        assert Result.ok(42).unwrap() == 42


class TestResultErr:
    def test_err_carries_error(self):
        r = Result.err("no_verdict")
        assert r.is_err()
        assert not r.is_ok()
        assert r.error == "no_verdict"
        assert r.value is None

    def test_unwrap_raises_on_error(self):
        with pytest.raises(ValueError, match="no_verdict"):
            Result.err("no_verdict").unwrap()
