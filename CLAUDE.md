# orchestration-kit

A Claude Code plugin for Windows. `README.md` describes it.

- Run Python as `python -X utf8 -s -B`. Tests: `python -X utf8 -s -B -m unittest discover -s tests -v`.
- `harness/` is a template tree that `scripts/harness.py` copies into other repositories. `harness/CLAUDE.md`,
  `harness/AGENTS.md` and everything under `harness/.agent/` are that template's content, not instructions
  for work in this repository. Never run `harness/scripts/setup.sh` here and never fill the template's
  `{{...}}` placeholders.
- `vendor/mogui/` is byte-identical to its upstream: do not edit it. `harness/` is derived from its upstream
  and is edited here; record what differs in `vendor/mogui-agent-harness/UPSTREAM.md`.
- `hooks/hooks.json` runs in every session of every project that has the plugin, not only in a master's. A
  hook there prints nothing and leaves no file behind unless the session is the one it is meant for.
- What `scripts/orchestration.py` reads from an Orca answer is checked for its shape, and an answer it
  cannot read is an error, never "no workers". `tests/fake_orca.py` replays the shapes of the Orca version
  it names; measure a new shape against the live CLI before changing it there.
- `prepare` and `dispatch --project` work in directories that are not ours. They write nothing before Orca
  has answered, overwrite no file (they only append the lines a `.gitignore` lacks and replace the harness's
  own `{{...}}` placeholders), run `scripts/setup.sh` only where every script and git hook of the harness
  is the one shipped here, leave a repository's own git hooks running, and never commit. Keep each of these
  when changing them.
  To measure against the live Orca, add a scratch folder and remove it again with
  `orca project setup-delete --setup <id> --force` (`orca project setups --json` gives the id).
- Every file is stored as written (`.gitattributes`: `* -text`). Keep LF line endings.
- An install from GitHub gets a new copy only when `version` in `.claude-plugin/plugin.json` changes. Raise
  it with every change to `master` that those installs should receive.
