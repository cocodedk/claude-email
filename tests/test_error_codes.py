"""Tests for src/error_codes.py — stable error-code enum + helpers."""
import pytest

from src.error_codes import (
    DEFAULT_HINTS, ERROR_CODES, ProjectNotFound, ProjectOutsideBase,
    error_result_from_exc, make_error,
)


class TestErrorCodesTable:
    def test_enum_contains_every_contracted_code(self):
        expected = {
            "bad_envelope", "unknown_kind", "unauthorized", "forbidden",
            "project_not_found", "invalid_state", "not_implemented",
            "rate_limited", "internal",
        }
        assert set(ERROR_CODES) == expected

    def test_permanent_codes_marked_not_retryable(self):
        for code in (
            "bad_envelope", "unknown_kind", "unauthorized", "forbidden",
            "project_not_found", "invalid_state", "not_implemented",
        ):
            assert ERROR_CODES[code] is False, code

    def test_transient_codes_marked_retryable(self):
        assert ERROR_CODES["rate_limited"] is True
        assert ERROR_CODES["internal"] is True


class TestMakeError:
    def test_payload_always_carries_default_hint(self):
        """An error flag is useless without informative content. Every
        kind=error envelope must carry a hint the client can render
        verbatim — so when no per-call hint is supplied, the table
        default fills in."""
        payload = make_error("unauthorized", "auth failed")
        assert payload == {
            "code": "unauthorized",
            "message": "auth failed",
            "retryable": False,
            "hint": DEFAULT_HINTS["unauthorized"],
        }

    def test_retryable_from_table_for_transient(self):
        payload = make_error("internal", "boom")
        assert payload["retryable"] is True

    def test_per_call_hint_overrides_default(self):
        custom = "Use the link in your inbox to authorize this device."
        payload = make_error("unauthorized", "auth failed", hint=custom)
        assert payload["hint"] == custom

    def test_unknown_code_raises_loudly(self):
        with pytest.raises(ValueError, match="unknown error code"):
            make_error("teapot", "short and stout")


class TestDefaultHintCoverage:
    """Every contracted error code must have a default hint so no
    code path can emit an envelope-status=error without informative
    content. New codes added to ERROR_CODES without a matching hint
    fail this test."""

    def test_every_code_has_a_default_hint(self):
        missing = sorted(c for c in ERROR_CODES if c not in DEFAULT_HINTS)
        assert not missing, f"missing DEFAULT_HINTS for: {missing}"

    def test_no_orphan_hints(self):
        """A hint without a code in ERROR_CODES means a stale rename."""
        orphans = sorted(c for c in DEFAULT_HINTS if c not in ERROR_CODES)
        assert not orphans, f"DEFAULT_HINTS for unknown codes: {orphans}"

    def test_hints_are_actionable_text(self):
        """Empty / whitespace-only / placeholder hints defeat the point."""
        for code, hint in DEFAULT_HINTS.items():
            assert hint and hint.strip(), code
            assert "TODO" not in hint, code
            assert "FIXME" not in hint, code


class TestErrorResultFromExc:
    def test_project_not_found_maps_to_code(self):
        result = error_result_from_exc(ProjectNotFound("nope"))
        assert result == {"error": "nope", "error_code": "project_not_found"}

    def test_project_outside_base_maps_to_forbidden(self):
        result = error_result_from_exc(ProjectOutsideBase("too far"))
        assert result == {"error": "too far", "error_code": "forbidden"}

    def test_generic_value_error_maps_to_internal(self):
        result = error_result_from_exc(ValueError("boom"))
        assert result == {"error": "boom", "error_code": "internal"}

    def test_project_not_found_is_value_error_subclass(self):
        """_resolve_project historically raised ValueError — subclass keeps
        legacy `except ValueError` catches working."""
        assert issubclass(ProjectNotFound, ValueError)
        assert issubclass(ProjectOutsideBase, ValueError)
