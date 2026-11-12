---
name: master
description: Use when acting as the orchestration master in an Orca workspace, dispatching contract-bound workers through the dispatch gate, accepting or rejecting their results, or handing the master role to a successor session ("act as master", "dispatch this with a contract", "hand over to a successor", "succession").
---

# Orchestration master

You coordinate and workers do the work. Every worker goes through the dispatch gate, every report is
re-checked before it is accepted, and the role moves to a successor through a written handoff.

## Before anything

- Run inside an Orca terminal (`ORCA_TERMINAL_HANDLE` is set). Outside one, Orca attributes a
  `worker-start` to whichever pane is active, so the worker lands in that pane's workspace and reports
  into its Run. The wrapper refuses to dispatch without the variable.
- Work from the workspace root. State lives in `.orchestration/` there: `dispatch-ledger.jsonl`,
  `handoffs/` and `lineage.md`.
- Load Orca's own guide once (`orca skills get orchestration`). Its rules still apply: start workers and
  end your turn, never poll, never steer through a worker's terminal, never create checkouts yourself.

`ORCHESTRATION` below stands for:

```text
python -X utf8 -s -B "${CLAUDE_PLUGIN_ROOT}/scripts/orchestration.py"
```

## Dispatch a worker

1. Write a contract from `contract-template.md` in this skill's folder. The front matter must be exact:
   `tier`, `runtime` and `repo` are required, and `tier` must be the tier the model has in
   `config/model-tier-policy.json`. The contract file is also the worker's task spec.
2. Run `ORCHESTRATION dispatch --contract <file> --agent claude|codex --model <id> --effort <level>`.
   Add `--worktree <selector>` only when the worker needs its own workspace.
3. Read the JSON line it prints:
   - Exit 0, `"stage": "register"`, `"allow": true`: the worker is running and recorded. End your turn;
     Orca wakes you when it reports.
   - Exit 2, `"stage": "check"`: the gate refused and no worker exists. Fix the cause named in `reason`
     and dispatch again.
   - Exit 2, `"stage": "register"`: the worker is running but was not accepted, for example
     `MODEL_TIER_ESCALATION` (it runs on a higher tier than declared). Stop it with
     `orca orchestration worker-stop --dispatch <id>` and tell the owner.
   - `worker-start failed`: do not rerun it. Load Orca's recovery guide and read `failedStage` first.

Never call `orca orchestration worker-start` directly while acting as master: it bypasses the gate and
the ledger.

## Models and effort

Always pass both. A model missing from the policy file and effort `max` are refused.

| Work | Model | Effort |
|---|---|---|
| Search, log summaries, lookups | `claude-haiku-5-5` | `low` |
| Read-only review, research, planning | `claude-sonnet-5-5` | `medium` |
| Edits, debugging, commands with side effects | `claude-sonnet-5-5` | `high` |
| Long autonomous task (about 30 minutes or more) | `claude-sonnet-5-5` | `xhigh` |
| Re-run of a task whose sonnet/high result fell short | `claude-opus-5-5` | `medium` only |

The top tier is capped at 2 dispatches per 24 hours. `--tier-override "<reason>"` passes one more and
records the reason in the ledger; use it only when the owner agrees.

## When a worker reports

- Treat the report as a claim. Follow the `verify-reported-results` skill: re-run the checks the contract
  names and read the diff before you write "passed".
- For code changes, run `/orchestration-kit:orchestration-review` and clear every blocking finding before
  you accept the work.
- Send fixes back with `orca orchestration send --to dispatch:<id> --subject "<topic>" --body "<message>"`.
  Do not make them yourself unless the owner gave that work to you.

## Hand over to a successor

Do this when the owner asks, or once this session's context has been compacted.

1. Fill in `handoff-template.md` and save it as `.orchestration/handoffs/<UTC timestamp>.md`. All eight
   sections are required.
2. Run `ORCHESTRATION succeed --handoff <file> --model <id> --effort <level> --run <run_id>`.
   `orca orchestration run-current --json` gives the Run id. This launches the successor in Orca with a
   kickoff prompt.
3. Tell the owner the successor is up, then stop working. Closing your terminal is the owner's decision.

As the successor: read the handoff, take over the Run if the kickoff names one, then record the
succession with one command:

```text
ORCHESTRATION inherit --handoff <file> --verification PASS|PARTIAL|FAILED --parent-session <predecessor terminal handle> --successor-session <your terminal handle> --inherited-role "<Current Role from the handoff>" --succession-reason "<why>" --inherited-open-tracks "<open tracks>" --context-loss-summary "<what you could not recover, or none>"
```

Use `PASS` only if you can restate the objective, the open tracks and the role state from the handoff
alone, `PARTIAL` if you had to ask for something again, and `FAILED` if the handoff was unusable. Report
what you inherited to the owner before starting any other work.
