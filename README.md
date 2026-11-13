# orchestration-kit

A Claude Code plugin for Windows that adds a thin orchestration layer on top of the Orca CLI, reassembled
from [baksohyeon/mogui-ADE-orchestrator](https://github.com/baksohyeon/mogui-ADE-orchestrator) and
[baksohyeon/mogui-agent-harness](https://github.com/baksohyeon/mogui-agent-harness). Orca keeps its workers,
Runs and mail. The plugin adds:

- **Dispatch gate and ledger**: a worker starts only after its contract passes the gate, and every decision
  is appended to a ledger. A retry or a fix is one more attempt at the same task (`dispatch --follows`).
- **Project directories made ready**: `dispatch --project <directory>` adds the directory to Orca and
  installs the repository harness before the worker starts, without overwriting anything.
- **A wait that survives an Orca restart**: a read-only `watch` that wakes the master when Orca does not.
- **Master succession**: a checked handoff, a successor launched in Orca, and a lineage ledger.
- **Review workflow**: `/orchestration-kit:orchestration-review` fans a diff out to reviewers and verifies
  every blocking finding.
- **Repository harness**: the rules a worker follows inside one repository (`harness/`).

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

In a Claude Code session started in an Orca terminal, ask it to act as master. The
`orchestration-kit:master` skill (`skills/master/SKILL.md`) holds the whole procedure, and each script
explains its commands with `--help`.

| Piece | Path |
|---|---|
| Wrapper (`prepare`, `dispatch`, `watch`, `succeed`, `inherit`, `report`) | `scripts/orchestration.py` |
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
