#!/usr/bin/env bash
# install-hooks.sh: wire up .githooks/ as the project's hook directory
#
# Run once. Hooks added under .githooks/ will run automatically after this.

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

# Ensure hooks executable
chmod +x .githooks/* 2>/dev/null || true

# Git stops running the hooks in .git/hooks once core.hooksPath is set, and every checkout of this
# repository shares the setting, so they are named before they go quiet.
REPLACED=$(find "$(git rev-parse --git-common-dir)/hooks" -maxdepth 1 -type f ! -name '*.sample' 2>/dev/null || true)

# Wire git to use .githooks/
git config core.hooksPath .githooks

echo "Git hooks enabled: core.hooksPath = .githooks, in every checkout of this repository"
if [ -n "$REPLACED" ]; then
  echo ""
  echo "[NOTE] These hooks no longer run. Merge what you still need into .githooks/ by hand:"
  echo "$REPLACED" | sed 's/^/  - /'
fi
echo ""
echo "Active hooks:"
ls .githooks/ | sed 's/^/  - /'
echo ""
echo "Test: edit a docs/*.md file, then git add + git commit. The updated field should change automatically."
