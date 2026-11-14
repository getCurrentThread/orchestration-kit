---
name: master
description: Use when acting as the orchestration master in an Orca workspace, dispatching contract-bound workers through the dispatch gate, making a project directory ready for a worker (known to Orca, carrying the repository harness), accepting or rejecting their results, handing the master role to a successor session, or installing the repository harness that workers follow inside a repository ("act as master", "dispatch this with a contract", "do this in that project folder", "hand over to a successor", "succession", "install the agent harness in this repo").
---

# Orchestration master

You coordinate and workers do the work. Every worker goes through the dispatch gate, every report is
re-checked before it is accepted, and the role moves to a successor through a written handoff.

The wrapper says what to do next: a refusal carries a `message`, and every `attention` entry and every line
of the watch names its step. Follow those. This skill holds what they do not say. `ORCHESTRATION` stands for:

```text
python -X utf8 -s -B "${CLAUDE_PLUGIN_ROOT}/scripts/orchestration.py"
```

## Before anything

- A master runs in an Orca terminal. Look at `ORCA_TERMINAL_HANDLE` first. When it is empty you are not
  the master, and the wrapper refuses to dispatch. Do not work around the refusal, and do not stop on it
  either: do what "Outside Orca" says, and nothing else of this skill.
- Work from the workspace root. State lives in `.orchestration/` there: `dispatch-ledger.jsonl`,
  `contracts/`, `handoffs/` and `lineage.md`.
- Load Orca's own guide once (`orca skills get orchestration`). Its rules still apply: start workers and
  end your turn, never poll, never steer through a worker's terminal, never create checkouts yourself.
- Never call `orca orchestration worker-start` directly: it bypasses the gate and the ledger.

## Outside Orca

From any other terminal, carry the conversation into a fresh master in Orca, on the same checkout. Run this
from the workspace root, without asking the owner first:

```text
ORCHESTRATION start --said "<what the owner has asked for so far, in the owner's words>"
```

Leave `--said` out when the owner has only asked you to act as master. The command starts Orca when it is
closed, makes the folder a workspace of Orca's, without a harness, and launches the master in a terminal
there, on the owner's default model. It writes nothing into the folder.

- Exit 0, `"stage": "start"`: the master runs in the terminal the line names, in the tab that is in front
  in Orca. It starts on a task the owner gave you, and waits for the first task otherwise. Tell the owner
  where it runs and that tasks go to it there, then stop: you are not the master.
- `Orca cannot be reached`: follow the numbered steps of the message. Nothing was started, so running the
  same command again is safe.
- Any other refusal names what to look at. Do that before a second run, which could start a second master.
- A conversation that already holds work a sentence cannot carry (decisions, findings, open tracks) goes
  over as a handoff instead: `succeed` works from any terminal too. See "Hand over to a successor".

## Dispatch a worker

1. Write a contract from `contract-template.md` in this skill's folder and keep it in
   `.orchestration/contracts/`. `tier`, `runtime` and `repo` are required, and `tier` must be the tier the
   model's family has in `config/model-tier-policy.json`. The contract is also the worker's task spec: it
   says what to do, and the repository the worker starts in says how. Keep repository rules out of it.
2. Run it, and add where the worker runs:

   ```text
   ORCHESTRATION dispatch --contract <file> --agent claude|codex --model <family> --effort <level>
   ```

   | Where | Add |
   |---|---|
   | Your own workspace | nothing |
   | A project directory the task is for | `--project <directory>` |
   | A worktree of its own in that project, which two workers at once each need | `--project <directory> --worktree new-top-level` |
   | A workspace Orca already shows, such as an earlier worker's | `--worktree path:<folder>` |
   | A new worktree in your own repository | `--worktree new-top-level` or `new-child` |

   A new worktree starts from the branch that is checked out there and holds only what is committed on it:
   commit what the worker must see before you dispatch.
