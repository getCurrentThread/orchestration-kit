# orchestration-kit

A Claude Code plugin that adds a thin orchestration layer on top of the Orca CLI, reassembled for
Windows from [baksohyeon/mogui-ADE-orchestrator](https://github.com/baksohyeon/mogui-ADE-orchestrator)
and its sibling [baksohyeon/mogui-agent-harness](https://github.com/baksohyeon/mogui-agent-harness).
Orca keeps doing what it already does (workers, Runs, mail). This plugin adds what it does not:

- **Dispatch gate and ledger.** A worker starts only after its contract passes the gate (front matter,
  budget, model tier, fan-out cap). Every decision is appended to a JSONL ledger, and the model the worker
  really runs on, read from its Claude Code transcript or its Codex rollout, is compared with the declared
  one. A contract is not dispatched a second time until Orca shows its earlier dispatch as settled. A fix
  for a reported result goes back as a new dispatch into the same workspace, because Orca answers a message
  to a settled worker by starting a new one that no gate has seen. The watch names such a worker.
  `dispatch --follows <dispatch>` records a retry or a fix as one more attempt at the task of the dispatch
  it follows: the ledger holds every attempt under that task, `report` lists them, and a fourth attempt at
  one task is refused.
- **Project directories made ready.** A task for a directory needs no setup by hand. Before the worker
  starts, `dispatch --project <directory>` adds the directory to Orca as a project if Orca does not know
  it, installs the repository harness if it has none, fills the harness's placeholders and runs its setup.
  Every step looks before it acts, so it runs on an existing project and runs again without changing
  anything: no file is overwritten, and a repository that runs git hooks of its own keeps them until the
  owner has decided. With `--worktree new-top-level` the worker gets a worktree of its own in that project,
  so that several can work in it at the same time. A new worktree always starts from the branch that is
  checked out locally, in a project and in the master's own repository alike.
- **A wait that survives an Orca restart.** Orca wakes a waiting master by typing into its terminal, and
  after a restart it does not. A read-only watch that the master keeps in the background wakes it for
  unread reports and for workers that stopped without one. It passes on what Orca itself reports: a worker
  parked on a prompt for a human, and the next step Orca names for a finished one. An answer it cannot read
  ends it with an error and is never taken for "all settled".
- **Master succession.** A written handoff that is refused while a section is empty or still the template's,
  a successor launched in an Orca terminal, and an append-only lineage ledger that records whether the
  predecessor's terminal was still open. A session outside Orca becomes a master the same way: `succeed`
  adds its folder to Orca when Orca does not know it, and launches the master there. After a compaction, a session-start hook tells the master, and no other session,
  that a handoff is due.
- **Review workflow.** `/orchestration-kit:orchestration-review` fans a diff (uncommitted changes, a branch
  against its base, or a GitHub PR) out to reviewers, removes duplicates and has every CRITICAL or HIGH
  finding checked by an independent verifier. `--in <directory>` reviews another checkout than the
  session's own, such as a worker's worktree. `--contract <file>` adds a reviewer that reads the task
  contract beside the diff and says of every requirement whether the change meets it: an unmet one blocks,
  and one that a diff cannot show is listed for the master to check by running it.
- **Repository harness.** The rules a worker follows inside one repository, derived from the sibling
  project [baksohyeon/mogui-agent-harness](https://github.com/baksohyeon/mogui-agent-harness): a router
  (`CLAUDE.md`, `AGENTS.md`) that points at `.agent/` (rules, context, memory), `.planning/` (work in
  progress) and `docs/wiki/` (decisions, guides, postmortems), with session and git hooks and a code graph
  over MCP. `scripts/harness.py` installs it into a repository without overwriting anything.

The first five are the workspace layer and the last is the repository layer. The master hands a worker a
contract, and the repository the worker starts in supplies its own rules. The layers meet in one place:
`prepare`, which puts the second into a directory on behalf of the first.

## Requirements

Windows, Python 3.12 or later on `PATH` as `python`, Claude Code, and the Orca desktop app with its `orca`
CLI. The master session must run inside an Orca terminal.

The repository harness is bash: its hooks and scripts need Git for Windows. Installing it does not. Two of
its parts are optional and do nothing until their tool is on `PATH`: the code graph needs
[code-review-graph](https://github.com/tirth8205/code-review-graph) (`uv tool install code-review-graph`, or
`pip install code-review-graph`), and the secret scan of every commit needs
[gitleaks](https://github.com/gitleaks/gitleaks).

## Install

From GitHub, in a shell:

```text
claude plugin marketplace add getCurrentThread/orchestration-kit
claude plugin install orchestration-kit@orchestration-kit
```

Claude Code clones the repository with `git` and keeps its own copy of the plugin. To move to a later
release, run `claude plugin update orchestration-kit@orchestration-kit` and start a new session. A new copy
arrives only when `version` in `.claude-plugin/plugin.json` has changed.

From a clone, to work on the plugin itself:

```text
git clone https://github.com/getCurrentThread/orchestration-kit.git
claude plugin marketplace add ./orchestration-kit
claude plugin install orchestration-kit@orchestration-kit
```

A marketplace added from a folder is read in place: an edit in the clone takes effect at the next session
start or `/reload-plugins`, whatever the version says. To try the plugin in one session without installing:
`claude --plugin-dir <path to the clone>`.

## Use

In a Claude Code session started in an Orca terminal, ask it to act as master; the
`orchestration-kit:master` skill holds the procedure. The pieces it uses:

| Piece | Path |
|---|---|
| Wrapper (`prepare`, `dispatch`, `watch`, `succeed`, `inherit`, `report`) | `scripts/orchestration.py` |
| Model families, tiers and caps (no versioned ids) | `config/model-tier-policy.json` |
| Contract and handoff templates | `skills/master/` |
| Session-start hook, after a compaction only | `hooks/hooks.json` |
| Review command, workflow and agents | `commands/`, `review/`, `agents/` |
| State, per workspace | `.orchestration/` in the workspace root |
| Repository harness installer | `scripts/harness.py` |
| Repository harness files | `harness/` |

### Give the master a task for a project directory

Name the directory when you give the master its task. It dispatches with `--project <directory>`, and the
wrapper makes the directory ready first. The same steps run alone as
`python -X utf8 -s -B <path to the clone>/scripts/orchestration.py prepare <directory>`:

| Step | Done when | Left alone when |
|---|---|---|
| Add the directory to Orca (`orca repo add`), as a Git project or, outside a repository, as a folder | Orca does not know it | Orca already has it |
| Install the repository harness | The directory has no `.agent/Instructions.md` | It has one. A file that differs from the harness's is never touched: the harness's version is written beside it as `<name>.starter`. Only `.gitignore` is merged: the lines it lacks are appended to it |
| Fill the harness's `{{...}}` placeholders: the project's name, repository and branch as the folder and git show them, and a neutral value for the rest | A page of the harness, or its `*.starter`, still holds one | None is left. Double braces of the project's own are never touched |
| Run the harness's `bash scripts/setup.sh` (git hooks, wiring check, `.setup-done`, and `git init` in a new directory) | The directory holds the harness's scripts and git hooks exactly as shipped, has no `.setup-done` and runs no git hooks of its own | A `*.starter` file waits for a merge, the project has changed one of those scripts or hooks, or git already runs hooks there, from `.git/hooks` (git-lfs keeps four in it) or from a `core.hooksPath` that the project or the account has set: pointing git at `.githooks` would silence them |

The command prints one JSON line that says what it did, and lists under `attention` what only a person or
the master can do: the `*.starter` files to merge, the hooks to carry over, the `git add --chmod=+x` command
that stages a new harness's scripts and hooks for its commit, a git identity to set where git has none and
would refuse every commit, and the tools the machine lacks. It also names what it did that the owner should
see: the lines it appended to `.gitignore` and the pages whose placeholders it filled, for the owner to
correct the neutral values. A second run changes nothing. It refuses a directory inside a
repository and a linked worktree (a project is the root of a main checkout, or a directory outside every
repository), a drive's root and the home folder, and it changes nothing while Orca gives no answer or git
cannot say what the directory is. An install that fails half way takes its files back. It never commits: a
new harness stays uncommitted until you or the master commit it.

A worker started with `--project <directory>` alone works in the project's own checkout. For several
workers at once, the master adds `--worktree new-top-level`: Orca then makes each worker a worktree and a
branch of its own. The wrapper names the branch the project has checked out as the base. Left to itself,
Orca starts a new worktree of a clone from the remote's branch, where a harness or a fix that was only
committed locally is missing. The same holds without `--project`: `--worktree new-top-level` or `new-child`
makes the worktree in the master's own repository, from the branch the master has checked out.

### Install the repository harness by hand

From a clone:

```text
python -X utf8 -s -B <path to the clone>/scripts/harness.py <repository root> --dry-run
python -X utf8 -s -B <path to the clone>/scripts/harness.py <repository root>
```

A file the repository does not have is added. A file it has is never touched: when it differs from the
harness's, the harness's version is written beside it as `<name>.starter` for a merge by hand. `.gitignore`
is the exception: the lines it lacks are appended to it. This command leaves the placeholders as they are;
`prepare` fills them. The command
prints what it did and the steps that remain, and names what the machine lacks (`bash`,
`code-review-graph`, `gitleaks`). It stops before writing anything when a file sits where the harness needs
a folder.

`harness/` holds exactly the files that are installed. The first session in the repository finishes the
job: its router tells the agent to report the `*.starter` files, fill the `{{...}}` placeholders and run
`bash scripts/setup.sh`. The installer never sets `core.hooksPath`; that script does, and it leaves a
repository's husky or lefthook chain as it is. The setting is shared by every checkout of the repository,
linked worktrees included, and git stops running the hooks in `.git/hooks` once it is set: the script names
those hooks so that they can be merged into `.githooks/` by hand. The pre-commit hook keeps `updated_at`
current in the pages under `docs/` and scans every commit for secrets where `gitleaks` is installed. A
worker that Orca starts in a new worktree of the repository is not sent through the setup again: the
setup of the main checkout covers every linked worktree.

The harness gives every session in the repository a map of the code through `code-review-graph`:

- `.mcp.json` starts its MCP server. `.claude/settings.json` approves that server, which Claude Code
  honors once the folder is trusted, so a worker is not stopped by an approval prompt.
- A session-start hook reports the state of the graph, and a hook after every Edit, Write or Bash call
  keeps it current. The first of those calls builds it.

None of this does anything until the tool is installed.

## Tests

```text
python -X utf8 -s -B -m unittest discover -s tests -v
```

The tests run the real vendored gate against a fake Orca CLI and a fake Claude CLI (`tests/fake-orca.cmd`,
`tests/fake-claude.cmd`), `prepare` against new and existing git repositories, the installer against the
real harness tree, the harness's write guard, hook setup
and pre-commit hook through Git Bash, and the review workflow's script under Node with scripted agents
(skipped where `node` is not on `PATH`). `.github/workflows/tests.yml` runs them on `windows-latest` for
every push and pull request, and fails when Git Bash or Node is missing instead of skipping.

## Provenance

`vendor/mogui/UPSTREAM.md` and `vendor/mogui-agent-harness/UPSTREAM.md` list the upstream commit, what
was copied, what was changed, what was left out, and what was measured on Windows. `vendor/mogui/` is
byte-identical to its upstream; `harness/` is derived from its upstream and maintained here.

## License

MIT: see `LICENSE`. What was taken from other projects keeps its own MIT notice:

| Notice | Covers |
|---|---|
| `vendor/mogui/LICENSE` | mogui-ADE-orchestrator: every file `vendor/mogui/UPSTREAM.md` lists as copied or modified |
| `vendor/mogui/LICENSE-ECC` | [ECC](https://github.com/affaan-m/ECC), from which the review agents and workflow derive |
| `harness/.agent/LICENSE-mogui-agent-harness` | mogui-agent-harness: `harness/`. It travels with every install of the harness |
