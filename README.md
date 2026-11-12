# orchestration-kit

A Claude Code plugin that adds a thin orchestration layer on top of the Orca CLI, reassembled for
Windows from [baksohyeon/mogui-ADE-orchestrator](https://github.com/baksohyeon/mogui-ADE-orchestrator).
Orca keeps doing what it already does (workers, Runs, mail). This plugin adds what it does not:

- **Dispatch gate and ledger.** A worker starts only after its contract passes the gate (front matter,
  budget, model tier, fan-out cap). Every decision is appended to a JSONL ledger, and the model the worker
  really runs on is compared with the declared one.
- **Master succession.** A written handoff, a successor launched in Orca, and an append-only lineage ledger.
- **Review workflow.** `/orchestration-kit:orchestration-review` fans a diff out to reviewers, removes
  duplicates and has every CRITICAL or HIGH finding checked by an independent verifier.

## Requirements

Windows, Python 3.12 or later on `PATH` as `python`, Claude Code, and the Orca desktop app with its `orca`
CLI. The master session must run inside an Orca terminal.

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
| Wrapper (`dispatch`, `succeed`, `inherit`) | `scripts/orchestration.py` |
| Model tiers and caps | `config/model-tier-policy.json` |
| Contract and handoff templates | `skills/master/` |
| Review command, workflow and agents | `commands/`, `review/`, `agents/` |
| State, per workspace | `.orchestration/` in the workspace root |

## Tests

```text
python -X utf8 -s -B -m unittest discover -s tests -v
```

The tests run the real vendored gate against a fake Orca CLI (`tests/fake-orca.cmd`).

## Provenance

`vendor/mogui/UPSTREAM.md` lists the upstream commit, every copied file, what was changed, and what was
left out. Upstream is MIT licensed; its license texts are in `vendor/mogui/`.
