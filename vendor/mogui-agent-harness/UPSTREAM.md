# Upstream provenance

- Source: https://github.com/baksohyeon/mogui-agent-harness
- Commit: `a1cbdbf29cca9131b1741caa9ef7644b3a5ff747` (main, 2026-10-28; still the head of `main` on 2026-11-17)
- License: MIT, Copyright (c) 2026 baksohyeon. Upstream's `LICENSE` is kept byte-identical as
  `harness/.agent/LICENSE-mogui-agent-harness`, so it travels with every install.

`harness/` is the tree that `scripts/harness.py` installs into a repository. It is derived from the upstream
tree at the commit above and maintained here, so it lives outside `vendor/`. Of upstream's 108 files, 54 are
unchanged, 31 are changed and 23 are not in `harness/`; 2 files exist only here. `harness/` has 87 files.

To check an unchanged file: `git hash-object --no-filters harness/<path>` equals
`git rev-parse <commit>:<path>` in a clone of the source.

## Not installed

`harness/` holds exactly what a repository receives, so upstream's own project files are left out:

| Upstream path | Why |
|---|---|
| `LICENSE` | At a repository root it would read as that repository's license. Kept as `.agent/LICENSE-mogui-agent-harness`. |
| `README.md`, `docs/en/`, `docs/ko/`, `docs/research/` (10 files) | They describe the upstream project. |
| `scripts/ingest.sh` | Upstream's installer, bash with `rsync`. `scripts/harness.py` replaces it, see "Ported". |
| `scripts/install-obsidian-plugins.sh` | Needed `jq`, `curl` and a GitHub token. The guide points at Obsidian's own plugin browser. |
| `.githooks/prepare-commit-msg` | Its `audit_log` entries were written after git had built the commit, so they never reached it and stayed staged. `pre-commit` still sets `updated_at`; `audit_log` is written by hand. |
| `tests/redaction-gate/run.sh` | Upstream's regression suite for `scripts/redaction-scan.sh`, which is unchanged here. It needs `gitleaks`, and every project would carry it in a `tests/` folder of its own. What the hook does with the scan's answer is tested in `tests/test_harness.py`. |
| `.claude/hooks/no-bak-slang-check.sh`, `.claude/hooks/remind-korean-style.sh` and their `.codex/hooks/` copies | A guard against one Korean calque slang family, removed by the owner's decision on 2026-11-12. |
| `.cursorrules`, `.windsurfrules`, `.cursor/rules/agentic-router.mdc`, `.cursor/mcp.json` | The adapters for Cursor and Windsurf, which were never run here. Removed by the owner's decision on 2026-11-17. |

## Changed

| Path under `harness/` | Change |
|---|---|
| `.codex/hooks/load-context.sh`, `memory-health.sh`, `no-emoji-check.sh`, `setup-check.sh` | Regular files. Upstream stores symbolic links to `../../.claude/hooks/`, which Windows checks out as text files. All six files in `.codex/hooks/` mirror `.claude/hooks/`: a test holds that here, and `scripts/verify-agent-ssot.sh` in the repository that received them. |
| `.claude/hooks/no-emoji-check.sh` | Reads its input and writes its decision with Python instead of `jq`, with `PYTHONUTF8=1`. When it cannot decide (no Python, unreadable input) it exits 2, which blocks the write; upstream let the write pass. It skips its `.codex/hooks/` copy, which holds the same emoji ranges. |
| `.claude/settings.json`, `.codex/hooks.example.json` | The two `code-review-graph` commands exit quietly when that tool is not installed; installed and failing, the session-start one prints the tool's message and says that the first tool call builds a missing graph. The entries of the removed hooks are gone. `.claude/settings.json` approves the MCP server of `.mcp.json` and runs every hook as `bash "$CLAUDE_PROJECT_DIR/..."`, so a path with a space works. |
| `.codex/config.example.toml` | Starts the installed `code-review-graph` command instead of `uvx code-review-graph`. |
| `CLAUDE.md`, `AGENTS.md` | The existing-project bootstrap describes what is left after `scripts/harness.py` (merge the `*.starter` files, then `scripts/setup.sh`) instead of running `ingest.sh`, and no longer skips `setup.sh` where husky or lefthook is in use. The tool table names the install command. The branch policy is a placeholder, where upstream states `dev` and `main` as fact. A linked worktree is covered by the `.setup-done` of its main checkout. The pointer to the removed slang vocabulary is gone. They name no Cursor or Windsurf adapter, and the two stay identical. |
| `.claude/hooks/setup-check.sh` and its `.codex/hooks/` copy | Only reports: upstream also set `core.hooksPath` at every session start, which replaced a husky or lefthook chain. In a linked worktree the marker of the main checkout counts, because `.setup-done` is not tracked and a new worktree never has one. |
| `.claude/hooks/memory-selfcheck.sh` and its `.codex/hooks/` copy | The reminder no longer ends in four `{{rule-N}}` placeholders, which no bootstrap step filled and every prompt printed. |
| `PROMPTS.md` | Section 2 starts from installed files and always runs `setup.sh`. Links to the removed documents are gone. It names two routers, and no Cursor or Windsurf host. |
| `.githooks/pre-commit` | Five defects of upstream's hook are mended, each held by a test. It reaches the secret scan on every commit, where upstream exits first when no `docs/*.md` file is staged. Every line of a page keeps its ending, where upstream's text-mode write turned LF into CRLF. Its updater exits 11 to 13, so a failed Python (exit 1) is a warning that names the page and no longer "bumped". It lists staged files with `core.quotePath=false`, so a page whose name is not ASCII is not skipped. Without `git config user.name` it falls back to `GIT_AUTHOR_NAME`, and leaves `@git_author` alone with a warning when there is none, where upstream ended silently. |
| `.github/workflows/redaction.yml` | Scans pushes to `master` as well as `main`. The step that ran `tests/redaction-gate/run.sh` is gone. |
| `config/gitleaks.toml` | No longer exempts the path `tests/redaction-gate/`, which is not installed. |
| `scripts/install-hooks.sh` | Says that `core.hooksPath` holds for every checkout of the repository, and names the hooks in `.git/hooks` that stop running once it is set. |
| `scripts/setup.sh` | Leaves `core.hooksPath` as it is where the repository has husky or lefthook, and still finishes. Names the install command for `code-review-graph`. Compares `AGENTS.md`, the one other router, with `CLAUDE.md` using `cmp`: see `scripts/smoke-codex.sh`. |
| `scripts/verify-agent-ssot.sh` | Requires `.mcp.json`; no longer requires `scripts/ingest.sh`, the two removed hooks, `.githooks/prepare-commit-msg` or the Cursor and Windsurf adapters, and its check of the Cursor rule is gone. Fails when a `.codex/hooks/` file differs from its `.claude/hooks/` original. Its JSON check runs `python3 -I -m json.tool`: without `-I`, a `json.py` in the repository's root ran inside the setup. Compares the router files with `cmp`: see `scripts/smoke-codex.sh`. |
| `scripts/smoke-codex.sh` | Compares `AGENTS.md` with `CLAUDE.md` using `cmp`, as the two scripts above now do. Upstream compares hashes from `shasum`, a perl script. The bash of Git for Windows has perl's folder on its PATH only as a login shell, so on a machine whose own PATH lacks that folder `setup.sh` ended with exit 127 (`shasum: command not found`). |
| `scripts/wiki-lint.sh` | Check 8, the slang pattern, is gone. Its stale guard no longer lists the removed adapters. |
| `.agent/workflows/lint.md`, `.agent/workflows/session-start.md` | No longer name the removed slang guard. The start order names two routers. |
| `docs/wiki/guides/01-onboarding.md`, `docs/wiki/guides/README.md`, `docs/wiki/guides/maintenance.md` | No longer name the Cursor and Windsurf adapters. |
| `.agent/korean-persona.md` | The vocabulary section on that slang is gone; the voice section stays. |
| `docs/wiki/guides/03-host-runtime-and-hooks.md` | Names `.mcp.json` for Claude Code. Its Cursor section and the rows for Cursor and Windsurf are gone. |
| `docs/wiki/guides/obsidian-setup.md`, `docs/wiki/index.md` | The guide no longer needs a script, and the index lists it, which clears the one warning `wiki-lint.sh` gave on a new install. |

