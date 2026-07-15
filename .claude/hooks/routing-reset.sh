#!/usr/bin/env bash
# PreToolUse hook on Agent.
# Zeroes the direct-edit counter (routing-reminder.sh) whenever an Agent
# call delegates to one of the fable-advisor routing lanes, so the
# reminder only fires on genuine drift, not after a real delegation.
set -euo pipefail

STATE_DIR="$HOME/.claude/hook-state"
mkdir -p "$STATE_DIR"
STATE_FILE="$STATE_DIR/${CLAUDE_CODE_SESSION_ID:-default}.count"

PYEXE="/c/Users/Rishab 2/AppData/Local/Python/bin/python.exe"
SUBTYPE=$("$PYEXE" -c "
import sys, json
try:
    d = json.load(sys.stdin)
except Exception:
    print('')
    raise SystemExit
print(d.get('tool_input', {}).get('subagent_type', ''))
" 2>/dev/null || echo "")

case "$SUBTYPE" in
  codex-implementer|implementer-opus|fable-advisor)
    echo 0 > "$STATE_FILE"
    ;;
esac
