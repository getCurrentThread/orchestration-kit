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

The repository harness is bash: its hooks and scripts need Git for Windows. Installing it does not. Its
code graph is optional and needs [code-review-graph](https://github.com/tirth8205/code-review-graph) on
`PATH`: `uv tool install code-review-graph`, or `pip install code-review-graph`.

## Install

```text
claude plugin marketplace add <path to this folder>
claude plugin install orchestration-kit@orchestration-kit
```

To try it in one session without installing: `claude --plugin-dir <path to this folder>`.

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

```text
python -X utf8 -s -B <path to this folder>/scripts/harness.py <repository root> --dry-run
python -X utf8 -s -B <path to this folder>/scripts/harness.py <repository root>
```

A file the repository does not have is added. A file it has is never touched: when it differs from the
harness's, the harness's version is written beside it as `<name>.starter` for a merge by hand. The command
prints what it did and the steps that remain, and names what the machine lacks (`bash`,
`code-review-graph`). It stops before writing anything when a file sits where the harness needs a folder.

`harness/` holds exactly the files that are installed. The first session in the repository finishes the
job: its router tells the agent to report the `*.starter` files, fill the `{{...}}` placeholders and run
`bash scripts/setup.sh`. That script is the only thing that sets `core.hooksPath`, and it leaves a
repository's husky or lefthook chain as it is.

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

The tests run the real vendored gate against a fake Orca CLI (`tests/fake-orca.cmd`), the installer
against the real harness tree, the harness's write guard through Git Bash, and the review workflow's script
under Node with scripted agents (skipped where `node` is not on `PATH`).

## Provenance

`vendor/mogui/UPSTREAM.md` and `vendor/mogui-agent-harness/UPSTREAM.md` list the upstream commit, what
was copied, what was changed, what was left out, and what was measured on Windows. `vendor/mogui/` is
byte-identical to its upstream; `harness/` is derived from its upstream and maintained here. Both
upstreams are MIT licensed; their license texts are in `vendor/mogui/` and
`harness/.agent/LICENSE-mogui-agent-harness`.
