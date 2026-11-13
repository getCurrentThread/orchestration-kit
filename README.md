# orchestration-kit

A Claude Code plugin that adds a thin orchestration layer on top of the Orca CLI, reassembled for
Windows from [baksohyeon/mogui-ADE-orchestrator](https://github.com/baksohyeon/mogui-ADE-orchestrator)
and its sibling [baksohyeon/mogui-agent-harness](https://github.com/baksohyeon/mogui-agent-harness).
Orca keeps doing what it already does (workers, Runs, mail). This plugin adds what it does not:

- **Dispatch gate and ledger.** A worker starts only after its contract passes the gate (front matter,
  budget, model tier, fan-out cap). Every decision is appended to a JSONL ledger, and the model the worker
  really runs on is compared with the declared one.
- **A wait that survives an Orca restart.** Orca wakes a waiting master by typing into its terminal, and
  after a restart it does not. A read-only watch that the master keeps in the background wakes it for
  unread reports and for workers that stopped without one.
- **Master succession.** A written handoff, a successor launched in Orca, and an append-only lineage ledger.
- **Review workflow.** `/orchestration-kit:orchestration-review` fans a diff (uncommitted changes, a branch
  against its base, or a GitHub PR) out to reviewers, removes duplicates and has every CRITICAL or HIGH
  finding checked by an independent verifier.
- **Repository harness.** The rules a worker follows inside one repository, derived from the sibling
  project [baksohyeon/mogui-agent-harness](https://github.com/baksohyeon/mogui-agent-harness): a router
  (`CLAUDE.md`, `AGENTS.md`) that points at `.agent/` (rules, context, memory), `.planning/` (work in
  progress) and `docs/wiki/` (decisions, guides, postmortems), with session and git hooks and a code graph
  over MCP. `scripts/harness.py` installs it into a repository without overwriting anything.

The first four are the workspace layer and the last is the repository layer. They share no code: the
master hands a worker a contract, and the repository the worker starts in supplies its own rules.

## Requirements

Windows, Python 3.12 or later on `PATH` as `python`, Claude Code, and the Orca desktop app with its `orca`
CLI. The master session must run inside an Orca terminal.

The repository harness is bash: its hooks and scripts need Git for Windows. Installing it does not. Two of
its parts are optional and do nothing until their tool is on `PATH`: the code graph needs
[code-review-graph](https://github.com/tirth8205/code-review-graph) (`uv tool install code-review-graph`, or
`pip install code-review-graph`), and the secret scan of every commit needs
[gitleaks](https://github.com/gitleaks/gitleaks).

## Install

From GitHub, in a shell:

```text
claude plugin marketplace add getCurrentThread/orchestration-kit
claude plugin install orchestration-kit@orchestration-kit
```

Claude Code clones the repository with `git` and keeps its own copy of the plugin. To move to a later
release, run `claude plugin update orchestration-kit@orchestration-kit` and start a new session. A new copy
arrives only when `version` in `.claude-plugin/plugin.json` has changed.

From a clone, to work on the plugin itself:

```text
git clone https://github.com/getCurrentThread/orchestration-kit.git
claude plugin marketplace add ./orchestration-kit
claude plugin install orchestration-kit@orchestration-kit
```

A marketplace added from a folder is read in place: an edit in the clone takes effect at the next session
start or `/reload-plugins`, whatever the version says. To try the plugin in one session without installing:
`claude --plugin-dir <path to the clone>`.

## Use

In a Claude Code session started in an Orca terminal, ask it to act as master; the
`orchestration-kit:master` skill holds the procedure. The pieces it uses:

| Piece | Path |
|---|---|
| Wrapper (`dispatch`, `watch`, `succeed`, `inherit`) | `scripts/orchestration.py` |
| Model families, tiers and caps (no versioned ids) | `config/model-tier-policy.json` |
| Contract and handoff templates | `skills/master/` |
| Review command, workflow and agents | `commands/`, `review/`, `agents/` |
| State, per workspace | `.orchestration/` in the workspace root |
| Repository harness installer | `scripts/harness.py` |
| Repository harness files | `harness/` |

### Install the repository harness

Ask the master to install it: its skill carries the command with the plugin's own path. By hand, from a
clone:

```text
python -X utf8 -s -B <path to the clone>/scripts/harness.py <repository root> --dry-run
python -X utf8 -s -B <path to the clone>/scripts/harness.py <repository root>
```

A file the repository does not have is added. A file it has is never touched: when it differs from the
harness's, the harness's version is written beside it as `<name>.starter` for a merge by hand. The command
prints what it did and the steps that remain, and names what the machine lacks (`bash`,
`code-review-graph`). It stops before writing anything when a file sits where the harness needs a folder.

`harness/` holds exactly the files that are installed. The first session in the repository finishes the
job: its router tells the agent to report the `*.starter` files, fill the `{{...}}` placeholders and run
`bash scripts/setup.sh`. The installer never sets `core.hooksPath`; that script does, and it leaves a
repository's husky or lefthook chain as it is. The setting is shared by every checkout of the repository,
linked worktrees included, and git stops running the hooks in `.git/hooks` once it is set: the script names
those hooks so that they can be merged into `.githooks/` by hand. The pre-commit hook keeps `updated_at`
current in the pages under `docs/` and scans every commit for secrets where `gitleaks` is installed.

The harness gives every session in the repository a map of the code through `code-review-graph`:

- `.mcp.json` starts its MCP server. `.claude/settings.json` approves that server, which Claude Code
  honors once the folder is trusted, so a worker is not stopped by an approval prompt.
- A session-start hook reports the state of the graph, and a hook after every Edit, Write or Bash call
  keeps it current. The first of those calls builds it.

None of this does anything until the tool is installed.

## Tests

```text
python -X utf8 -s -B -m unittest discover -s tests -v
```

The tests run the real vendored gate against a fake Orca CLI and a fake Claude CLI (`tests/fake-orca.cmd`,
`tests/fake-claude.cmd`), the installer against the real harness tree, the harness's write guard, hook setup
and pre-commit hook through Git Bash, and the review workflow's script under Node with scripted agents
(skipped where `node` is not on `PATH`).

## Provenance

`vendor/mogui/UPSTREAM.md` and `vendor/mogui-agent-harness/UPSTREAM.md` list the upstream commit, what
was copied, what was changed, what was left out, and what was measured on Windows. `vendor/mogui/` is
byte-identical to its upstream; `harness/` is derived from its upstream and maintained here.

## License

MIT: see `LICENSE`. What was taken from other projects keeps its own MIT notice:

| Notice | Covers |
|---|---|
| `vendor/mogui/LICENSE` | mogui-ADE-orchestrator: every file `vendor/mogui/UPSTREAM.md` lists as copied or modified |
| `vendor/mogui/LICENSE-ECC` | [ECC](https://github.com/affaan-m/ECC), from which the review agents and workflow derive |
| `harness/.agent/LICENSE-mogui-agent-harness` | mogui-agent-harness: `harness/`. It travels with every install of the harness |
