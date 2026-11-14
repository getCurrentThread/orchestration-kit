#!/usr/bin/env bash
# setup.sh: one-time setup after a fresh clone. Safe to re-run.
# Add or remove steps to fit your new project.

set -euo pipefail

# Every git hook and verification in the starter assumes a git repo. To make it
# work right away even in an empty new project that has not run git init yet,
# initialize the repo here if it is not one.
if ! git rev-parse --show-toplevel >/dev/null 2>&1; then
  echo "==> Not a git repository, running 'git init'"
  git init -q
  echo "  git repository initialized"
  echo ""
fi

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

# ============================================================================
# 1/4 Install git hooks
# ============================================================================
echo "==> 1/4 Install git hooks"
chmod +x scripts/*.sh 2>/dev/null || true
chmod +x .githooks/* 2>/dev/null || true
if [ -d .husky ] || compgen -G "lefthook.*" >/dev/null || compgen -G ".lefthook.*" >/dev/null ||
  grep -qE '"(husky|lefthook)"' package.json 2>/dev/null; then
  # core.hooksPath would replace that chain, so it stays as it is.
  echo "  [KEEP] husky or lefthook found: core.hooksPath is not changed."
  echo "         Merge the frontmatter automation of .githooks/pre-commit into that chain by hand."
elif [ -f scripts/install-hooks.sh ]; then
  ./scripts/install-hooks.sh
else
  git config core.hooksPath .githooks
  echo "  git hooks enabled (core.hooksPath = .githooks)"
fi

# ============================================================================
# 2/4 Multi-host router verify (CLAUDE.md == AGENTS.md == ...)
# ============================================================================
echo ""
echo "==> 2/4 Multi-host router verify"
DRIFT=()
for f in AGENTS.md .cursorrules .windsurfrules; do
  if [ ! -e "$f" ]; then
    DRIFT+=("$f (missing)")
  else
    # cmp, not shasum: shasum is a perl script, which the bash of Git for Windows finds only as a login shell.
    cmp -s CLAUDE.md "$f" || DRIFT+=("$f")
  fi
done
if [ ${#DRIFT[@]} -eq 0 ]; then
  echo "  [OK] host instruction files in sync"
else
  echo "  [DRIFT] ${DRIFT[*]}: run: cp CLAUDE.md AGENTS.md .cursorrules .windsurfrules"
fi

# ============================================================================
# 3/4 Verify required AI tools
# ============================================================================
echo ""
echo "==> 3/4 Verify AI tools (optional; the starter skeleton works without them)"

# Host-agnostic plugin/skill detection. No single vendor (Claude Code, Codex,
# Cursor, opencode, ...) is assumed: probe every known agent config root, and
# let an explicit <TOOL>_HOME env var override. Teach setup.sh about another
# host by adding its config root to HOST_ROOTS.
HOST_ROOTS=(
  "${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
  "$HOME/.config/claude"
  "${CODEX_HOME:-$HOME/.codex}"
  "$HOME/.config/codex"
  "$HOME/.cursor"
  "$HOME/.config/opencode"
)

# plugin_found <env_override_path> <name-pattern>... -> 0 if the override exists,
# or any host root contains a matching entry (case-insensitive, depth-limited
# search). Vendor-neutral by construction: it never hard-codes a single host's
# plugin/skill layout, which varies (e.g. plugins/data/*, plugins/cache/*/*,
# skills/*) across Claude Code, Codex, Cursor, opencode, ...
plugin_found() {
  local override="$1"; shift
  [ -n "$override" ] && [ -e "$override" ] && return 0
  local pat root hit
  for pat in "$@"; do
    for root in "${HOST_ROOTS[@]}"; do
      [ -d "$root" ] || continue
      hit="$(find "$root" -maxdepth 5 -iname "$pat" -print 2>/dev/null | head -n1 || true)"
      [ -n "$hit" ] && return 0
    done
  done
  return 1
}

# gstack: a skill bundle exposing bin/, or a `gstack` binary on PATH.
if command -v gstack >/dev/null 2>&1 \
  || plugin_found "${GSTACK_HOME:-}" "*gstack*"; then
  echo "  [OK] gstack"
else
  echo "  [CHECK] gstack missing. Clone into your host's skills dir (or set GSTACK_HOME):"
  echo "          git clone --depth 1 https://github.com/garrytan/gstack.git <host-skills-dir>/gstack && (cd <host-skills-dir>/gstack && ./setup)"
fi

command -v gsd-tools >/dev/null 2>&1 && echo "  [OK] GSD (gsd-tools)" || {
  echo "  [CHECK] GSD missing. Install:"
  echo "          npx @opengsd/gsd-core@latest"
}
command -v code-review-graph >/dev/null 2>&1 && echo "  [OK] code-review-graph" || {
  echo "  [CHECK] code-review-graph missing. Install:"
  echo "          uv tool install code-review-graph   (or: pip install code-review-graph)"
}

# Superpowers: a plugin/skill bundle. Detection stays host-agnostic (no ~/.claude
# assumption) so a vendor-neutral install on ANY host counts; set SUPERPOWERS_HOME
# to point at an explicit install.
if plugin_found "${SUPERPOWERS_HOME:-}" "*superpowers*"; then
  echo "  [OK] Superpowers"
else
  echo "  [CHECK] Superpowers missing. Install via your host's plugin manager (see CLAUDE.md tools table),"
  echo "          or set SUPERPOWERS_HOME to an existing install."
fi

# ============================================================================
# 4/4 Harness wiring verify
# ============================================================================
echo ""
echo "==> 4/4 Harness wiring verify"
if [ -x scripts/verify-agent-ssot.sh ]; then
  scripts/verify-agent-ssot.sh
else
  echo "  [SKIP] scripts/verify-agent-ssot.sh not executable"
fi

touch .setup-done

echo ""
echo "Setup complete. Next: read CLAUDE.md (or AGENTS.md), then start a session."
