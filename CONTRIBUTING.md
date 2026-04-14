# Contributing to claude-email

## Local Setup

1. Install Python 3.11+ and the [Claude Code CLI](https://claude.ai/code).
2. Clone the repo and install dependencies:
   ```bash
   python3 -m venv .venv
   .venv/bin/pip install -r requirements.txt
   ```
3. Copy `.env.example` to `.env` and fill in credentials.
4. Install git hooks:
   ```bash
   ./scripts/install-hooks.sh
   ```

## Local Git Setup

Run once after cloning:
```bash
git config pull.rebase true
git config core.autocrlf input
git config push.autoSetupRemote true
```

## Build and Test

```bash
.venv/bin/pytest tests/ -q      # Run all tests
.venv/bin/pytest tests/ -v      # Verbose output
```

## Branch Naming

| Prefix | Use for |
|---|---|
| `feature/` | New features |
| `fix/` | Bug fixes |
| `chore/` | Maintenance |
| `docs/` | Documentation |

## PR Checklist

- [ ] All tests pass (`pytest tests/ -q`)
- [ ] Manual test completed for changed functionality
- [ ] Commit messages follow Conventional Commits (`feat:`, `fix:`, `chore:`, etc.)
