# Upstream provenance

- Source: https://github.com/baksohyeon/mogui-ADE-orchestrator
- Commit: `45bd5e1a96dd315c073461ddf1912cca62cee191` (main, 2026-11-12)
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
| `review/orchestration-review.workflow.js` | `.claude/workflows/orch-review.workflow.js` | Workflow name and error prefixes renamed. Agent types use the plugin namespace (`orchestration-kit:code-reviewer`, `orchestration-kit:silent-failure-hunter`). Both `agent()` calls set `model: 'sonnet'` and `effort: 'medium'`. Two comments reworded. |
| `commands/orchestration-review.md` | `.claude/commands/orch-review.md` | Command name, description and intro reworded. `scriptPath` points at `${CLAUDE_PLUGIN_ROOT}/review/orchestration-review.workflow.js`. |
| `agents/code-reviewer.md` | `.claude/agents/code-reviewer.md` | Description only: the "MUST BE USED for all code changes" wording was removed so the agent is not invoked on every edit once the plugin is installed. |

## Taken as a format only

- Handoff document: the eight section titles and the Role State block (`skills/master/handoff-template.md`).
- Contract front matter rules (`skills/master/contract-template.md`).
- Succession trigger and the 13 lineage fields (written through `lineage.append_entry`).

## Not taken

`master-ops/scripts/dispatch` and the bash hooks, the onboarding interview and charter, `succession.py`
(its spawn and retire paths need a POSIX shell and `ps`), `bootstrap.py`, `recovery.py`, the `bd` and
`ctx` integrations, the pre-trust scripts, `/review-pr` with its four extra agents, the eight skills
under `master-ops/skills/`, and the redaction scan.

## Windows notes

- Upstream tests `tests/test_dispatch_gate.py`, `tests/test_dispatch_gate_front_matter.py` and
  `tests/test_lineage.py` at this commit: 142 passed, 10 skipped on Windows 11 with Python 3.14.5. The 10
  skipped tests run extensionless scripts directly, which Windows cannot do.
- Those tests isolate state through `HOME`, which Windows ignores. Run them with `USERPROFILE` pointed at
  an empty folder, or leftover tickets make one test fail with `AMBIGUOUS_TICKET`.
- The gate's own ticket lock uses `fcntl` and is a no-op on Windows. `scripts/orchestration.py` serialises
  gate calls with its own lock and always passes `--contract-sha` to `register`.

## Updating to a newer commit

1. Clone the source into an empty scratch folder with `core.autocrlf=false`.
2. Run the three test files above with `PYTHONPATH=<clone>/src` and an empty `USERPROFILE`.
3. Copy the unmodified files again, re-apply the changes listed under "Modified copies", update the commit
   above, and run `python -X utf8 -s -B -m unittest discover -s tests -v`.
