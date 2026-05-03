"""Exceptions raised by the chat DB registry layer."""


class AgentNameTaken(Exception):
    """Raised when another live process already owns the agent name."""

    def __init__(self, name: str, owner_pid: int):
        self.name = name
        self.owner_pid = owner_pid
        super().__init__(f"agent {name!r} already owned by pid {owner_pid}")


class AgentProjectTaken(Exception):
    """Deprecated as of 2026-05-03 — register_agent no longer raises this.
    Kept so existing 'except (AgentNameTaken, AgentProjectTaken)' clauses
    in proc_reconcile.py, chat_pid_reclaim.py, and chat-register-self.py
    remain safe. Will be removed in a follow-up cleanup pass."""

    def __init__(self, project_path: str, owner_name: str, owner_pid: int):
        self.project_path = project_path
        self.owner_name = owner_name
        self.owner_pid = owner_pid
        super().__init__(
            f"project {project_path!r} already owned by agent "
            f"{owner_name!r} (pid {owner_pid})",
        )
