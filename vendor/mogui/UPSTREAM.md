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
| `review/orchestration-review.workflow.js` | `.claude/workflows/orch-review.workflow.js` | Workflow name and error prefixes renamed. Agent types use the plugin namespace (`orchestration-kit:code-reviewer`, `orchestration-kit:silent-failure-hunter`). Both `agent()` calls set `model: 'sonnet'` and `effort: 'medium'`. Two comments reworded. The diff travels as a file whose length each reviewer must reach. With `contractFile`, a fourth reviewer reads the task contract beside the diff and lists every requirement as met, unmet or unjudged; an unmet one is a HIGH finding, and a reviewer that names no requirement counts as failed. |
| `commands/orchestration-review.md` | `.claude/commands/orch-review.md` | Command name, description and intro reworded. `scriptPath` points at `${CLAUDE_PLUGIN_ROOT}/review/orchestration-review.workflow.js`. Branch Mode, `--in <directory>` and `--contract <file>` are added. |
| `agents/code-reviewer.md` | `.claude/agents/code-reviewer.md` | Description only: the "MUST BE USED for all code changes" wording was removed so the agent is not invoked on every edit once the plugin is installed. |

## Taken as a format only

- Handoff document: the eight section titles and the Role State block (`skills/master/handoff-template.md`).
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
| `succeed`: the Role State check, and a section that is empty or still the template's | `bootstrap.py` (`VALID_ROLES`). Checked when the handoff is written, not when the successor boots |
| `inherit`: whether the predecessor's terminal is still open | `succession.py` (`detect_duplicate_instances`, `retire_predecessor`). Nothing is closed here: that stays the owner's decision |
| `hooks/hooks.json` with `compacted` | `master-ops/scripts/compaction-probe.sh`. No recall quiz and no log |
| `report` | `scripts/dispatch-gate report`, vendored: a pass-through, followed by every task with more than one attempt |
| `dispatch` and `report`: the token budget and its measured usage | `resolve_token_budget`, the budget paragraph of the worker prompt, and the settlement at `worker_done` or `reap` in `master-ops/scripts/dispatch`. `dispatch` passes the `front_matter` of the gate's `check` to the gate's `budget` and the result to `register` (`token_budget` and `tokens_basis` are on the dispatch line). The paragraph is a sentence in the contract template, since the contract file is the worker's prompt. Upstream settles from the session record the worker names in `worker_done`; here `report` settles each dispatch Orca shows as settled, from the one session file born in the worker's folder after the dispatch whose first prompt names it (none or several: `unmeasured`), and lists an open one as `open`. A count is the worker's main session only: its subagents' turns are in files of their own |
| Three rules of `skills/master/`: a worker in its own workspace commits before it reports, three failed attempts stop the retries, and a research, review or design report gives evidence for every verdict (`contract-template.md`) | `master-ops/docs/runbooks/contract-conventions.md`, and `docs/en/resident-orchestrator-loop.md` of mogui-agent-harness. The evidence rule is clause 17 of the runbook; the wording here is this repository's own |

## Not taken

`master-ops/scripts/dispatch` and the bash hooks, the onboarding interview and charter, `succession.py`
(its spawn and retire paths need a POSIX shell and `ps`), `bootstrap.py`, `recovery.py`, the `bd` and
`ctx` integrations, the pre-trust scripts, `/review-pr` with its four extra agents, the eight skills
under `master-ops/skills/`, and the redaction scan.

Looked at again on 2026-11-13 and left out:

- A tracker of its own for the card id (`--card-reason`, the `bd` lookup): `dispatch --follows` uses the
  card column instead.
- One write root per worker (`product-path-guard.sh` through a `--settings` file):
  `orca orchestration worker-start` takes no launch arguments for the agent (1.4.257). A contract's `writes`
  is checked by the gate for its form, and by the master against the diff.
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
- `worker-reap` reclaiming by worktree and proving squash landings (#273, #295): it closes terminals and removes
  worktrees, which stays the owner's decision here. The file is vendored only because the gate imports it.
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
