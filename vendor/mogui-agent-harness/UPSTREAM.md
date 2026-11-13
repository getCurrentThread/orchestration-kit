# Upstream provenance

- Source: https://github.com/baksohyeon/mogui-agent-harness
- Commit: `a1cbdbf29cca9131b1741caa9ef7644b3a5ff747` (main, 2026-10-28; still the head of `main` on 2026-11-13)
- License: MIT, Copyright (c) 2026 baksohyeon. Upstream's `LICENSE` is kept byte-identical as
  `harness/.agent/LICENSE-mogui-agent-harness`, so it travels with every install.

`harness/` is this plugin's repository harness: the files that `scripts/harness.py` installs into a
repository so that any agent session started there, a dispatched worker included, reads the same rules. It
is the repository layer that the workspace layer in `vendor/mogui` names as its sibling.

It is derived from the upstream tree at the commit above and maintained here. It is not a byte-identical
copy, and it lives outside `vendor/` for that reason. Of upstream's 108 files, 61 are unchanged, 29 are
changed and 18 are not in `harness/`; 2 files exist only here. `harness/` has 92 files.

To check an unchanged file: `git hash-object --no-filters harness/<path>` equals
`git rev-parse <commit>:<path>` in a clone of the source.

## What differs from upstream

### Not installed

`harness/` holds exactly what a repository receives, so upstream's own project files are left out:

| Upstream path | Why |
|---|---|
| `LICENSE` | At a repository root it would read as that repository's license. Kept as `.agent/LICENSE-mogui-agent-harness`. |
| `README.md`, `docs/en/`, `docs/ko/`, `docs/research/` (10 files) | They describe the upstream project. The installed guides under `docs/wiki/guides/` cover use. |
| `scripts/ingest.sh` | Upstream's installer, bash with `rsync`. `scripts/harness.py` replaces it, see "Ported". |
| `scripts/install-obsidian-plugins.sh` | Needed `jq`, `curl` and a GitHub token. The guide now points at Obsidian's own plugin browser. |
| `.githooks/prepare-commit-msg` | Added an `audit_log` entry to every staged wiki page. Git builds the commit before it runs this hook, so the entries never reached their commit and stayed staged after it: 16 files after the commit that installs the harness. An entry repeated what `git log` holds, and `pre-commit` still sets `updated_at`. `audit_log` is written by hand. |
| `.claude/hooks/no-bak-slang-check.sh`, `.claude/hooks/remind-korean-style.sh` and their `.codex/hooks/` copies | A guard against one Korean calque slang family. Removed by the owner's decision on 2026-11-12: models no longer write it. |

### Changed

