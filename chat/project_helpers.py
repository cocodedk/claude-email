"""Small helpers shared between chat/project_tools.py and
chat/project_listing.py. Pulled out so the two modules don't need
to import each other (which previously formed a directional import
cycle through the re-export shim — Codex review P2)."""


def last_activity(task: dict | None) -> str | None:
    """Most-recent timestamp from a task row: completed_at → started_at →
    created_at. Returns None when the task is missing entirely (idle
    project)."""
    return (
        (task or {}).get("completed_at")
        or (task or {}).get("started_at")
        or (task or {}).get("created_at")
    )
