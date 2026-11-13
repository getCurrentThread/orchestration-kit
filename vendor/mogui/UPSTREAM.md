# Upstream provenance

- Source: https://github.com/baksohyeon/mogui-ADE-orchestrator
- Commit: `dc56671287a5cf460a37a319e72d9e5fe9bc72df` (main, 2026-11-13)
- License: MIT, Copyright (c) 2026 baksohyeon (`LICENSE`). The review agents and workflow derive from ECC,
  MIT, Copyright (c) 2026 Affaan Mustafa, https://github.com/affaan-m/ECC (`LICENSE-ECC`).

## Unmodified copies (byte-identical to the commit above)

| Path here | Upstream path |
|---|---|
| `vendor/mogui/scripts/dispatch-gate` | `scripts/dispatch-gate` |
| `vendor/mogui/src/master_runtime/__init__.py` | `src/master_runtime/__init__.py` |
| `vendor/mogui/src/master_runtime/core/__init__.py` | `src/master_runtime/core/__init__.py` |
| `vendor/mogui/src/master_runtime/core/dispatch_gate.py` | `src/master_runtime/core/dispatch_gate.py` |
| `vendor/mogui/src/master_runtime/core/dispatch_timeline.py` | `src/master_runtime/core/dispatch_timeline.py` |
| `vendor/mogui/src/master_runtime/core/worker_reap.py` | `src/master_runtime/core/worker_reap.py` |
| `vendor/mogui/src/master_runtime/core/watchdog.py` | `src/master_runtime/core/watchdog.py` |
| `vendor/mogui/src/master_runtime/core/lineage.py` | `src/master_runtime/core/lineage.py` |
| `vendor/mogui/LICENSE` | `LICENSE` |
| `vendor/mogui/LICENSE-ECC` | `vendor/ecc/LICENSE-ECC` |
| `agents/silent-failure-hunter.md` | `.claude/agents/silent-failure-hunter.md` |

`dispatch_timeline.py`, `worker_reap.py` and `watchdog.py` are present only because the gate imports them.
Their process inspection code (`ps`, `lsof`, `/proc`) is never reached from the gate.

To check a file: `git hash-object --no-filters <path here>` equals `git rev-parse <commit>:<upstream path>`
in a clone of the source.

## Modified copies

| Path here | Upstream path | Changes |
|---|---|---|
| `review/orchestration-review.workflow.js` | `.claude/workflows/orch-review.workflow.js` | Workflow name and error prefixes renamed. Agent types use the plugin namespace (`orchestration-kit:code-reviewer`, `orchestration-kit:silent-failure-hunter`). Both `agent()` calls set `model: 'sonnet'` and `effort: 'medium'`. Two comments reworded. The diff travels as a file whose length each reviewer must reach. With `contractFile`, a fourth reviewer reads the task contract beside the diff and lists every requirement as met, unmet or unjudged; an unmet one is a HIGH finding, and a reviewer that names no requirement counts as failed. Upstream's reviewers see the diff only. |
| `commands/orchestration-review.md` | `.claude/commands/orch-review.md` | Command name, description and intro reworded. `scriptPath` points at `${CLAUDE_PLUGIN_ROOT}/review/orchestration-review.workflow.js`. Branch Mode, `--in <directory>` and `--contract <file>` are added. |
| `agents/code-reviewer.md` | `.claude/agents/code-reviewer.md` | Description only: the "MUST BE USED for all code changes" wording was removed so the agent is not invoked on every edit once the plugin is installed. |

## Taken as a format only

- Handoff document: the eight section titles and the Role State block (`skills/master/handoff-template.md`).
- Contract front matter rules (`skills/master/contract-template.md`).
- Succession trigger and the 13 lineage fields (written through `lineage.append_entry`).

## Taken as an idea, written here

Upstream does each of these in bash or with POSIX process inspection. None of its code is copied: the
version here is Python in `scripts/orchestration.py` and asks Orca where upstream asks the operating system.

