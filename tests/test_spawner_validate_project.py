"""Tests for spawner's validate_project_path helper."""
import pytest


class TestValidateProjectPath:
    def test_valid_path_returns_resolved(self, tmp_path):
        from src.spawner import validate_project_path

        d = tmp_path / "proj"
        d.mkdir()
        result = validate_project_path(str(d))
        assert result == str(d.resolve())

    def test_nonexistent_dir_raises(self, tmp_path):
        from src.spawner import validate_project_path

        with pytest.raises(ValueError, match="does not exist"):
            validate_project_path(str(tmp_path / "nope"))

    def test_outside_allowed_base_raises(self, tmp_path):
        from src.spawner import validate_project_path

        outside = tmp_path / "outside"
        outside.mkdir()
        allowed = tmp_path / "allowed"
        allowed.mkdir()

        with pytest.raises(ValueError, match="outside allowed base"):
            validate_project_path(str(outside), allowed_base=str(allowed))

    def test_inside_allowed_base_passes(self, tmp_path):
        from src.spawner import validate_project_path

        base = tmp_path / "base"
        base.mkdir()
        proj = base / "proj"
        proj.mkdir()

        result = validate_project_path(str(proj), allowed_base=str(base))
        assert result == str(proj.resolve())

    def test_traversal_blocked(self, tmp_path):
        from src.spawner import validate_project_path

        base = tmp_path / "base"
        base.mkdir()
        # tmp_path exists but is outside base
        with pytest.raises(ValueError, match="outside allowed base"):
            validate_project_path(str(base / ".."), allowed_base=str(base))

    def test_bare_name_resolved_against_allowed_base(self, tmp_path):
        from src.spawner import validate_project_path

        base = tmp_path / "base"
        base.mkdir()
        proj = base / "babakcast"
        proj.mkdir()

        result = validate_project_path("babakcast", allowed_base=str(base))
        assert result == str(proj.resolve())

    def test_relative_subpath_resolved_against_allowed_base(self, tmp_path):
        from src.spawner import validate_project_path

        base = tmp_path / "base"
        base.mkdir()
        nested = base / "group" / "sub"
        nested.mkdir(parents=True)

        result = validate_project_path("group/sub", allowed_base=str(base))
        assert result == str(nested.resolve())

    def test_bare_name_without_allowed_base_falls_through(self, tmp_path, monkeypatch):
        from src.spawner import validate_project_path

        # With no allowed_base, "foo" resolves against cwd — unchanged legacy behavior
        monkeypatch.chdir(tmp_path)
        (tmp_path / "foo").mkdir()
        result = validate_project_path("foo")
        assert result == str((tmp_path / "foo").resolve())
