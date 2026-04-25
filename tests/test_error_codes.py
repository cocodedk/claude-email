"""Tests for src/error_codes.py — stable error-code enum + helpers."""
import pytest

from src.error_codes import (
    ERROR_CODES, ProjectNotFound, ProjectOutsideBase,
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
    def test_minimal_payload(self):
        payload = make_error("unauthorized", "auth failed")
        assert payload == {
            "code": "unauthorized",
            "message": "auth failed",
            "retryable": False,
        }

    def test_retryable_from_table_for_transient(self):
        payload = make_error("internal", "boom")
        assert payload["retryable"] is True

    def test_optional_hint_included_when_set(self):
        payload = make_error(
            "project_not_found", "nope",
            hint="Check the project name in Settings.",
        )
        assert payload["hint"] == "Check the project name in Settings."

    def test_hint_omitted_when_not_set(self):
        payload = make_error("unauthorized", "auth failed")
        assert "hint" not in payload

    def test_unknown_code_raises_loudly(self):
        with pytest.raises(ValueError, match="unknown error code"):
            make_error("teapot", "short and stout")


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