| Here | Upstream | What differs |
|---|---|---|
| `watch` never reads an answer it does not know as "settled": a missing or renamed `workers` or `messages` list, a row without `outcome`, a list without `page.hasMore`, text that is not JSON and an empty Run all end it with an error. It follows `page.nextCursor`, and reads every outcome other than `succeeded` and `failed` as open. | `master-ops/scripts/worker-wait` (`WorkerListError`, its paging loop) and the runbook `orca-json.md`: after Orca 1.4.246 renamed keys, every loose reader "failed silent". | Read-only as before: it never runs `check`. |
| `probe-model codex` reads the model of a Codex worker from its rollout file. | `scripts/model-probe-rollout` and the rollout snapshot in `master-ops/scripts/dispatch`. | Upstream picks the one rollout that appeared since the launch and gives up when several did. Here the rollout is matched by the folder its first record names (`session_meta.payload.cwd`), and the model is the one of its last `turn_context`. |
| `dispatch` refuses a contract that an open dispatch already runs (`CONTRACT_ALREADY_RUNNING`). | `master-ops/scripts/dispatch-collision-check`. | Upstream warns, fail-open, about overlapping `Scope:` lines and open pull requests. Here only the very same contract counts (its hash in the ledger), and it is refused until Orca shows its earlier dispatch as `succeeded` or `failed`. No answer from Orca does not clear it. Measured in Orca 1.4.258 on 2026-11-13: `orca orchestration send --to dispatch:<id>` to a settled dispatch answers `delivery=started_fresh followUpOf=<id>` with a new `dispatchId`, a worker that the gate never checked. The skill therefore sends a fix back as a new dispatch into the worker's workspace, and `watch` names every dispatch of the Run that the ledger does not hold. |
| `succeed` checks the Role State block: a known `Current Role`, `Role Lock` `ENABLED` or `DISABLED`, `Frozen` and `Unlock`. It also refuses a section that is empty or still the template's text, which upstream does not check. | `bootstrap.py` (`VALID_ROLES`, the Role State parser). | Checked when the handoff is written, not when the successor boots. The successor is launched with `--surface terminal`: without it the owner's Chat or CLI setting decides, and a master in a chat has no terminal handle. Where `orca worktree current` answers `selector_not_found` (measured in Orca 1.4.258 on 2026-11-13), `succeed` adds its folder to Orca first, without a harness. The kickoff names the predecessor's terminal handle, which the successor's `inherit` takes and no handoff section holds. |
| `inherit` records whether the predecessor's terminal is still among Orca's live ones. | `succession.py` (`detect_duplicate_instances`, `retire_predecessor`). | Upstream closes the pane and proves it with `ps`. Here nothing is closed: that stays the owner's decision. The terminal counts as gone only when Orca's list shows the successor's own terminal and not the predecessor's. |
| `hooks/hooks.json` with `compacted`: after a compaction the master, and no other session, is told that a handoff is due. | `master-ops/scripts/compaction-probe.sh`. | No recall quiz and no log. The master is known by the terminal handle that last dispatched or inherited (`.orchestration/master`). |
| `watch` passes on the next step Orca names for a settled worker, and says when Orca sees a quiet worker parked on a prompt for a human (`observation.agentWait`). | `worker_reap.py`, `worker-pane-sweep` and `approval-prompt-markers.txt`. | Upstream closes panes and removes worktrees itself, and tells a blocked worker from a finished one by matching English prompt text on its screen. Orca's own guide says that finished workers close by themselves unless `worker-list` names a next step, so both are read from Orca. Measured in Orca 1.4.257 on 2026-11-13, over the 197 dispatches of one machine: `projection.nextAction.kind` is `none`, `release` or `recover`, and `outcome` is `succeeded` or `failed`. `observation.agentWait` is taken from `orca orchestration worker-show --help`: no parked worker was there to measure. Measured in Orca 1.4.258 on the same day, on five workers that each got a worktree of their own: once settled they keep their terminal (`resource.releaseState` `retained`, `retainedReason` `user_requested`) and `nextAction.kind` stays `none`, so the watch names the release for them itself. That field is the row's `resource`: `projection.resource` holds only `{"state": "owned"}`. `orca orchestration worker-release --dispatch <id>` then answered `state` `released` with `processAction` `closed_agent_terminal`, the row showed `releaseState` `released`, `worker-read` still returned the output, and the worktree stayed. One healthy, working worker was listed with `stage.activity` `unknown` and `liveness` `unverifiable` (`reason` `missing_status`): the watch goes by the terminal's output for such a worker, as it does after a restart. A worker sent into a worktree that already existed (`--worktree path:`) settled with its terminal `reclaimable` and got no line from the watch: `orca orchestration worker-list --help` says that Orca releases such a terminal itself at `resource.autoReleaseAt`, and `worker-release` on it answered `released`. |
| `report` prints the gate's own ledger summary. | `scripts/dispatch-gate report`, vendored. | A pass-through, followed by every task that has more than one attempt. |
| `dispatch --follows <dispatch>` links a retry or a fix to the task of the dispatch it follows, and refuses a fourth attempt at one task. | The gate's `--card` column, which upstream fills with a `bd` tracker card, and the three-attempt rule of `docs/en/resident-orchestrator-loop.md`. | The card is the Orca Task of the task's first dispatch, passed on every `register`. The gate's own `attempt` counts the ALLOW lines of one contract file (2 on every register line), and a fix contract is a new file, so it never counted attempts at a task. A follow-up is refused while an earlier attempt at the task is not settled. |
| Two lines of `skills/master/`: a worker in its own workspace commits before it reports, and three failed attempts at one task stop the retries. | `master-ops/docs/runbooks/contract-conventions.md`, and `docs/en/resident-orchestrator-loop.md` of mogui-agent-harness. | Reworded for Orca workers. |