| Path under `harness/` | Change |
|---|---|
| `.codex/hooks/load-context.sh`, `memory-health.sh`, `no-emoji-check.sh`, `setup-check.sh` | Upstream stores symbolic links to `../../.claude/hooks/`. Windows checks a link out as a text file holding its path, which is a broken hook, so these are regular files. All six files in `.codex/hooks/` mirror `.claude/hooks/`: a test holds that here, and `scripts/verify-agent-ssot.sh` in the repository that received them. |
| `.claude/hooks/no-emoji-check.sh` | Reads the hook input and writes its decision with Python, which it already used to find emoji, instead of `jq`. Sets `PYTHONUTF8=1` so the text is read as UTF-8 whatever the Windows code page is. When it cannot decide (no Python, unreadable input) it exits 2, which blocks the write; upstream let the write pass. It also skips its `.codex/hooks/` copy, which holds the same emoji ranges. |
| `.claude/settings.json`, `.codex/hooks.example.json` | The two `code-review-graph` commands exit quietly when that tool is not installed. Installed and failing, the one after a tool call fails as upstream's does, which Claude Code shows as a hook error, and the session-start one prints the tool's own message and one line saying that a missing graph is built by the first tool call. The entries of the removed hooks are gone. `.claude/settings.json` approves the MCP server of `.mcp.json` and runs every hook as `bash "$CLAUDE_PROJECT_DIR/..."`, so a repository path with a space works. |
| `.cursor/mcp.json`, `.codex/config.example.toml` | Start the installed `code-review-graph` command instead of `uvx code-review-graph`, like the hooks and `.mcp.json`. |
| `CLAUDE.md`, `AGENTS.md`, `.cursorrules`, `.windsurfrules` | The existing-project bootstrap describes what is left after `scripts/harness.py` (report and merge the `*.starter` files, then `scripts/setup.sh`) instead of running `ingest.sh` from a starter clone. It no longer tells the agent to skip `setup.sh` where husky or lefthook is in use, because `setup.sh` handles that itself. The tool table names the install command. The line that pointed at the removed slang vocabulary is gone. The branch policy is a placeholder to fill in: upstream states `dev` and `main` as fact, which is wrong in a repository that has neither. The bootstrap section says that a linked git worktree is covered by the `.setup-done` of its main checkout. The four stay identical. |
| `.claude/hooks/setup-check.sh` and its `.codex/hooks/` copy | Only reports. Upstream also set `core.hooksPath` at every session start, which replaced a husky or lefthook chain without asking. Its bootstrap notice points at the router. In a linked worktree the marker of the main checkout counts: `.setup-done` is not tracked, so a new worktree never has one, and every worker that the workspace layer starts in its own worktree was told to run the bootstrap again. |
| `.claude/hooks/memory-selfcheck.sh` and its `.codex/hooks/` copy | The reminder no longer ends in four `{{rule-N}}` placeholders. No bootstrap step names this file, so they stayed and were printed into every prompt; a comment says where a rule line goes. |
| `PROMPTS.md` | Section 2 starts from installed files and always runs `setup.sh`. Links to the removed documents are gone. |
| `.githooks/pre-commit` | Upstream exits before its secret scan when no `docs/*.md` file is staged, so a commit of code alone was never scanned. It now reaches the scan on every commit. Four more defects of upstream's hook are mended, each held by a test. It rewrote a page through a text-mode file, which on Windows turns LF into CRLF: every line keeps the ending it had, and nothing below the front matter is rewritten. Its updater answers with exit 1 for "bumped", which is also what a failed Python exits with, so a page it could not read was reported as bumped: the codes are 11 to 13, and any other is a warning that names the page. It listed staged files with git's default quoting, so a page whose name is not ASCII (a Korean title) was skipped without a word: it lists them with `core.quotePath=false`. And `git config user.name` under `set -e` ended the hook silently, which git reports as a refused commit, where the identity comes from the environment alone: the name falls back to `GIT_AUTHOR_NAME`, and `@git_author` is left alone, with a warning, when there is none. |
| `.github/workflows/redaction.yml` | Scans pushes to `master` as well as `main`. |
| `scripts/install-hooks.sh` | Says that `core.hooksPath` holds for every checkout of the repository, and names the hooks in `.git/hooks` that stop running once it is set. |
| `scripts/setup.sh` | Leaves `core.hooksPath` as it is where the repository has husky or lefthook, and still finishes. Names the install command for `code-review-graph`. |
| `scripts/verify-agent-ssot.sh` | Requires `.mcp.json`; no longer requires `scripts/ingest.sh`, the two removed hooks or `.githooks/prepare-commit-msg`. Fails when a `.codex/hooks/` file differs from its `.claude/hooks/` original. Its JSON check runs `python3 -I -m json.tool`: without `-I`, `-m` puts the repository's root first on the module path, and a `json.py` there ran inside the setup (measured on 2026-11-13). |
| `scripts/wiki-lint.sh` | Check 8, the slang pattern, is gone. |
| `.agent/workflows/lint.md`, `.agent/workflows/session-start.md` | No longer name the removed slang guard. |
| `.agent/korean-persona.md` | The vocabulary section on that slang is gone; the voice section stays. |
| `docs/wiki/guides/03-host-runtime-and-hooks.md` | Names `.mcp.json` for Claude Code. |
| `docs/wiki/guides/obsidian-setup.md`, `docs/wiki/index.md` | The guide no longer needs a script, and the index lists it, which clears the one warning `wiki-lint.sh` gave on a new install. |

