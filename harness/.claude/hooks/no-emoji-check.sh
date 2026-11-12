#!/bin/bash
# no-emoji-check.sh: PreToolUse hook (Write|Edit). Block emojis from being written.
# Forces technical docs to drop emojis and use text alternatives (not allowed, prohibited, allowed, caution).
# If you do not want this style, remove it from the PreToolUse block in settings.json.

set -euo pipefail

# Fail-closed: when a step below fails, exit 2 blocks the write and its error is shown above this line.
trap 'echo "[Hook] no-emoji-check.sh could not run, so the write is blocked (fail-closed)." >&2; exit 2' ERR

INPUT=$(cat)

# Python reads the hook input and writes the decision.
export PYTHONUTF8=1
PYTHON3="python3"
if command -v python3 >/dev/null 2>&1; then
  case "$(command -v python3)" in
    */WindowsApps/python3*) PYTHON3="python" ;;
  esac
else
  PYTHON3="python"
fi

# Print the first non-empty tool_input field among the names given.
tool_input() {
  printf '%s' "$INPUT" | "$PYTHON3" -c '
import json, sys
fields = json.load(sys.stdin).get("tool_input") or {}
value = next((str(fields[name]) for name in sys.argv[1:] if fields.get(name)), "")
sys.stdout.buffer.write(value.encode("utf-8", "replace"))  # half of a surrogate pair has no UTF-8 form
' "$@"
}

# Print a PreToolUse deny decision with the reason given on stdin.
deny() {
  "$PYTHON3" -c '
import json, sys
decision = {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": sys.stdin.read().strip()}
print(json.dumps({"hookSpecificOutput": decision}))
'
}

# Self-skip: the hook's own code, plus retrospective notes that meta-quote blocked cases.
FILE_PATH=$(tool_input file_path)
case "$FILE_PATH" in
  *.claude/hooks/*) exit 0 ;;
  *.codex/hooks/*) exit 0 ;;
  *docs/wiki/postmortem/temp/*) exit 0 ;;
  *docs/wiki/postmortem/*) exit 0 ;;
esac

CONTENT=$(tool_input content new_string)
if [[ -z "$CONTENT" ]]; then
  exit 0
fi

# Detect emojis. Excluded on purpose: arrows (U+2190-21FF), multiplication sign (U+00D7).
EMOJIS=$(echo "$CONTENT" | "$PYTHON3" -c "
import sys, re
content = sys.stdin.read()
pattern = re.compile('[\U0001F000-\U0001FAFF☀-➿⬀-⯿]')
matches = pattern.findall(content)
if matches:
    seen = []
    for c in matches:
        if c not in seen:
            seen.append(c)
    print(','.join(seen[:5]))
")

if [[ -n "$EMOJIS" ]]; then
  printf '%s' "[Hook] Emojis are not allowed. Use text alternatives (not allowed, prohibited, allowed, caution, etc.). Found: $EMOJIS" | deny
fi

exit 0
