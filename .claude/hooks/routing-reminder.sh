#!/usr/bin/env bash
# PreToolUse hook on Edit|Write.
# Cannot detect the running session model (no hook input field or env var
# exposes it), so this tracks the actual observed failure mode instead:
# repeated direct edits with no intervening delegation. See
# routing-reset.sh, which zeroes this counter when an Agent call delegates
# to codex-implementer / implementer-opus / fable-advisor.
set -euo pipefail

STATE_DIR="$HOME/.claude/hook-state"
mkdir -p "$STATE_DIR"
STATE_FILE="$STATE_DIR/${CLAUDE_CODE_SESSION_ID:-default}.count"

COUNT=0
if [ -f "$STATE_FILE" ]; then
  COUNT=$(cat "$STATE_FILE")
fi
COUNT=$((COUNT + 1))

if [ "$COUNT" -ge 3 ]; then
  echo 0 > "$STATE_FILE"
  printf '{"systemMessage": "Routing check (CLAUDE.md Agentic Routing): %s direct edits since the last delegation. If this session is on Fable or Opus, this implementation work should be going through codex-implementer (or implementer-opus / fable-advisor with a stated reason) instead of direct edits. On Sonnet, direct edits are correct — ignore this."}' "$COUNT"
else
  echo "$COUNT" > "$STATE_FILE"
fi