## Not taken

`master-ops/scripts/dispatch` and the bash hooks, the onboarding interview and charter, `succession.py`
(its spawn and retire paths need a POSIX shell and `ps`), `bootstrap.py`, `recovery.py`, the `bd` and
`ctx` integrations, the pre-trust scripts, `/review-pr` with its four extra agents, the eight skills
under `master-ops/skills/`, and the redaction scan.

Looked at again on 2026-11-13 and left out, each for a reason:

- A tracker of its own for the card id on every dispatch (`--card-reason` and the `bd` lookup). The card
  column itself is used: see `dispatch --follows` above.
- One write root per worker (a `--settings` file whose PreToolUse hook is `product-path-guard.sh`).
  `orca orchestration worker-start` takes no launch arguments for the agent (its `--help` in 1.4.257), and
  the path that does, a terminal started by hand, gives up Orca's worker supervision. A contract's `writes`
  is still checked by the gate for its form only, and by the master against the diff.
- The `git status` call before a Codex worker starts in a new worktree (upstream #254, for a first
  `git add` refused with `index.lock: Operation not permitted`). Its test is a static check that the wrapper
  holds the call and runs no sandboxed `git add`. The failure was not reproduced here, and Orca creates a
  worker's worktree inside `worker-start`, so this wrapper has no moment between the two to run it.
- The master's `bashOutputMaxChars` setting and the poll, inbox and output-size hooks. They change the
  owner's settings or run in every session that has the plugin; `watch` covers the idle master.
- `worker_reap.py`, although it is vendored: see the table above.
- The four extra review agents: `code-simplifier` edits files, and the other three repeat what
  `code-reviewer` already lists or apply to typed domain models.

## Windows notes

- Upstream tests `tests/test_dispatch_gate.py`, `tests/test_dispatch_gate_front_matter.py` and
  `tests/test_lineage.py` at this commit: 150 passed, 10 skipped on Windows 11 with Python 3.14.5. The 10
  skipped tests run extensionless scripts directly, which Windows cannot do. They need `pytest` and
  `PYTHONPATH=<clone>/src;<clone>/tests`: the second folder holds the helper that skips those ten.
- Since `45bd5e1` three vendored files changed. The gate writes the tier policy's path into the ledger
  with `~` for the home folder, `register` accepts a tracker card (not used here), and `worker_reap.py`
  can delete a merged branch (never reached from the gate).
- Those tests isolate state through `HOME`, which Windows ignores. Run them with `USERPROFILE` pointed at
  an empty folder, or leftover tickets make one test fail with `AMBIGUOUS_TICKET`.
- The gate's own ticket lock uses `fcntl` and is a no-op on Windows. `scripts/orchestration.py` serialises
  gate calls with its own lock and always passes `--contract-sha` to `register`.

## Updating to a newer commit

1. Clone the source into an empty scratch folder with `core.autocrlf=false`.
2. Run the three test files above with `PYTHONPATH=<clone>/src;<clone>/tests` and an empty `USERPROFILE`.
3. Copy the unmodified files again, re-apply the changes listed under "Modified copies", update the commit
   above, and run `python -X utf8 -s -B -m unittest discover -s tests -v`.
