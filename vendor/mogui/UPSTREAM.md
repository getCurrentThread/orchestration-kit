# Upstream provenance

- Source: https://github.com/baksohyeon/mogui-ADE-orchestrator
- Commit: `dc56671287a5cf460a37a319e72d9e5fe9bc72df` (main, 2026-11-13)
- License: MIT, Copyright (c) 2026 baksohyeon (`LICENSE`). The review agents and workflow derive from ECC,
  MIT, Copyright (c) 2026 Affaan Mustafa, https://github.com/affaan-m/ECC (`LICENSE-ECC`).

## Unmodified copies (byte-identical to the commit above)

- `vendor/mogui/scripts/dispatch-gate` and `vendor/mogui/src/master_runtime/` (`__init__.py`,
  `core/__init__.py`, `core/dispatch_gate.py`, `core/dispatch_timeline.py`, `core/lineage.py`,
  `core/watchdog.py`, `core/worker_reap.py`), at the same paths under upstream's root.
- `vendor/mogui/LICENSE` from `LICENSE`, and `vendor/mogui/LICENSE-ECC` from `vendor/ecc/LICENSE-ECC`.
- `agents/silent-failure-hunter.md` from `.claude/agents/silent-failure-hunter.md`.

`dispatch_timeline.py`, `worker_reap.py` and `watchdog.py` are present only because the gate imports them.
Their process inspection code (`ps`, `lsof`, `/proc`) is never reached from the gate.

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
| Two rules of `skills/master/`: a worker in its own workspace commits before it reports, and three failed attempts stop the retries | `master-ops/docs/runbooks/contract-conventions.md`, and `docs/en/resident-orchestrator-loop.md` of mogui-agent-harness |

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

## Windows notes

- Upstream tests `tests/test_dispatch_gate.py`, `tests/test_dispatch_gate_front_matter.py` and
  `tests/test_lineage.py` at this commit: 150 passed, 10 skipped on Windows 11 with Python 3.14.5. The 10
  skipped tests run extensionless scripts directly, which Windows cannot do. They need `pytest` and
  `PYTHONPATH=<clone>/src;<clone>/tests`: the second folder holds the helper that skips those ten.
- Those tests isolate state through `HOME`, which Windows ignores. Run them with `USERPROFILE` pointed at
  an empty folder, or leftover tickets make one test fail with `AMBIGUOUS_TICKET`.
- The gate's own ticket lock uses `fcntl` and is a no-op on Windows. `scripts/orchestration.py` serialises
  gate calls with its own lock and always passes `--contract-sha` to `register`.

## Updating to a newer commit

1. Clone the source into an empty scratch folder with `core.autocrlf=false`.
2. Run the three test files above with `PYTHONPATH=<clone>/src;<clone>/tests` and an empty `USERPROFILE`.
3. Copy the unmodified files again, re-apply the changes listed under "Modified copies", update the commit
   above, and run `python -X utf8 -s -B -m unittest discover -s tests -v`.