### Added

| Path under `harness/` | What |
|---|---|
| `.mcp.json` | Starts the `code-review-graph` MCP server for Claude Code. Upstream ships that server's config for Cursor and Codex only, while its router tells every agent to consult it first. |
| `.agent/LICENSE-mogui-agent-harness` | Upstream's `LICENSE`, moved. |

File modes are not kept. Upstream marks its scripts and hooks executable; a commit made on Windows records
none of them, and `scripts/harness.py` and the `prepare` line of `scripts/orchestration.py` give the
`git add --chmod=+x` command for a repository that other systems use.

## Ported

| Here | Upstream | Differences |
|---|---|---|
| `scripts/harness.py` | `scripts/ingest.sh` | Python instead of bash with `rsync` or `cp -Rn`. Same four report groups, same rule that nothing existing is overwritten and a differing file gets `<name>.starter`, with one exception: a `.gitignore` of the repository's own gets the lines it lacks appended, in its own line endings, because nearly every repository has one and its starter made every setup wait for the same merge by hand (measured on 2026-11-13 in three runs). Two files that differ only in CRLF against LF count as identical. It stops before the first write when a file sits where a folder is needed, and a run that fails at a later write takes back the files it made. It names a missing `bash`, `code-review-graph` or `gitleaks`. It has no exclusion list, because `harness/` holds only installable files. |
| `scripts/orchestration.py prepare` | The router's "First-entry autonomous bootstrap", which leaves the install to a person and the setup to the first agent session. | The master's wrapper installs the harness into a directory that has none and runs `scripts/setup.sh` there, before a worker starts. It runs that script only where every script and git hook of the harness is the one shipped here (the pages are there to be changed: a rule that asked for every file as shipped never let the setup run once placeholders were filled, measured on 2026-11-13 in two runs), and not where git already runs hooks (files in `.git/hooks`, or a `core.hooksPath` that is set), which the setup's `core.hooksPath` would silence. It refuses a folder inside a repository, a linked worktree, and a repository that git itself refuses to read. The placeholders are filled by `prepare` itself (`harness.fill`), where upstream has an agent interview the owner (`PROMPTS.md` #1): the name, repository and branch come from the folder and git, and every other placeholder gets a neutral value that holds in any project, among them a branch rule that nothing is pushed unless the owner asks. In three runs on 2026-11-13 a master filled 47 placeholders by hand each time, and a placeholder left in stops every worker at its router. A test holds that every placeholder of the tree has a value. `dispatch --project <directory> --worktree new-top-level` gives a worker a worktree of its own there and passes the project's checked-out branch as `--base-branch`. Measured in Orca 1.4.258 on 2026-11-13, on a clone with one commit that was not pushed: without `--base-branch` the new worktree's `baseRef` was `refs/remotes/origin/master` and the commit was missing; with `--base-branch master` it was there. |

## Not run

Nothing is claimed about these: `.github/workflows/redaction.yml`, the Cursor and Windsurf adapters, Linux and
macOS, and a Windows machine whose code page is not UTF-8. `scripts/smoke-codex.sh --live` passed before the changes listed
above and was not run again after them.

## Windows notes

Measured on Windows 11 with Git for Windows 2.47 (bash 5.2), Python 3.14 as `python`, Python 3.12 as
`python3`, Claude Code 2.1.322, Orca 1.4.256 and code-review-graph 2.3.9 (`uv tool install`), in
repositories installed by `scripts/harness.py`.

- Install into an empty folder whose path has a space: 92 files added. There `scripts/setup.sh` exits 0,
  `scripts/verify-agent-ssot.sh` exits 0 (91 OK, 0 FAIL), `scripts/wiki-lint.sh` passes with no warning,
  `scripts/decisions-index.sh --check` exits 0 and the static part of `scripts/smoke-codex.sh` gives 9 PASS.
