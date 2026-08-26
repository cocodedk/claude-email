#!/usr/bin/env bash
# loop-watchdog.sh [--kill] [--max-age-min N]
#
# Report (and optionally kill) agent-loop processes that have outlived a threshold.
#
# Matching is by /proc/<pid>/cmdline, and it EXCLUDES this script, its own shell, and
# every ancestor of it. That matters: `pgrep -f <pattern>` matches the wrapper shell
# whose command line CONTAINS the pattern, so a naive check reports phantom loops that
# are really just the check itself. Every count in this file is a real process.
set -uo pipefail
KILL=0; MAX_AGE_MIN=180
while [ $# -gt 0 ]; do case "$1" in
  --kill) KILL=1; shift ;;
  --max-age-min) MAX_AGE_MIN="$2"; shift 2 ;;
  *) echo "unknown arg: $1" >&2; exit 2 ;;
esac; done

# every ancestor of this process, so we never match ourselves
declare -A SELF
p=$$
while [ "$p" -gt 1 ]; do SELF[$p]=1; p=$(ps -o ppid= -p "$p" 2>/dev/null | tr -d ' '); [ -z "$p" ] && break; done

PATTERNS='run-chain\.sh|loop-engine\.sh|verify-loop\.sh|judge-check\.sh|claude -p|codex exec'
found=0
for d in /proc/[0-9]*; do
  pid="${d#/proc/}"
  [ -n "${SELF[$pid]:-}" ] && continue
  cmd=$(tr '\0' ' ' < "$d/cmdline" 2>/dev/null) || continue
  [ -z "$cmd" ] && continue
  case "$cmd" in *"loop-watchdog"*) continue ;; esac
  printf '%s' "$cmd" | grep -qE "$PATTERNS" || continue
  # elapsed time, in minutes
  age_s=$(ps -o etimes= -p "$pid" 2>/dev/null | tr -d ' '); [ -z "$age_s" ] && continue
  age_m=$(( age_s / 60 ))
  short=$(printf '%.90s' "$cmd")
  if [ "$age_m" -ge "$MAX_AGE_MIN" ]; then
    echo "STRAY  pid=$pid  age=${age_m}m (>= ${MAX_AGE_MIN}m)  $short"
    [ "$KILL" -eq 1 ] && { kill -TERM "$pid" 2>/dev/null && echo "       -> TERM sent"; }
  else
    echo "alive  pid=$pid  age=${age_m}m  $short"
  fi
  found=$((found+1))
done
[ "$found" -eq 0 ] && echo "no loop processes running (checked $(ls -d /proc/[0-9]* | wc -l) pids)"
exit 0