## Added

| Path under `harness/` | What |
|---|---|
| `.mcp.json` | Starts the `code-review-graph` MCP server for Claude Code. Upstream ships that server's config for Cursor and Codex only. |
| `.agent/LICENSE-mogui-agent-harness` | Upstream's `LICENSE`, moved. |

File modes are not kept: a commit made on Windows records no executable bit. `scripts/harness.py` and
`prepare` give the `git add --chmod=+x` command for a repository that other systems use.

## Ported

| Here | Upstream | Differences |
|---|---|---|
| `scripts/harness.py` | `scripts/ingest.sh` | Python instead of bash with `rsync`. Same four report groups, and the same rule that nothing existing is overwritten and a differing file gets `<name>.starter`. A `.gitignore` of the repository's own is the exception: the lines it lacks are appended, in its own line endings. Files that differ only in CRLF against LF count as identical. It stops before the first write when a file sits where a folder is needed, and takes back its files when a later write fails. It names a missing `bash`, `code-review-graph` or `gitleaks`. |
| `scripts/orchestration.py prepare` | The router's "First-entry autonomous bootstrap", and `PROMPTS.md` #1, where an agent interviews the owner. | Installs the harness and runs `scripts/setup.sh` before a worker starts: only where every script and git hook of the harness is the one shipped here, and not where git already runs hooks of the project's own. It refuses a folder inside a repository, a linked worktree, and a repository that git refuses to read. It fills the placeholders itself (`harness.fill`): name, repository and branch from the folder and git, and a neutral value for the rest. A test holds that every placeholder of the tree has a value. `dispatch --project <directory> --worktree new-top-level` passes the project's checked-out branch as `--base-branch`: without it, Orca 1.4.258 based the new worktree on the remote's branch, and a commit that was not pushed was missing. |

## Measured

On 2026-11-17, on Windows 11, in a new repository that received the tree of plugin 0.14.2 (91 files):

- With gitleaks 8.30.1 (winget `Gitleaks.Gitleaks`), the commit of the new install passed the secret scan with 0
  findings and took 26 seconds, and a commit that held a made-up GitHub token was refused in 2 seconds (exit 1,
  rules `github-pat` and `github_token`). Until that day the machine had no gitleaks, and the hook skipped the scan.
- `scripts/smoke-codex.sh --live` passed with codex-cli 0.167.0: the static checks, and `codex exec` answered in 26
  seconds.
- `code-review-graph update --skip-flows`, which the PostToolUse hook runs after every Edit, Write and Bash call,
  took 1.5 seconds the first time and 0.7 seconds per call after it.

## Not run

Nothing is claimed about these: `.github/workflows/redaction.yml`, Linux and macOS, and a Windows machine whose
code page is not UTF-8.
