# orchestration-kit

A Claude Code plugin for Windows that adds a thin orchestration layer on top of the Orca CLI, reassembled
from [baksohyeon/mogui-ADE-orchestrator](https://github.com/baksohyeon/mogui-ADE-orchestrator) and
[baksohyeon/mogui-agent-harness](https://github.com/baksohyeon/mogui-agent-harness). Orca keeps its workers,
Runs and mail.

![Who does what. The owner asks the master and the master reports back. A contract goes from the master through the dispatch gate, which notes it in the ledger, into Orca, where a worker starts in a terminal of its own. The worker's report, worker_done, comes back to the master as Orca mail, and watch wakes the master when that mail does not arrive.](docs/overview-animated.svg)

The plugin adds:

- **Dispatch gate and ledger**: a worker starts only after its contract passes the gate, and every decision
  is appended to a ledger. A retry or a fix is one more attempt at the same task (`dispatch --follows`). A
  contract's `budget: [tokens=N]` is recorded at dispatch, and `report` measures each settled worker's usage
  from its session file and shows it against the budget.
- **Project directories made ready**: `dispatch --project <directory>` adds the directory to Orca and
  installs the repository harness before the worker starts, without overwriting anything.
- **A wait that survives an Orca restart**: a read-only `watch` that wakes the master when Orca does not.
- **Resume a worker whose agent ended**: `resume --dispatch <id>` brings the Claude session back in the worker's
  own terminal, once the screen proves the agent ended, and it reports for the same dispatch.
- **Where a dispatch stands**: `status` says, for every dispatch that `report` has not measured, whether Orca shows it
  open, settled, unknown, gone or unreadable, and the step it waits for. It only reads.
- **No worker beside one the gate refused**: a worker the gate refuses at register already runs, so every dispatch
  is refused until Orca shows it settled. `override-hold --dispatch <id> --reason "<why>"` ends that hold on the
  owner's word, and only while Orca shows the refused worker's agent exited.
- **Master succession**: a dispatch line that says when the master's context is 80% of the way to its compaction, a checked handoff, a successor launched in Orca, a lineage ledger, and the predecessor's terminal closed by Orca after a recorded `PASS`.
- **Review workflow**: `/orchestration-kit:orchestration-review` fans a diff out to reviewers, verifies
  every blocking finding, and names every changed file outside the contract's `writes`.
- **Repository harness**: the rules a worker follows inside one repository (`harness/`).

## How it works

The parts by name, and who calls what. A solid arrow acts and a dashed one only reads:

![The owner asks the master, and the master runs the commands of scripts/orchestration.py. That wrapper has the dispatch gate check and register every start, the gate appends to the ledger under .orchestration/, and the wrapper asks Orca to start the worker. Orca starts the worker in its workspace, which prepare gave the harness rules. The worker reports with worker_done, and Orca's mail wakes the master; watch wakes it when Orca does not. The review workflow reads the worker's diff.](docs/overview.svg)

One dispatch, from its contract to the merge, and the four ways it can go differently:

![The usual path: contract, check, start, register, work, verify, accept. A: the gate says no before the start, so no worker runs; fix what the line says and dispatch again. B: the gate says no after the start, so the worker already runs and nobody else starts until it is settled. C: the worker goes quiet; watch rings and resume carries on the same dispatch. D: the result is not what the contract asked; dispatch --follows sends the next attempt at the same task, three at most.](docs/dispatch-lifecycle-comic.svg)

The master role moves to a successor session through a written handoff:

![Six steps. The master's memory fills up, or the owner asks for a handover. The old master writes a handoff note. succeed checks the note and launches a successor in Orca. The successor reads the note and takes over the Run's mail. inherit records the result in the lineage. On PASS, Orca closes the old terminal, and workers keep running.](docs/succession-animated.svg)

## Requirements

Windows, Python 3.12 or later on `PATH` as `python`, Claude Code, and the Orca desktop app with its `orca`
CLI. The harness's hooks and scripts are bash and need Git for Windows. Two tools are optional, and their
parts of the harness do nothing without them: `code-review-graph` (the code graph) and `gitleaks` (the
secret scan of every commit).

## Install

```text
claude plugin marketplace add getCurrentThread/orchestration-kit
claude plugin install orchestration-kit@orchestration-kit
```

`claude plugin update orchestration-kit@orchestration-kit` fetches a new copy only when `version` in
`.claude-plugin/plugin.json` has changed. To work on the plugin, add the marketplace from a clone instead
(`claude plugin marketplace add ./orchestration-kit`): it is read in place, so an edit takes effect at the
next session start or `/reload-plugins`. `claude --plugin-dir <clone>` tries it in one session.

## Use

Ask a Claude Code session to act as master (`/orchestration-kit:master`). In an Orca terminal that session
is the master. In any other terminal it starts Orca if Orca is closed, and carries the conversation into a
fresh master in an Orca terminal on the same checkout (`start`). The `orchestration-kit:master` skill
(`skills/master/SKILL.md`) holds the whole procedure, and each script explains its commands with `--help`.

The wrapper's ten commands, in the order a master meets them:

![Set up: start opens a fresh master in Orca, and prepare gets a project folder ready. Run work: dispatch sends one contract through the gate and starts its worker, and watch wakes the master when Orca does not. Look: status says where each dispatch stands, and report counts the tokens each used against its budget. Recover: resume wakes a worker whose agent ended before it reported, and override-hold lifts a hold on the owner's word. Hand over: succeed checks the handoff note and calls in a successor, and inherit records the succession.](docs/commands-comic.svg)

| Piece | Path |
|---|---|
| Wrapper (`prepare`, `dispatch`, `watch`, `resume`, `start`, `succeed`, `inherit`, `report`, `status`, `override-hold`) | `scripts/orchestration.py` |
| Repository harness installer (`<repository root> [--dry-run]`) | `scripts/harness.py` |
| Repository harness files | `harness/` |
| Model families, tiers and caps | `config/model-tier-policy.json` |
| Contract and handoff templates | `skills/master/` |
| Session-start hook, after a compaction only | `hooks/hooks.json` |
| Review command, workflow and agents | `commands/`, `review/`, `agents/` |
| State, per workspace | `.orchestration/` in the workspace root |

## Tests

```text
python -X utf8 -s -B -m unittest discover -s tests -v
```

They run the vendored gate against a fake Orca and a fake Claude CLI (`tests/fake-orca.cmd`,
`tests/fake-claude.cmd`). The harness's hooks need Git Bash and the review workflow needs Node: without one,
those tests are skipped. `.github/workflows/tests.yml` runs everything on `windows-latest` and fails when
either is missing.

## Provenance and license

MIT: see `LICENSE`. `vendor/mogui/` is byte-identical to its upstream, and `harness/` is derived from its
upstream and maintained here. The two `UPSTREAM.md` files list the upstream commit, what was copied and what
was changed. What was taken from other projects keeps its own MIT notice:

| Notice | Covers |
|---|---|
| `vendor/mogui/LICENSE` | mogui-ADE-orchestrator: every file `vendor/mogui/UPSTREAM.md` lists as copied or modified |
| `vendor/mogui/LICENSE-ECC` | [ECC](https://github.com/affaan-m/ECC), from which the review agents and workflow derive |
| `harness/.agent/LICENSE-mogui-agent-harness` | mogui-agent-harness: `harness/`. It travels with every install of the harness |