- A Claude Code session in that folder (`claude -p`, haiku): the five session-start hooks and the prompt hook ran
  without an error, a Write holding an emoji was denied and its file was not created, a plain Write passed, and
  `core.hooksPath` was still unset afterwards.
- The emoji hook gives the same decisions from an LF and from a CRLF copy of itself.
- `code-review-graph`: `build` took 1.7 s on 31 files, `update --skip-flows` 0.7 s and `status` 0.6 s. With no
  graph, `status` exits 1 and says so on stderr, and `update --skip-flows` builds one and exits 0; outside a git
  repository `update` exits 1. Its MCP server connected from `.mcp.json` with 30 tools.
  `claude mcp get code-review-graph` shows it pending approval in a folder that is not trusted and connected in
  a trusted one, which is what the Claude Code documentation says of `enabledMcpjsonServers`.
- A worker dispatched through this plugin's gate (Orca, sonnet) into an existing repository finished the install
  there: no approval prompt, 38 hook runs without an error, and two calls of the graph's MCP tools. The same
  dispatch with upstream's wiring logged 17 hook errors: the `code-review-graph` command was missing, and the
  session-start write of `core.hooksPath` failed once with `could not write config file .git/config: Permission
  denied`. Why that write was refused was not found; `setup-check.sh` no longer makes it.
- One thing a worker met there is upstream behaviour and unchanged: the front matter of
  `.agent/workflows/*.md` keeps its `YYYY-MM-DD` and `@git_author` placeholders.
- A commit of the whole install with `core.hooksPath` set leaves nothing staged or changed behind it. A test
  holds that, and it fails with upstream's `.githooks/prepare-commit-msg` put back.
- With `gitleaks` 8.30.1 on the path, the release the workflow pins: the commit of a whole install passes the
  hook's scan (0 findings in 92 files), a commit of one clean code file is scanned and passes, and a commit of one
  code file holding the synthetic AWS key of the gate's own tests is refused with `[aws_access_key]` while `HEAD`
  stays where it was. `tests/redaction-gate/run.sh` is written for the workflow's Linux runner and does not pass
  on Windows: 18 cases pass, 2 fail (the malformed-report case and the missing-Python case), and it stops at its
  first symbolic-link case, where `ln -s` fails.
- The harness ships no `.gitattributes`, so with `core.autocrlf=true` (the default of Git for Windows) a new
  worktree holds its hooks and scripts with CRLF, while the commit holds LF. Measured on 2026-11-13 in such a
  worktree (`.githooks/pre-commit` with 187 CR bytes): `git hook run pre-commit` and `bash .githooks/pre-commit`
  ran to the end with exit 0, as did `scripts/verify-agent-ssot.sh` and `.claude/hooks/setup-check.sh`. Another
  bash (WSL, Cygwin) was not measured.
- `core.hooksPath` set from a linked worktree is read by the main checkout too, and a `pre-commit` in its
  `.git/hooks` that had blocked a commit no longer ran (measured with a hook that exits 1).
- Upstream's `.githooks/pre-commit`, in a repository with `core.autocrlf=false` (measured on 2026-11-13): a
  commit of an LF page under `docs/` whose `updated_at` it bumps is recorded as CRLF (`git ls-files --eol`:
  `i/crlf w/crlf`); a staged `docs/한글.md` keeps its old `updated_at`; a commit whose identity comes
  from `GIT_AUTHOR_NAME` and `GIT_COMMITTER_NAME` alone exits 1 with no message; and a page that is not
  UTF-8 is listed under `[bumped]` while its `updated_at` stays as it was.
- `git rev-parse --path-format=absolute --git-common-dir`, which `setup-check.sh` uses to find the main
  checkout, needs git 2.31 or later. With an older git the hook reports as it did before (not run against
  an older git).

## Updating to a newer upstream commit

Not exercised. Diff the two upstream commits in a clone of the source, apply that diff here with
`git apply --directory=harness`, resolve what touches a file listed under "What differs from upstream", run the
tests, and update the commit and the counts above.
