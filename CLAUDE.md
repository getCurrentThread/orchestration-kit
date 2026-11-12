# orchestration-kit

A Claude Code plugin for Windows. `README.md` describes it.

- Run Python as `python -X utf8 -s -B`. Tests: `python -X utf8 -s -B -m unittest discover -s tests -v`.
- `harness/` is a template tree that `scripts/harness.py` copies into other repositories. `harness/CLAUDE.md`,
  `harness/AGENTS.md` and everything under `harness/.agent/` are that template's content, not instructions
  for work in this repository. Never run `harness/scripts/setup.sh` here and never fill the template's
  `{{...}}` placeholders.
- `vendor/mogui/` is byte-identical to its upstream: do not edit it. `harness/` is derived from its upstream
  and is edited here; record what differs in `vendor/mogui-agent-harness/UPSTREAM.md`.
- Every file is stored as written (`.gitattributes`: `* -text`). Keep LF line endings.
