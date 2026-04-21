"""Coverage for chat/dispatch.py helpers: _parse_task_id and _parse_bool."""
from chat.dispatch import _parse_bool, _parse_task_id


class TestParseTaskId:
    def test_missing_returns_none(self):
        assert _parse_task_id({}) is None

    def test_explicit_none_returns_none(self):
        assert _parse_task_id({"task_id": None}) is None

    def test_numeric_string_parses(self):
        assert _parse_task_id({"task_id": "42"}) == 42

    def test_int_passes_through(self):
        assert _parse_task_id({"task_id": 7}) == 7

    def test_non_numeric_string_returns_none(self):
        """A non-numeric string must not raise — dispatch drops the task_id
        so the tool call succeeds without threading."""
        assert _parse_task_id({"task_id": "not-a-number"}) is None

    def test_unsupported_type_returns_none(self):
        """Lists, dicts, etc. should coerce to None (TypeError path)."""
        assert _parse_task_id({"task_id": ["x"]}) is None


class TestParseBool:
    def test_true_passthrough(self):
        assert _parse_bool(True) is True

    def test_false_passthrough(self):
        assert _parse_bool(False) is False

    def test_truthy_strings(self):
        for v in ("true", "True", "1", "yes", "Y", "on"):
            assert _parse_bool(v) is True, v

    def test_falsy_strings(self):
        """Includes 'false' — bool('false') would be True (non-empty),
        which is why we can't just use bool()."""
        for v in ("false", "False", "0", "no", "N", "off", ""):
            assert _parse_bool(v) is False, v

    def test_int_truthy(self):
        assert _parse_bool(1) is True
        assert _parse_bool(42) is True

    def test_int_falsy(self):
        assert _parse_bool(0) is False

    def test_float_truthy_and_falsy(self):
        assert _parse_bool(1.5) is True
        assert _parse_bool(0.0) is False

    def test_unknown_string_returns_default(self):
        assert _parse_bool("maybe") is False
        assert _parse_bool("maybe", default=True) is True

    def test_other_type_returns_default(self):
        assert _parse_bool(None) is False
        assert _parse_bool([1]) is False
        assert _parse_bool({}, default=True) is True