3. Read the JSON lines it prints:
   - Exit 0, `"stage": "register"`: the worker runs and is recorded. `dispatch` and `run` are the ids the
     commands below take, `workspace` is the folder the worker runs in, and `task_attempt` says which
     attempt at its task this is. Do what `next` says: keep a watch running, then end your turn.
   - Exit 2 with only an `orchestration: ...` line, or at `"stage": "prepare"` or `"check"`: refused before
     anything was started, and no worker exists. Fix what the line says and dispatch again.
   - `CONTRACT_ALREADY_RUNNING` or `TASK_ALREADY_RUNNING`: an earlier dispatch is not settled, and no second
     worker was started. To change what that worker does, `send` to the dispatch the line names; see
     "After an Orca restart" when only its report is missing. An `outcome` other than `in_progress` is no
     proof that it is over: look at it with the command the `orchestration: ...` line names. Only the very
     same contract file is held, so one you have revised for a new attempt passes.
   - `TASK_ATTEMPTS_EXHAUSTED`: the task has had three attempts. Stop, and tell the owner what was tried.
   - Exit 2 at `"stage": "register"`: the worker runs but was not accepted, for example
     `MODEL_TIER_ESCALATION` (it runs on a higher tier than declared). Stop it with
     `orca orchestration worker-stop --dispatch <id>` and tell the owner.
   - `worker-start failed`: do not rerun it. Load Orca's recovery guide and read `failedStage` first.

### Models and effort

Always pass `--effort`, and `--model` for a Claude worker. Name the model by its family, as
`config/model-tier-policy.json` lists it, never by version: the wrapper asks Claude Code which version the
family means now. A full model id pins a version on purpose and is gated as its family. For `--agent codex`
leave `--model` out: the wrapper takes it from `~/.codex/config.toml`.

| Work | Model | Effort |
|---|---|---|
| Search, log summaries, lookups | `haiku` | `low` |
| Read-only review, research, planning | `sonnet` | `medium` |
| Edits, debugging, commands with side effects | `sonnet` | `high` |
| Long autonomous task (about 30 minutes or more) | `sonnet` | `xhigh` |
| Re-run of a task whose sonnet/high result fell short | `opus` | `medium` only |

Effort `max` and a model outside the policy are refused. The top tier is capped at 2 dispatches per 24
hours; `--tier-override "<reason>"` passes one more, only when the owner agrees. The dispatch line's
`warnings` say when the model the worker really runs on could not be read (`MODEL_UNVERIFIED`,
`MODEL_PROBE_FAILED`) or is another one of the declared tier or a lower one (`MODEL_MISMATCH`): tell the
owner. `ORCHESTRATION report [--today]` summarises the ledger.

## A task for a project directory

With `--project <directory>` the wrapper makes the directory ready before the gate is asked, and prints a
line with `"stage": "prepare"`: Orca knows the directory as a project, it carries the repository harness
(the rules a worker follows there: a router, `.agent/`, `.planning/`, `docs/wiki/`, hooks), the harness's
placeholders are filled, and its setup has run. Do not ask the owner whether to do this, and do none of it
by hand. `ORCHESTRATION prepare <directory>` does the same without dispatching. It is safe on any project,
any number of times: nothing that exists is overwritten. A dispatch without `--project` installs nothing.
To see what an install would do, run
`python -X utf8 -s -B "${CLAUDE_PLUGIN_ROOT}/scripts/harness.py" <repository root> --dry-run`.

- `setup` is `ran` or `done` once the harness's setup has run. `waits` means that an entry of `attention`
  comes first: deal with it and run `prepare` again, which then runs the setup. Do not run the script by
  hand. `left to the first session there` means that the project has changed the harness's scripts or
  hooks: its own router tells the worker what to run. After `failed`, tell the owner what the entry says.
- `attention` lists what is left, and each entry says what to do. `dispatch --project` starts the worker
  whether or not it has entries: deal with every entry before you end your turn, and name what was
  changed in your report. What an entry leaves to the owner stays the owner's: how to merge a `*.starter`
  file, git hooks of the project's own, and installing a tool the machine lacks.
- No git author name or email: git refuses every commit there, yours and a worker's. Ask the owner which
  identity to use, and set it for that project only (`git -C <directory> config user.name ...`, and
  `user.email`), never with `--global`. Do this before the first dispatch.
