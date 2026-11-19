# Upstream provenance

- Source: https://github.com/baksohyeon/mogui-ADE-orchestrator
- Commit: `ed3b803a7d00063c886b0d0edfd3426c8eb6db02` (main, 2026-11-15, v0.5.345)
- License: MIT, Copyright (c) 2026 baksohyeon (`LICENSE`). The review agents and workflow derive from ECC,
  MIT, Copyright (c) 2026 Affaan Mustafa, https://github.com/affaan-m/ECC (`LICENSE-ECC`).

## Unmodified copies (byte-identical to the commit above)

- `vendor/mogui/scripts/dispatch-gate` and `vendor/mogui/src/master_runtime/` (`__init__.py`,
  `core/__init__.py`, `core/dispatch_gate.py`, `core/dispatch_timeline.py`, `core/lineage.py`,
  `core/token_budget.py`, `core/watchdog.py`, `core/worker_reap.py`), at the same paths under upstream's root.
- `vendor/mogui/LICENSE` from `LICENSE`, and `vendor/mogui/LICENSE-ECC` from `vendor/ecc/LICENSE-ECC`.
- `agents/silent-failure-hunter.md` from `.claude/agents/silent-failure-hunter.md`.

`dispatch_timeline.py`, `worker_reap.py` and `watchdog.py` are present only because the gate imports them.
Their process inspection code (`ps`, `lsof`, `/proc`, in `WorkerReaper`) is never reached from the gate: the
gate reaches `worker_reap.py` only through `resolve_task_id_from_dispatch_id`, which asks Orca.
`token_budget.py` is read through the gate: `dispatch` asks `budget` and passes the result to `register`, and
`report` calls `settle`, which reads the session file with it. Nothing in `scripts/orchestration.py` imports it.

To check a file: `git hash-object --no-filters <path here>` equals `git rev-parse <commit>:<upstream path>`
in a clone of the source.

## Modified copies

| Path here | Upstream path | Changes |
|---|---|---|
| `review/orchestration-review.workflow.js` | `.claude/workflows/orch-review.workflow.js` | Workflow name and error prefixes renamed. Agent types use the plugin namespace (`orchestration-kit:code-reviewer`, `orchestration-kit:silent-failure-hunter`). Both `agent()` calls set `model: 'sonnet'` and `effort: 'medium'`. Two comments reworded. The language reviewer is gone: upstream keeps a map from 18 language names to a reviewer agent, and here every entry was the one `code-reviewer`, so the extra dimension differed from `quality` in its label only; an input that still names a `language` is read as one that does not. The diff travels as a file whose length each reviewer must reach. With `contractFile`, a fourth reviewer reads the task contract beside the diff and lists every requirement as met, unmet or unjudged; an unmet one is a HIGH finding, and a reviewer that names no requirement counts as failed. |
| `commands/orchestration-review.md` | `.claude/commands/orch-review.md` | Command name, description and intro reworded. `scriptPath` points at `${CLAUDE_PLUGIN_ROOT}/review/orchestration-review.workflow.js`. Branch Mode, `--in <directory>` and `--contract <file>` are added. With `--contract` it first runs `scripts/orchestration.py scope` and reports every file outside the contract's `writes` as blocking. The derivation and passing of `language` is gone, with the language reviewer of the workflow. |
| `agents/code-reviewer.md` | `.claude/agents/code-reviewer.md` | Description only: the "MUST BE USED for all code changes" wording was removed so the agent is not invoked on every edit once the plugin is installed. |

## Taken as a format only

- Handoff document: the eight section titles and the Role State block, which holds `Current Role` only (`skills/master/handoff-template.md`). Upstream's block also has `Role Lock`, `Frozen` and `Unlock`; they were dropped because nothing here reads them, where upstream gives them force with a hook. A handoff that still carries the three lines is accepted.
- Contract front matter rules (`skills/master/contract-template.md`).
- Succession trigger and the 13 lineage fields (written through `lineage.append_entry`).

## Taken as an idea, written here

Upstream does each of these in bash or with POSIX process inspection. None of its code is copied: the
version here is Python in `scripts/orchestration.py` and asks Orca where upstream asks the operating system.
What was measured in Orca for them is recorded where it is used: in that script's comments, in the shapes
of `tests/fake_orca.py` and in `skills/master/SKILL.md`.

