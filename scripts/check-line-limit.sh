#!/bin/sh
# Enforce the 200-line-per-file limit from CLAUDE.md.
# Scans all Python source files (excluding tests and __init__.py).
# Exit 1 if any file exceeds the limit.
set -eu

LIMIT=200
cd "$(git rev-parse --show-toplevel)"

violations=""

for f in $(find src/ chat/ -name '*.py' ! -name '__init__.py' && echo main.py chat_server.py); do
    [ -f "$f" ] || continue
    lines=$(wc -l < "$f")
    if [ "$lines" -gt "$LIMIT" ]; then
        violations="$violations\n  $f: $lines lines (limit $LIMIT)"
    fi
done

if [ -n "$violations" ]; then
    printf "File line limit exceeded:%b\n" "$violations" >&2
    exit 1
fi