- Commit a new harness when the owner agrees, after the setup has run. Stage its files by name, never
  with `git add -A`, and its scripts and hooks with the `git add --chmod=+x ...` command the entry gives.
  The harness's pre-commit hook runs for that commit too: it dates the pages under `docs/`, writes the git
  author's name into them and takes those changes into the commit. Git's `LF will be replaced by CRLF`
  warnings there are harmless.
- Edit a filled placeholder only where the owner's request gives a rule that the neutral value does not
  say. Then change `CLAUDE.md` and copy it over `AGENTS.md`, `.cursorrules` and `.windsurfrules`: the four
  router files stay identical.

Two workers at the same time in one project each need a worktree of their own, or they edit the same
files. Run `prepare` first and deal with its `attention` until `setup` no longer `waits`, see that the
harness is committed, then dispatch each with `--project <directory> --worktree new-top-level`. Orca gives
each a worktree and a new branch. The branch that holds the work is the one the worker's report names, and
`git -C <workspace> branch --show-current` gives it: review and merge that one, and name a branch that
was left unused in your report.

## Wait for workers

Orca wakes you by typing into your terminal, and after a restart it does not. Never wait on Orca alone:
before you end a turn with a dispatch open, have one watch per Run running in the background (Bash with
`run_in_background`):

```text
ORCHESTRATION watch --run <run_id>
```

It only reads, and it is silent while Orca does its job. When it ends you are woken, and its lines say
why. After you have dealt with a wake, start the watch again if a dispatch is still open. Never run
`orca orchestration check` in a loop or a script: it takes your mail. `No messages.` from a `check` after
a repeated wake is the whole answer, not an error.

- `... are unread and Orca has not woken you`: read the mail with the command the line names.
- `... is open and its worker's screen ... has not changed for ...`: the worker has most likely stopped
  without a report. Read its screen with the command the line names. If it is back at its prompt, see
  "After an Orca restart". Only if the screen shows it still at work, start the watch again with a longer
  `--stalled-after <seconds>`. `liveness unverifiable` is no stall by itself. When the line adds
  `parked on a prompt that only a human can answer`, the worker is healthy and waits for an approval or a
  login: tell the owner what it asks, and do not answer it through its terminal. When it adds an outcome
  `which this watch does not know`, look at the dispatch and tell the owner that the watch needs that name.
- `... has settled and its mail is read`: nothing is left to wait for. What the workers reported is still
  yours to review and merge. A line that names a command for a settled dispatch (`Orca names a next step`,
  `Orca keeps its terminal open`) is for after that: run the command only once you no longer need that
  worker, its branch merged or given up. Its worktree and branch stay. Name them in your report, because
  removing them is the owner's decision.
- The watch failed (`no usable answer`, `lists no dispatch`, `cannot be run`): nothing watches the Run,
  and waiting does not mend it. Fix what the line says and start the watch again.

## When a worker reports

- Mail from a worker can be a question instead of a report, and the worker waits for the answer. Give it
  with `orca orchestration reply --id <msg_id> --body "<answer>"`, then start the watch again. An answer
  that changes what the contract says belongs in your report.
  `orca orchestration inbox --terminal run:<run_id> --limit 50 --full` shows mail you have already read.
- Treat the report as a claim. Follow the `verify-reported-results` skill: re-run the checks the contract
  names and read the diff before you write "passed".
- For code changes, run the review and clear every blocking finding before you accept the work:

  ```text
  /orchestration-kit:orchestration-review --in <workspace of the dispatch line> --contract <contract> <base branch>
  ```

  Leave `--in` out for your own checkout, and the base branch out for uncommitted changes. With
  `--contract`, one reviewer lists every requirement as `met`, `unmet` or `unjudged`. An `unmet` one
  blocks. An `unjudged` one is what a diff cannot show, such as the output of a command: run those checks
  yourself. Without `--contract`, a clean change that does not do what was asked passes. For a fix, name
  the fix contract, and give the commit the earlier attempt ended on in place of the base branch to show
  the reviewers the fix alone.