| Here | Upstream |
|---|---|
| `watch`: never reads an answer it does not know as "settled", follows every page, never runs `check` | `master-ops/scripts/worker-wait` (`WorkerListError`) and the runbook `orca-json.md` |
| `watch`: passes on the next step Orca names, a terminal Orca keeps open, and a worker parked on a prompt | `worker_reap.py`, `worker-pane-sweep` and `approval-prompt-markers.txt`. Upstream closes panes and matches prompt text itself; here both are read from Orca |
| `probe-model codex` | `scripts/model-probe-rollout`. Here the rollout is matched by the folder its first record names |
| `dispatch`: `CONTRACT_ALREADY_RUNNING` | `master-ops/scripts/dispatch-collision-check`. Upstream warns about overlapping scopes; here the very same contract is refused until Orca shows its dispatch settled |
| `dispatch --follows` and the cap of three attempts at one task | The gate's `--card` column and `docs/en/resident-orchestrator-loop.md` of mogui-agent-harness. The card is the Orca Task of the task's first dispatch |
| `succeed`: `Current Role` is one of the roles, and a section is neither empty nor still the template's. `Role Lock`, `Frozen` and `Unlock` are not asked for: nothing here reads them, where upstream gives them force with a hook | `bootstrap.py` (`VALID_ROLES`). Checked when the handoff is written, not when the successor boots |
| `inherit`: retiring the predecessor after a recorded `PASS` | `succession.py` (`detect_duplicate_instances`, `retire_predecessor`). Upstream retires it with a handshake and `ps`. Here `succeed` writes its terminal's handle to `.orchestration/master`, `inherit` asks Orca to close that terminal only after a `PASS`, and an `orca terminal list` without that handle, asked for up to ten seconds after the close, is the proof. After `PARTIAL` or `FAILED` it stays open |
| `hooks/hooks.json` with `compacted` | `master-ops/scripts/compaction-probe.sh`. No recall quiz and no log |
| `report` | `scripts/dispatch-gate report`, vendored: a pass-through, followed by every task with more than one attempt |
| `dispatch` and `report`: the token budget and its measured usage | `resolve_token_budget`, the budget paragraph of the worker prompt, and the settlement at `worker_done` or `reap` in `master-ops/scripts/dispatch`. `dispatch` passes the `front_matter` of the gate's `check` to the gate's `budget` and the result to `register` (`token_budget` and `tokens_basis` are on the dispatch line). The paragraph is a sentence in the contract template, since the contract file is the worker's prompt. Upstream settles from the session record the worker names in `worker_done`; here `report` settles each dispatch Orca shows as settled, from the one session file born in the worker's folder after the dispatch whose first prompt names it (none or several: `unmeasured`), and lists an open one as `open`. A count is the worker's main session only: its subagents' turns are in files of their own |
| `resume --dispatch <id>`: brings the Claude session of a worker whose agent ended back in the worker's own terminal and tells it to go on; the same dispatch settles | The runbook "Resume before re-dispatch" of `master-ops/docs/runbooks/dispatch.md`. Upstream finds the session with `orca search` (by the recorded session id, else by the dispatch id and the worktree), opens a new terminal with `orca terminal create`, submits the hit's `resumeCommand` there, waits for `turn_started`, writes `resume_attempt` and `redispatched_after_resume_failure` events into its ledger, and re-dispatches the contract after a failed resume. Here the command is Claude's own resume line from the terminal's text, typed into the worker's own terminal only when the text ends with that line and a shell prompt and the line's session id is the name of the dispatch's own session file (found as `report` finds it): `orca search` also returns the reflector session that the owner's autoharness starts when a worker's session ends, and Orca takes a `worker_done` only from the dispatch's own handle (a new terminal's handle was refused as `worker_identity_changed`; Orca 1.4.259, 2026-11-15). The session is waited for with `orca terminal wait --for tui-idle`, told to go on by a `send --to dispatch:<id>` and Orca's notice typed into the terminal, and nothing is written to the ledger and no attempt at the task is used. Left out on purpose: a worker whose terminal was closed (Orca fails its dispatch, and a resumed session there needs a new dispatch started onto its terminal with `worker-start --task <task> --terminal <handle>`, whose model Orca does not report: null, so the gate could not check its tier), Codex workers, and an agent that ended without printing its resume line (nothing then proves that it ended) |
| `dispatch`: a Claude worker is started with `permissions.blockWritesOutsideWorkingDirectories` in its settings file (`config/worker-settings-5m.json`, `-1h.json`; the same file holds `promptCacheTtl`, because two `--settings` flags do not add up: only the last counts). Its `Write`, `Edit` and `NotebookEdit` refuse a path outside the folder it runs in, and any `.claude/settings.json`. Measured on Claude Code 2.1.326 on 2026-11-18, headless and in an Orca terminal: a write outside was refused, one inside passed (`data.json`, `.claude-plugin/plugin.json`, `hooks/hooks.json`), `.claude/settings.json` and `nested/.claude/settings.json` inside were refused, the cache was written as `ephemeral_5m_input_tokens` only, and the interactive session's resume line repeated the flags. Over the 97 worker sessions on this machine, 127 of 936 file-tool writes went outside the worker's folder: 115 into the session's own scratchpad (left writable), 14 elsewhere under the temporary folder, 1 into a mistyped path, none into another checkout. Not covered: shell commands, MCP tools and what a hook runs; Codex workers; the sandbox, which does not run on native Windows. It needs Claude Code 2.1.314 or later and takes the `EnterWorktree` and `ExitWorktree` tools away (https://code.claude.com/docs/en/settings-reference#permissions-blockwritesoutsideworkingdirectories, read on 2026-11-18) | `write_worker_settings` in `master-ops/scripts/dispatch` (read at `1d2db60`, main, 2026-11-17): it writes `~/.mogui/worker-settings/<task-id>.json` with two `PreToolUse` matchers (`Edit\|Write\|NotebookEdit` and `Bash`) that run `hooks/product-path-guard.sh` with `MOGUI_WORKER_WRITE_ROOT=<worktree>`. Left out on 2026-11-13 because `worker-start` takes no launch arguments for the agent (1.4.257); that no longer holds, since 0.16.0 `dispatch` starts Claude with a command line of its own. Here no hook and no file per worker: a built-in setting, so the `Bash` matcher has no counterpart, and a contract's `writes` is still checked by the gate for its form and by the master against the diff |
| Three rules of `skills/master/`: a worker in its own workspace commits before it reports, three failed attempts stop the retries, and a research, review or design report gives evidence for every verdict (`contract-template.md`) | `master-ops/docs/runbooks/contract-conventions.md`, and `docs/en/resident-orchestrator-loop.md` of mogui-agent-harness. The evidence rule is clause 17 of the runbook; the wording here is this repository's own |
| `scope`, which the review command runs with `--contract`: it names every file of the diff that the contract's `writes` does not cover, and such a file blocks the result | The hard stops of `docs/en/resident-orchestrator-loop.md` of mogui-agent-harness: a file out of scope is a sign of a runaway worker, and an instruction alone enforces nothing. Upstream's gate checks the form of `writes` only, as the vendored one does; here the comparison is made by rule on the diff under review, after the work and not while it is done |
| `dispatch`: the `next` of the dispatch line says that a handoff is due once the master's session holds 80% of the tokens it is compacted at | The same document's hard stop at about 70% of the context ("stop starting new work, switch to handoff-writing mode"). Here the share is 80%, by the owner's decision on 2026-11-17, and the dispatch is not refused: its worker runs on, and the successor reviews the result. The tokens are read from the session file that `CLAUDE_CODE_SESSION_ID` names, and the window in Claude Code's order: `CLAUDE_CODE_AUTO_COMPACT_WINDOW`, the `--autocompact` on the command line of the session's `claude.exe`, the model's `autoCompactWindow` under `modelSettings`, where `/autocompact` saves it, then `autoCompactWindow`, the last two from the user's settings (https://code.claude.com/docs/en/model-config, `/settings-reference` and `/env-vars`, read on 2026-11-17). Where none of them names a number, as with `auto`, nothing is said. The command line was measured with Claude Code 2.1.326: `claude.exe` is the third process above the wrapper, and a stand-in of that name started with `--autocompact 500k` was read as 500,000. Measured on this machine that day: 11 of 12 compactions of this project's sessions came at 367,000 to 376,000 tokens with the window set to 400,000 |
| The rule of `skills/master/` that a review finding which can only be fixed outside the contract's `writes` goes into the master's report as work for a contract of its own | How mogui-agent-harness handles its own pull requests: a review finding outside the scope of its PR #10 was filed as issue #11 and closed by PR #12 (read on 2026-11-19). This repository has no issues and no pull requests, so the master's report takes the issue's place |

## Not taken

`master-ops/scripts/dispatch` and the bash hooks, the onboarding interview and charter, `succession.py`
(its spawn and retire paths need a POSIX shell and `ps`), `bootstrap.py`, `recovery.py`, the `bd` and
`ctx` integrations, the pre-trust scripts, `/review-pr` with its four extra agents, the eight skills
under `master-ops/skills/`, and the redaction scan.

The pre-trust scripts stay out. Since plugin 0.16.0 `dispatch` starts a Claude worker with a command line of its
own, and Orca answers Claude's question whether to trust a folder only for a Claude it starts itself. So the
wrapper answers it: yes, in the worker's terminal, and only once the screen shows the cursor on that choice
(`trust_folder` in `scripts/orchestration.py`; measured on Orca 1.4.261 with Claude Code 2.1.326 on 2026-11-17).

Looked at again on 2026-11-13 and left out:

- A tracker of its own for the card id (`--card-reason`, the `bd` lookup): `dispatch --follows` uses the
  card column instead.
- The `git status` call before a Codex worker starts in a new worktree (upstream #254): the failure was not
  reproduced here, and Orca creates the worktree inside `worker-start`.
- The master's `bashOutputMaxChars` setting and the poll, inbox and output-size hooks: they change the
  owner's settings or run in every session that has the plugin. `watch` covers the idle master.
- The four extra review agents: `code-simplifier` edits files, and the others repeat `code-reviewer` or
  apply to typed domain models.

Looked at again on 2026-11-15 and left out (upstream pull requests in brackets):

- Putting the work layer back after a compaction (#274, `compact-rehydration-hook.sh`): 612 lines of bash on
  PostToolUse and UserPromptSubmit of every session. `hooks/hooks.json` here stays silent outside the master's
  session, and `compacted` with the handoff covers the master.
- `worker-reap` reclaiming by worktree and proving squash landings (#273, #295): still left out. The master releases a
  worker and, once its work is merged, removes the worker's workspace with Orca's own commands (`orca worktree rm`
  without `--force`, which refuses uncommitted files and keeps an unmerged branch), as the master skill says; whatever
  is not merged stays the owner's decision. The file is vendored only because the gate imports it.
- The shared git directory as a writable root for Codex, git escalation events, the push destination in Codex
  briefs (#294, #304) and pre-trusting Antigravity worktrees (#269): the one ledger found on this machine on
  2026-11-15 holds Claude workers only, and workers never push here.
- The minimum Orca version in the preflight (#300): the wrapper already refuses an Orca answer it cannot read.
  Orca 1.4.259 was installed; upstream's minimum is 1.4.258.
- `measure-model`, proving the model from the worker's session record (#278): left out by the owner's choice on
  2026-11-15, when token budgets and resuming a worker were chosen instead.
- What belongs to installing upstream's template: hook paths resolved from the workspace (#279), the place of
  `instance-runtime.json` (#280), the headless-browser rule (#282), the research layout (#301) and the Grok
  model ids. This project installs no `master-ops` template.

## Windows notes

- Upstream tests `tests/test_dispatch_gate.py`, `tests/test_dispatch_gate_front_matter.py` and
  `tests/test_lineage.py` at this commit: 188 passed, 15 skipped on Windows 11 with Python 3.14.5 and pytest
  9.2.0 (150 passed and 15 skipped, 32 passed, and 6 passed in the three files). Ten of the skipped tests run
  extensionless scripts directly, which Windows cannot do; the other five need ledger file locking (3), symlinks
  (1) or FIFO files (1). They need `pytest` (not in the system Python: use a virtual environment outside the
  repository) and `PYTHONPATH=<clone>/src;<clone>/tests`: the second folder holds `windows_exec_surface.py`,
  the helper that skips those ten.
- Those tests isolate state through `HOME`, which Windows ignores. Run them with `USERPROFILE` pointed at a new,
  empty folder for each run, or leftover tickets (under `.mogui` there) make a test fail with `AMBIGUOUS_TICKET`.
- The gate's ticket lock and ledger lock use `fcntl` and are no-ops on Windows. `scripts/orchestration.py`
  serialises gate calls with its own lock and always passes `--contract-sha` to `register`.

## Updating to a newer commit

1. Clone the source into an empty scratch folder with `core.autocrlf=false`.
2. Run the three test files above with `PYTHONPATH=<clone>/src;<clone>/tests` and a new, empty `USERPROFILE`.
3. Copy the unmodified files again (and any file the gate newly imports), re-apply the changes listed under
   "Modified copies", update the commit above, and run `python -X utf8 -s -B -m unittest discover -s tests -v`.
