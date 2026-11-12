---
name: master
description: Use when acting as the orchestration master in an Orca workspace, dispatching contract-bound workers through the dispatch gate, accepting or rejecting their results, handing the master role to a successor session, or installing the repository harness that workers follow inside a repository ("act as master", "dispatch this with a contract", "hand over to a successor", "succession", "install the agent harness in this repo").
---

# Orchestration master

You coordinate and workers do the work. Every worker goes through the dispatch gate, every report is
re-checked before it is accepted, and the role moves to a successor through a written handoff.

## Before anything

- Run inside an Orca terminal (`ORCA_TERMINAL_HANDLE` is set). Outside one, Orca attributes a
  `worker-start` to whichever pane is active, so the worker lands in that pane's workspace and reports
  into its Run. The wrapper refuses to dispatch without the variable.
- If you are not in one, do not work around the refusal. Tell the owner, who can continue this conversation
  from an Orca terminal on this checkout (`claude --resume`), or hand over to a successor (see "Hand over to a
  successor"). `succeed` works from any terminal: Orca starts the successor in the workspace that holds your
  current folder, so run it from the workspace root of a repository that `orca repo list` shows.
- Work from the workspace root. State lives in `.orchestration/` there: `dispatch-ledger.jsonl`,
  `handoffs/` and `lineage.md`.
- Load Orca's own guide once (`orca skills get orchestration`). Its rules still apply: start workers and
  end your turn, never poll, never steer through a worker's terminal, never create checkouts yourself.

`ORCHESTRATION` below stands for:

```text
python -X utf8 -s -B "${CLAUDE_PLUGIN_ROOT}/scripts/orchestration.py"
```

## Repository harness

A worker follows the rules of the repository it starts in, not yours. The repository harness carries those
rules: a router (`CLAUDE.md`, `AGENTS.md`) that points at `.agent/` (rules, context, memory), `.planning/`
(work in progress) and `docs/wiki/` (decisions, guides, postmortems), with session and git hooks.

- Before the first dispatch into a repository, check that `.agent/Instructions.md` exists there. If it does
  not, ask the owner whether to install the harness: it adds about ninety files to that repository.
- Install with the command below, first with `--dry-run`. Nothing existing is overwritten: a file that
  differs from the harness's gets the harness's version beside it as `<name>.starter`.

  ```text
  python -X utf8 -s -B "${CLAUDE_PLUGIN_ROOT}/scripts/harness.py" <repository root> [--dry-run]
  ```

- Report the staged `*.starter` files and everything the command names as missing to the owner, and merge
  only as the owner decides. Then follow the steps the command prints.
- The harness gives every session in the repository a map of the code: the `code-review-graph` MCP server
  and two hooks that keep its graph current. Installing that tool is the owner's step, and the command
  prints how. Without it the harness still works.
- A worker with its own workspace (`--worktree`) only sees a harness that is committed.
- Keep repository rules out of contracts. The contract says what to do; the repository says how.

## Dispatch a worker

1. Write a contract from `contract-template.md` in this skill's folder. The front matter must be exact:
   `tier`, `runtime` and `repo` are required, and `tier` must be the tier the model's family has in
   `config/model-tier-policy.json`. The contract file is also the worker's task spec.
2. Run `ORCHESTRATION dispatch --contract <file> --agent claude|codex --model <family> --effort <level>`.
   Add `--worktree <selector>` only when the worker needs its own workspace.
3. Read the JSON line it prints:
   - Exit 0, `"stage": "register"`, `"allow": true`: the worker is running and recorded, and `dispatch` and
     `run` hold the ids that the commands below take. Do what `next` says: keep a watch running (see "Wait
     for workers"), then end your turn. Orca wakes you when the worker reports.
   - Exit 2, `"stage": "check"`: the gate refused and no worker exists. Fix the cause named in `reason`
     and dispatch again.
   - Exit 2, `"stage": "register"`: the worker is running but was not accepted, for example
     `MODEL_TIER_ESCALATION` (it runs on a higher tier than declared). Stop it with
     `orca orchestration worker-stop --dispatch <id>` and tell the owner.
   - `worker-start failed`: do not rerun it. Load Orca's recovery guide and read `failedStage` first.

Never call `orca orchestration worker-start` directly while acting as master: it bypasses the gate and
the ledger.

## Models and effort

Always pass `--effort`, and `--model` for a Claude worker. Name the model by its family, as
`config/model-tier-policy.json` lists it, and never write a version number: at dispatch the wrapper asks
Claude Code which version the family means now and starts the worker on that. A full model id is only for
pinning a version on purpose, and it is gated as its family. A model the policy does not cover and effort
`max` are refused.

| Work | Model | Effort |
|---|---|---|
| Search, log summaries, lookups | `haiku` | `low` |
| Read-only review, research, planning | `sonnet` | `medium` |
| Edits, debugging, commands with side effects | `sonnet` | `high` |
| Long autonomous task (about 30 minutes or more) | `sonnet` | `xhigh` |
| Re-run of a task whose sonnet/high result fell short | `opus` | `medium` only |

For `--agent codex`, leave `--model` out: the wrapper passes the model set in `~/.codex/config.toml`.

The top tier is capped at 2 dispatches per 24 hours. `--tier-override "<reason>"` passes one more and
records the reason in the ledger; use it only when the owner agrees.

## Wait for workers

Orca wakes you by typing into your terminal. After a restart it types into no terminal it has not heard from
since, so a report that arrives then stays unread while you sit idle. Never wait on Orca alone: before you
end a turn with a dispatch open, have one watch per Run running in the background (Bash with
`run_in_background`):

```text
ORCHESTRATION watch --run <run_id>
```

Orca's rule against watch scripts is about `check`, which takes your mail. This watch only reads. It is
silent while Orca does its job. When it ends you are woken, and its output says why:

- `... are unread and Orca has not woken you`: read the mail with the command it names.
- `... is open and its worker's screen in <terminal> has not changed for ...`: Orca does not show the worker
  working and its screen has stood still that long. A working agent keeps a timer moving on its screen even
  while its command prints nothing, so this worker has most likely stopped without a report. Read its screen
  with the command the line names. If it is back at its prompt, see "After an Orca restart". Only if the
  screen shows it still at work, start the watch again with a longer `--stalled-after <seconds>`.
- `... has settled and its mail is read`: nothing is left to wait for.
- The watch failed (`Orca has refused to show <run> ...`, or `the Orca CLI ... cannot be run`): nothing is
  watching the Run. Waiting does not mend either. Fix what the line says and start the watch again.

After you have dealt with a wake, start the watch again if a dispatch is still open. Never run
`orca orchestration check` in a loop or a script: it takes your mail.

## When a worker reports

- Treat the report as a claim. Follow the `verify-reported-results` skill: re-run the checks the contract
  names and read the diff before you write "passed".
- For code changes, run `/orchestration-kit:orchestration-review` and clear every blocking finding before
  you accept the work. With no argument it reviews uncommitted changes. Give it the base branch when the
  worker committed its work, for example `/orchestration-kit:orchestration-review master`.
- Send fixes back with `orca orchestration send --to dispatch:<id> --subject "<topic>" --body "<message>"`.
  Do not make them yourself unless the owner gave that work to you.
- `orca orchestration inbox --terminal run:<run_id> --limit 50 --full` shows mail you have already read.

## After an Orca restart

The watch says when Orca was restarted, and it ends only once Orca is running again, so the commands below
work. Workers and their sessions survive a restart. What Orca loses is its view of them: it lists each one as
`unverifiable` until it hears from it again, and it may refuse a `worker_done` sent right after the restart.

- A report whose subject starts with `Rejected worker_done` is the worker's real report, and its dispatch is
  still open. Verify the work as for any report.
- A worker that finished while Orca was down could not send anything: its dispatch is open, no report came,
  and its screen shows the report it could not send.
- In both cases the work is done and only the report is missing. Never dispatch the contract again. Ask the
  worker to send it again:

  ```text
  orca orchestration send --to dispatch:<id> --subject "Send worker_done again" --body "Orca was restarted and did not take your worker_done. Do not redo any work and change no file. Send the same worker_done again."
  ```

- Orca cannot wake a worker it has not heard from since the restart: the watch gave that worker's liveness
  as `unverifiable`. Only then, after the `send`, type Orca's own notice into the worker's terminal, which
  the watch named:

  ```text
  orca terminal send --terminal <handle> --text "You have 1 orchestration message. Run orca orchestration check." --enter
  ```

- Start the watch again and end your turn. The second `worker_done` settles the dispatch.

## Hand over to a successor

Do this when the owner asks, or once this session's context has been compacted.

1. Fill in `handoff-template.md` and save it as `.orchestration/handoffs/<UTC timestamp>.md`. All eight
   sections are required.
2. Run `ORCHESTRATION succeed --handoff <file> --model <family> --effort <level> --run <run_id>`.
   `orca orchestration run-current --json` gives the Run id. This launches the successor in Orca with a
   kickoff prompt.
3. Stop your watch, the background task: the Run's mail is the successor's from now on. Tell the owner the
   successor is up, then stop working. Closing your terminal is the owner's decision.

As the successor: read the handoff, take over the Run if the kickoff names one, then record the
succession with one command:

```text
ORCHESTRATION inherit --handoff <file> --verification PASS|PARTIAL|FAILED --parent-session <predecessor terminal handle> --successor-session <your terminal handle> --inherited-role "<Current Role from the handoff>" --succession-reason "<why>" --inherited-open-tracks "<open tracks>" --context-loss-summary "<what you could not recover, or none>"
```

A predecessor that did not run in Orca has no terminal handle: give its session name as `--parent-session`.

Use `PASS` only if you can restate the objective, the open tracks and the role state from the handoff
alone, `PARTIAL` if you had to ask for something again, and `FAILED` if the handoff was unusable. Then report
what you inherited to the owner, before any other work.

- After `PASS`, do not end your turn on that report. It is not a question and nobody answers it, so an idle
  successor stalls the work. Carry on with the next step the handoff names, unless the handoff says that step
  waits for the owner. If the Run has a dispatch open, start your own watch on it.
- After `PARTIAL` or `FAILED`, stop after the report: the owner has to supply what the handoff lost.
