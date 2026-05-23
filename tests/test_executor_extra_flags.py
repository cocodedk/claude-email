"""Tests for executor flags introduced by claude-code 2.1.115+."""
import subprocess
from src.executor import execute_command


def test_exclude_dynamic_prompt_default_on(mocker):
    mock_run = mocker.patch("subprocess.run")
    mock_run.return_value = subprocess.CompletedProcess(
        args=[], returncode=0, stdout="", stderr="",
    )
    execute_command("hi")
    cmd = mock_run.call_args.args[0]
    assert "--exclude-dynamic-system-prompt-sections" in cmd


def test_exclude_dynamic_prompt_disabled(mocker):
    mock_run = mocker.patch("subprocess.run")
    mock_run.return_value = subprocess.CompletedProcess(
        args=[], returncode=0, stdout="", stderr="",
    )
    execute_command("hi", exclude_dynamic_prompt=False)
    cmd = mock_run.call_args.args[0]
    assert "--exclude-dynamic-system-prompt-sections" not in cmd


def test_mcp_nonblocking_default_off(mocker, monkeypatch):
    monkeypatch.delenv("MCP_CONNECTION_NONBLOCKING", raising=False)
    mock_run = mocker.patch("subprocess.run")
    mock_run.return_value = subprocess.CompletedProcess(
        args=[], returncode=0, stdout="", stderr="",
    )
    execute_command("hi", extra_env={"FOO": "1"})
    env = mock_run.call_args.kwargs["env"] or {}
    assert "MCP_CONNECTION_NONBLOCKING" not in env


def test_mcp_nonblocking_enabled(mocker):
    mock_run = mocker.patch("subprocess.run")
    mock_run.return_value = subprocess.CompletedProcess(
        args=[], returncode=0, stdout="", stderr="",
    )
    execute_command("hi", mcp_nonblocking=True)
    env = mock_run.call_args.kwargs["env"]
    assert env["MCP_CONNECTION_NONBLOCKING"] == "true"