- Send a fix back as a new dispatch, never as a `send`: a dispatch that has reported is settled, and Orca
  answers a `send` to it by starting a worker that no gate has checked and no ledger holds. Write a fix
  contract (what is wrong, what must hold, the same `writes`), name it after the first one with the
  attempt's number (`<name>-2.md`), and dispatch it into the worker's own workspace:
  `ORCHESTRATION dispatch --contract <fix contract> ... --follows <dispatch of the rejected result> --worktree path:<workspace of the dispatch line>`.
  A worker that failed is dispatched again the same way. `--follows` takes any earlier dispatch of the task
  and makes this one its next attempt; `ORCHESTRATION report` lists every task with more than one. Do not
  make the fixes yourself unless the owner gave that work to you.
- `orca orchestration send --to dispatch:<id> --subject "<topic>" --body "<message>"` is for a worker that
  is still working: it gets the message when its current turn ends.
- Stop after the third failed attempt at one task, whether the worker failed or its result was rejected,
  and tell the owner what was tried and why each attempt fell short: the contract or the task is what
  needs to change. The wrapper refuses a fourth (`TASK_ATTEMPTS_EXHAUSTED`). Do not get round it by leaving
  `--follows` out.
- A worker with a workspace of its own must commit its work on its branch: what it leaves uncommitted
  there is in no diff you can review from your checkout. The contract template asks for it.
- Merge an accepted branch in the project's own checkout with a merge commit, so that the worker's commits
  stay as it made them: `git -C <project> merge --no-ff <branch>`. Run the project's whole test suite after the merge, then
  release the worker as the watch names it. Never push.

## After an Orca restart

The watch says when Orca was restarted, and it ends only once Orca is running again. Workers and their
sessions survive a restart. Orca loses its view of them, and it may refuse a `worker_done` sent right after
the restart.

- A report whose subject starts with `Rejected worker_done` is the worker's real report, and its dispatch
  is still open. Verify the work as for any report.
- A worker that finished while Orca was down could not send anything: its dispatch is open, no report came,
  and its screen shows the report it could not send.
- In both cases the work is done and only the report is missing. Never dispatch the contract again. Ask
  the worker to send it again:

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

Do this when the owner asks, or once this session's context has been compacted: you then work from a
summary of what you knew, and the plugin's session-start hook says so. A handoff is never automatic: tell
the owner that one is due, then write it.

1. Fill in `handoff-template.md` and save it as `.orchestration/handoffs/<UTC timestamp>.md`. All eight
   sections are required: write `none` where there is nothing. `succeed` refuses a handoff the successor
   could not work from, and names the section.
2. Run `ORCHESTRATION succeed --handoff <file> --model <family> --effort <level> --run <run_id>`.
   `orca orchestration run-current --json` gives the Run id. A Run exists only once something was
   dispatched: leave `--run` out while it gives none.
3. Stop your watch, the background task: the Run's mail is the successor's from now on. Tell the owner the
   successor is up, then stop working. Closing your terminal is the owner's decision.

As the successor: read the handoff, take over the Run if the kickoff names one, then record the
succession with one command:

```text
ORCHESTRATION inherit --handoff <file> --verification PASS|PARTIAL|FAILED --parent-session <predecessor terminal handle> --successor-session <your terminal handle> --inherited-role "<Current Role from the handoff>" --succession-reason "<why>" --inherited-open-tracks "<open tracks>" --context-loss-summary "<what you could not recover, or none>"
```

- The kickoff names the predecessor's terminal handle. A predecessor that did not run in Orca has none:
  give its session name as `--parent-session`, or any name for it where the handoff holds none.
- Use `PASS` only if you can restate the objective, the open tracks and the role state from the handoff
  alone, `PARTIAL` if you had to ask for something again, and `FAILED` if the handoff was unusable.
- If `inherit` prints `not verified (...)`, the predecessor's terminal is still open or Orca's list could
  not prove otherwise: say so in your report, because two masters on one Run both take its mail. Closing
  that terminal is the owner's decision, not yours.
- Report what you inherited to the owner, before any other work. After `PASS`, do not end your turn on
  that report: nobody answers it, and an idle successor stalls the work. Carry on with the next step the
  handoff names, unless the handoff says that step waits for the owner, and start your own watch if the
  Run has a dispatch open. After `PARTIAL` or `FAILED`, stop after the report: the owner has to supply
  what the handoff lost.
