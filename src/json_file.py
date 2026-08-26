"""Atomic JSON object read/write for per-project config files."""
import json
import os
import tempfile


def load_json_dict(path: str) -> dict:
    """Read a JSON object from path. Return {} if missing, corrupt, or not an object."""
    try:
        with open(path, "r") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def write_json(path: str, data: dict) -> None:
    """Write JSON atomically — a torn read of .claude.json makes Claude Code
    quarantine the file as malformed, so never truncate it in place."""
    try:
        mode = os.stat(path).st_mode & 0o777
    except OSError:
        mode = 0o644
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
