"""Parse the email `spawn` meta-command's argument string.

Supported syntax:
    spawn <path>
    spawn <path> <instruction>
    spawn <path> as <agent-name>
    spawn <path> as <agent-name> <instruction>

Returns (project_dir, agent_name, instruction). agent_name is None when
the `as <name>` clause is absent. The caller is responsible for
validating the name format via src.agent_name.validated_agent_name."""


def parse_spawn_args(raw: str) -> tuple[str, str | None, str]:
    """Tokenize ``raw`` into (project_dir, agent_name, instruction).

    ``as`` is a reserved keyword only at token position 1 (right after
    the path); everywhere else it's part of the instruction. Whitespace
    is collapsed in the returned instruction. Returns ("", None, "")
    for empty / whitespace-only input."""
    tokens = raw.split()
    if not tokens:
        return "", None, ""
    project_dir = tokens[0]
    if len(tokens) >= 3 and tokens[1] == "as":
        agent_name = tokens[2]
        instruction = " ".join(tokens[3:])
    else:
        agent_name = None
        instruction = " ".join(tokens[1:])
    return project_dir, agent_name, instruction
