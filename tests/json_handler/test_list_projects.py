"""``kind=list_projects`` returns the discoverable git-repo set under
``allowed_base`` with per-project task state — powers the Projects tab."""
import json

from src.json_handler import handle_json_email

from .conftest import base_config, json_email


def _git_dir(parent, name: str):
    d = parent / name
    d.mkdir()
    (d / ".git").mkdir()
    return d


class TestListProjectsKind:
    def test_returns_ack_with_projects_array(self, resources, tmp_path, mocker):
        cdb, tq, wm = resources
        cfg = base_config(tmp_path)
        # Note: the conftest already mkdir'd ``tmp_path/p`` (no .git) — that
        # one will be excluded by the git-repo filter.
        _git_dir(tmp_path, "alpha")
        mock_send = mocker.patch("src.json_handler.send_reply", return_value="<r@x>")
        msg = json_email({
            "v": 1, "kind": "list_projects",
            "meta": {"auth": "s3cret"},
        })
        assert handle_json_email(msg, cfg, cdb, tq, wm) is True
        body = json.loads(mock_send.call_args.kwargs["body"])
        assert body["kind"] == "ack"
        assert isinstance(body["data"]["projects"], list)
        names = [p["name"] for p in body["data"]["projects"]]
        assert names == ["alpha"]  # the non-git ``p`` is excluded

    def test_empty_universe_returns_empty_list(self, resources, tmp_path, mocker):
        """Universe with no git repos under allowed_base — ack with empty
        list, NOT an error."""
        cdb, tq, wm = resources
        cfg = base_config(tmp_path)
        # Remove the conftest-created ``p`` and don't make any git repos.
        (tmp_path / "p").rmdir()
        mock_send = mocker.patch("src.json_handler.send_reply", return_value="<r@x>")
        msg = json_email({
            "v": 1, "kind": "list_projects",
            "meta": {"auth": "s3cret"},
        })
        handle_json_email(msg, cfg, cdb, tq, wm)
        body = json.loads(mock_send.call_args.kwargs["body"])
        assert body["kind"] == "ack"
        assert body["data"]["projects"] == []

    def test_per_project_shape_matches_spec(self, resources, tmp_path, mocker):
        cdb, tq, wm = resources
        cfg = base_config(tmp_path)
        _git_dir(tmp_path, "alpha")
        mock_send = mocker.patch("src.json_handler.send_reply", return_value="<r@x>")
        msg = json_email({
            "v": 1, "kind": "list_projects",
            "meta": {"auth": "s3cret"},
        })
        handle_json_email(msg, cfg, cdb, tq, wm)
        body = json.loads(mock_send.call_args.kwargs["body"])
        row = body["data"]["projects"][0]
        # Five fields the app contract pinned:
        assert set(row.keys()) == {
            "name", "path", "running_task_id", "queue_depth", "last_activity_at",
        }

    def test_unauthorized_returns_error(self, resources, tmp_path, mocker):
        cdb, tq, wm = resources
        cfg = base_config(tmp_path, secret="correct")
        mock_send = mocker.patch("src.json_handler.send_reply", return_value="<r@x>")
        msg = json_email({
            "v": 1, "kind": "list_projects",
            "meta": {"auth": "WRONG"},
        })
        handle_json_email(msg, cfg, cdb, tq, wm)
        body = json.loads(mock_send.call_args.kwargs["body"])
        assert body["error"]["code"] == "unauthorized"

    def test_echoes_meta_ask_id(self, resources, tmp_path, mocker):
        cdb, tq, wm = resources
        cfg = base_config(tmp_path)
        _git_dir(tmp_path, "alpha")
        mock_send = mocker.patch("src.json_handler.send_reply", return_value="<r@x>")
        msg = json_email({
            "v": 1, "kind": "list_projects",
            "meta": {"auth": "s3cret", "ask_id": 42},
        })
        handle_json_email(msg, cfg, cdb, tq, wm)
        body = json.loads(mock_send.call_args.kwargs["body"])
        assert body["meta"]["ask_id"] == 42
