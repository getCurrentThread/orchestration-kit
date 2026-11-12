#!/bin/bash
# setup-check.sh: SessionStart hook. Notify when scripts/setup.sh has not run.
#
# It only reads. scripts/setup.sh is the one place that sets core.hooksPath, and it
# keeps an existing husky or lefthook chain, so a session start never rewires git hooks.
#
# Run time: ~30ms. Product-agnostic. Copy as is.

set -e

REPO="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
cd "$REPO" || exit 0

# The router (CLAUDE.md/AGENTS.md § "First-entry autonomous bootstrap") is the
# single source of truth: the agent runs the bootstrap flow ITSELF rather than
# asking the human to type commands. This hook only surfaces the signal; it
# must not contradict the router by prompting for a yes/no.
if [[ ! -f ".setup-done" ]]; then
  echo "[setup-check] ./scripts/setup.sh has not run. Some additional setup may be missing."
  echo "[AGENT-BOOTSTRAP] Claude/Codex: follow the 'First-entry autonomous bootstrap' contract in the router (CLAUDE.md/AGENTS.md) and run the appropriate flow yourself. Do not just tell the human to run it."
fi

exit 0
