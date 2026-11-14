"""orchestration-kit: gated worker dispatch and master succession on top of the Orca CLI.

Run from the workspace root as `python -X utf8 -s -B orchestration.py <command>`:
  prepare   make a project directory ready for a worker: known to Orca, and carrying the repository harness
  dispatch  check a contract with the vendored dispatch gate, start one Orca worker, register it
  watch     wait for a Run's workers; return when the master must act and Orca has not woken it
  start     from a terminal outside Orca: start a fresh master in Orca on this checkout, with what the owner said
  succeed   validate a handoff document and launch a successor master in Orca
  inherit   append the succession to the lineage ledger (run by the successor)
  report    summarise the dispatch ledger: models, denials, tiers, overrides

State is kept in ./.orchestration/ of the current directory.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import msvcrt
import os
import re
import subprocess
import sys
import time
import tomllib
from datetime import datetime, timezone
from pathlib import Path

import harness  # scripts/harness.py, beside this file

ROOT = Path(__file__).resolve().parent.parent
VENDOR = ROOT / "vendor" / "mogui"
GATE = VENDOR / "scripts" / "dispatch-gate"
POLICY = ROOT / "config" / "model-tier-policy.json"
PYTHON = [sys.executable, "-X", "utf8", "-s", "-B"]
# Answered by the Claude Code CLI from its model picker, without an API turn.
MODEL_LIST_REQUEST = '{"type":"control_request","request_id":"orchestration-kit","request":{"subtype":"list_models"}}\n'
HANDOFF_HEADINGS = (
    "Role State",
    "Current Objective",
    "Active/Open Tracks",
    "Accepted Artifacts",
    "Deferred Work",
    "Open Questions",
    "Recommended Next Role",
    "Observed Baseline",
)
# The outcomes Orca gives a dispatch that is over (1.4.257: all 197 rows of one machine's history hold one of
# them, stopped and abandoned workers included). Any other outcome is read as open, so that a new one is never
# taken for "settled".
SETTLED = ("succeeded", "failed")
ATTEMPTS = 3  # at one task; a fourth rarely differs from the third, and the contract or the task has to change
OPEN_SECONDS = 120  # for `orca open` to bring the app up
HANDOFF_TEMPLATE = ROOT / "skills" / "master" / "handoff-template.md"
ROLES = (
    "Architecture", "Research", "Reference Implementation", "Feature Implementation",
    "Release / Operations", "Incident Response", "Maintenance",
)
LINEAGE_TEXT_FIELDS = (
    "parent_session",
    "successor_session",
    "inherited_role",
    "succession_reason",
    "inherited_open_tracks",
    "context_loss_summary",
)


def state_dir() -> Path:
    path = Path.cwd() / ".orchestration"
    path.mkdir(exist_ok=True)
    return path


def orca() -> str:
    # The vendored gate honours the same override, so both always talk to the same Orca.
    return os.environ.get("ORCA_CLI_COMMAND", "orca")


def run(command: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(
        command, capture_output=True, text=True, encoding="utf-8", errors="replace", **kwargs
    )


def fail(message: str) -> int:
    print(f"orchestration: {message}", file=sys.stderr)
    return 2


def tiers() -> dict[str, list[str]]:
    return json.loads(POLICY.read_text(encoding="utf-8"))["tiers"]


def family(model: str) -> str | None:
    """Return the policy entry a model falls under: one named like it, else one that is a "-"-separated part of it.

    The policy lists families, not versioned ids, so a new version of a model needs no edit anywhere.
    """
    name = model.casefold()
    entries = [entry for models in tiers().values() for entry in models]
    exact = [entry for entry in entries if entry.casefold() == name]
    part = [entry for entry in entries if entry.casefold() in name.split("-")]
    return next(iter(exact + part), None)


def codex_model() -> str | None:
    """Return the model the owner's Codex config selects, so that no Codex model name is kept in this project."""
    # shortcut: reads the top-level `model` only; follow the active profile once the Codex config uses profiles.
    try:
        return tomllib.loads((Path.home() / ".codex" / "config.toml").read_text(encoding="utf-8")).get("model")
    except (OSError, ValueError):
        return None


def launch_model(agent: str, model: str) -> str:
    """Return the versioned id Claude Code resolves a family to right now, or the model as typed.

    Orca's built-in catalogue refuses an effort level for some family names (haiku in 1.4.256); a versioned id
    is outside that catalogue and keeps its effort.
    """
    if agent != "claude":
        return model
    # shortcut: list_models is the CLI control request Orca's own model discovery sends, and it is not in the
    # public docs. If it stops answering, the model goes to Orca as typed; move to the Agent SDK's supportedModels() then.
    command = [
        os.environ.get("CLAUDE_CLI_COMMAND", "claude"), "-p", "--input-format", "stream-json",
        "--output-format", "stream-json", "--verbose", "--strict-mcp-config", "--settings", '{"disableAllHooks":true}',
    ]
    try:
        listing = run(command, input=MODEL_LIST_REQUEST, timeout=30).stdout
    except (OSError, subprocess.TimeoutExpired):
        return model
    for line in listing.splitlines():
        try:
            rows = json.loads(line)["response"]["response"]["models"]
        except (ValueError, KeyError, TypeError):
            continue
        for row in rows:
            if isinstance(row, dict) and str(row.get("value")).casefold() == model.casefold() and row.get("resolvedModel"):
                return row["resolvedModel"]
    return model


def launch_problem(model: str) -> str | None:
    """Return why a model may not be launched, or None. Only models the policy covers are allowed."""
    if family(model) is None:
        return f"model {model!r} is not listed in {POLICY}"
    return None


def top_tier_problem(model: str, effort: str) -> str | None:
    """Top-tier models are reserved for re-runs at medium effort."""
    if family(model) in tiers().get("top", []) and effort != "medium":
        return f"top-tier model {model!r} may only run at effort 'medium'"
    return None


def gate_run(*arguments: str) -> subprocess.CompletedProcess:
    """Run the vendored dispatch gate on this workspace's ledger."""
    state = state_dir()
    env = {**os.environ, "MOGUI_STATE_DIR": str(state), "PYTHONUTF8": "1"}
    command = [*PYTHON, str(GATE), "--ledger", str(state / "dispatch-ledger.jsonl"), *arguments]
    # The vendored gate locks with fcntl, which does not exist on Windows, so gate calls are serialised here.
    # shortcut: a byte lock on one machine; replace it if masters on several hosts ever share a state folder.
    with open(state / "orchestration.lock", "a+b") as lock:
        lock.seek(0)
        while True:
            try:
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                break
            except OSError:
                time.sleep(0.2)
        return run(command, env=env)


def gate(*arguments: str) -> tuple[int, dict]:
    """Run the vendored dispatch gate and return its exit code and its one-line JSON decision."""
    result = gate_run(*arguments)
    try:
        return result.returncode, json.loads(result.stdout.strip().splitlines()[-1])
    except (IndexError, ValueError):
        unreadable = {"allow": False, "reason": "GATE_OUTPUT_UNREADABLE", "message": result.stderr.strip()[-500:]}
        return result.returncode or 1, unreadable


def contract_title(contract: str) -> str:
    """Return the contract's first heading. Orca names the Task, and a Run it creates for it, after the first
    line of the spec, which is the front matter's `---` unless a title is passed."""
    for line in Path(contract).read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return Path(contract).stem


def ledger_rows() -> list[dict]:
    """Return the rows of this workspace's dispatch ledger. A line that is not a JSON object is left out."""
    ledger = state_dir() / "dispatch-ledger.jsonl"
    rows = []
    for line in ledger.read_text(encoding="utf-8", errors="replace").splitlines() if ledger.exists() else []:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def task_of(row: dict) -> object:
    """Return the task a registered dispatch belongs to: the Orca Task of the first dispatch at it.

    The gate writes it into the row's `card`. A row from before that was passed is the first dispatch at its task.
    """
    return row.get("card") or row.get("orchestration_task")


def attempts() -> dict[object, list[str]]:
    """Map every task of this workspace's ledger to the dispatches the gate registered for it, oldest first."""
    tasks: dict[object, list[str]] = {}
    for row in ledger_rows():
        if row.get("job_id") and row.get("decision") == "ALLOW" and task_of(row):
            tasks.setdefault(task_of(row), []).append(row["job_id"])
    return tasks


def same_contract(contract: str) -> list[str]:
    """Return the earlier dispatches of this very contract, oldest first."""
    sha = hashlib.sha256(Path(contract).read_bytes()).hexdigest()  # as the gate hashes a contract
    return [row["job_id"] for row in ledger_rows() if row.get("contract_sha") == sha and row.get("job_id")]


def unsettled_dispatch(earlier: list[str]) -> tuple[str, object] | None:
    """Return the newest of these dispatches that Orca does not show as settled, with the outcome it gives.

    A second worker on one contract, or on one task, repeats the first one's edits in the same files. It is
    asked for most often after an Orca restart, when the first worker's report is missing and its work is not.
    Only a settled outcome clears a dispatch: no answer about it, or an outcome this code does not know,
    leaves it held.
    """
    for dispatch_id in dict.fromkeys(reversed(earlier)):
        shown = orca_result("orchestration", "worker-show", "--dispatch", dispatch_id, "--full") or {}
        projection = shown.get("projection")
        outcome = projection.get("outcome") if isinstance(projection, dict) else None
        if outcome not in SETTLED:
            return dispatch_id, outcome
    return None


class Refused(Exception):
    """A directory cannot be made ready: the reason for the line, with the message for the master."""

    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason


def git_says(folder: Path, *arguments: str) -> list[str] | None:
    """Return the lines `git rev-parse` prints in folder, or None where the folder is in no repository.

    Any other failure is not "no repository". Git refuses a repository that another account owns with the same
    exit code, and a harness installed there as into a plain folder would end in a second repository inside it.
    """
    try:
        # LC_ALL: the one message that is read below must come in English.
        done = run(["git", "-C", str(folder), "rev-parse", *arguments], env={**os.environ, "LC_ALL": "C"})
    except OSError as error:
        raise Refused("GIT_UNREADABLE", f"git cannot be run, so nothing was changed: {error}") from error
    if done.returncode == 0:
        return done.stdout.splitlines()
    if "not a git repository" in done.stderr:
        return None
    said = done.stderr.strip()[-400:] or f"exit {done.returncode}"
    raise Refused("GIT_UNREADABLE", f"git did not say what {folder} is, so nothing was changed. It said: {said}")


def orca_knows(target: Path) -> dict | str | None:
    """Return Orca's record of the project at target, None if it has none, or what Orca said that cannot be used."""
    repos = listed(orca_answer("repo", "list"), "repos")
    if not isinstance(repos, list):
        return repos or "no answer"
    for repo in repos:
        try:
            # As resolved paths: Orca keeps a path as it was given, with either slash, in any case, through a junction.
            if isinstance(repo.get("path"), str) and Path(repo["path"]).resolve() == target:
                return repo
        except (OSError, ValueError):  # a path of another machine
            continue
    return None


def own_git_hooks(target: Path) -> list[str]:
    """Name the git hooks that already run in a project from somewhere other than .githooks.

    The harness's setup points git at .githooks, and git then runs no other hook: not the four that git-lfs
    keeps in .git/hooks (pre-push among them), and not a hook folder the project or the account chose.
    """
    configured = run(["git", "-C", str(target), "config", "--get", "core.hooksPath"]).stdout.strip()
    if configured:  # for this repository or for every one of this account, whether the folder holds a hook yet or not
        return [] if configured == ".githooks" else [f"core.hooksPath = {configured}"]
    listed_path = git_says(target, "--git-path", "hooks")
    folder = (target / listed_path[0]).resolve() if listed_path else None
    if folder is None or not folder.is_dir():
        return []
    return sorted(entry.name for entry in folder.iterdir() if entry.is_file() and entry.suffix != ".sample")


def set_up_harness(target: Path, attention: list[str]) -> str:
    """Run the harness's setup in a project that is ready for it, and say where the setup stands.

    It runs once, and only where the project holds the harness's scripts and git hooks exactly as this plugin
    ships them: what runs is then the plugin's own, and no file of the project waits for the owner's decision.
    """
    files = harness.sources()
    done = (target / ".setup-done").exists()
    starters = sorted(f"{relative}.starter" for relative in files if os.path.lexists(f"{target / relative}.starter"))
    if starters:
        attention.append(
            f"{len(starters)} file(s) of the project differ from the harness's, whose versions lie beside them: "
            f"{', '.join(starters)}. Report them to the owner, merge as the owner decides and delete them."
            + ("" if done else " Then run this again: it runs the setup once no starter is left.")
        )
    # PROMPTS.md describes the placeholders and keeps them. The harness's own are filled by now.
    unfilled = sorted(
        relative for relative, source in files.items()
        if relative != "PROMPTS.md" and harness.PLACEHOLDER.search(source.read_bytes())
        and (target / relative).is_file() and harness.PLACEHOLDER.search((target / relative).read_bytes())
    )
    if unfilled:
        attention.append(
            f"Double braces that are no placeholder of this harness are still in: {', '.join(unfilled)}. The router "
            "there tells a worker's first session to stop and ask about them: fill them from what the repository and "
            "the owner's request show, or ask the owner. CLAUDE.md, AGENTS.md, .cursorrules and .windsurfrules stay identical."
        )
    if done:
        return "done"
    if starters:
        return "waits"
    # What runs must be the plugin's own: the setup's scripts, and the git hooks it switches on. The pages are
    # there to be changed (placeholders filled, .gitignore merged, wiki pages dated by the first commit), and a
    # rule that asked for them as shipped never let the setup run in a project that had been made ready.
    code = (relative for relative in files if relative.endswith(".sh") or relative.startswith(".githooks/"))
    if not all(harness.same_content(files[relative], target / relative) for relative in code):
        return "left to the first session there"  # the project has changed that code; its router says what to run
    hooks = own_git_hooks(target)
    if hooks:
        attention.append(
            f"The project already has git hooks of its own ({', '.join(hooks)}), which stop running once "
            "`bash scripts/setup.sh` points git at .githooks. Tell the owner, merge the ones still needed into "
            ".githooks/, then run that script there."
        )
        return "waits"
    bash = harness.git_bash()
    if bash is None:
        return "waits"  # the caller names what the machine lacks
    try:
        ran = run([str(bash), "scripts/setup.sh"], cwd=target, timeout=600)
        problem = "" if ran.returncode == 0 else (ran.stdout + ran.stderr).strip()[-600:] or f"exit {ran.returncode}"
    except subprocess.TimeoutExpired:
        problem = "it ran for more than ten minutes"
    if problem:
        attention.append(f"`bash scripts/setup.sh` failed in the project: {problem}")
        return "failed"
    return "ran"


def make_ready(target: Path, line: dict, with_harness: bool) -> None:
    """Do the steps of `prepare_project` and write each one's outcome into the line, or raise Refused."""
    if target.parent == target or target == Path.home().resolve():
        # A repository made there would hold every project below it, and none of them could be prepared again.
        raise Refused("NOT_A_PROJECT", f"{target} is a drive's root or the home folder, which hold projects and are not one.")
    # A new project is a new folder in an existing one: a path whose parent is missing is a typing error.
    if not target.is_dir() and (target.exists() or not target.parent.is_dir()):
        raise Refused("NO_SUCH_DIRECTORY", f"{target} is not a directory. A new project is made only as a new folder in an existing one.")
    place = target if target.is_dir() else target.parent
    inside = git_says(place, "--git-dir", "--git-common-dir", "--show-toplevel")
    if inside:
        git_dir, common, top = ((place / entry).resolve() for entry in inside)
        if top != target or git_dir != common:
            # A subfolder gets no harness of its own, and a linked worktree would become a second project in Orca.
            raise Refused(
                "NOT_A_PROJECT_ROOT",
                f"{target} belongs to the git repository whose git folder is {common}, and is not the root of its "
                "main checkout. A project is such a root, or a directory outside every repository: name that root "
                "as the project. A worktree that Orca already shows takes `--worktree path:<folder>` instead.",
            )
    # Asked before the first write: with Orca away, nothing is changed.
    repo = orca_knows(target)
    if isinstance(repo, str):
        raise Refused("ORCA_UNREADABLE", f"Orca did not say which projects it has, so nothing was changed. It said: {repo}")

    attention: list[str] = []
    if with_harness and harness.SOURCE.is_relative_to(target):
        line["harness"] = "skipped"
        attention.append("The directory holds this plugin's own harness template, so no harness was installed in it.")
    elif with_harness:
        if (target / ".agent" / "Instructions.md").is_file():
            line["harness"] = "present"
        else:
            written = harness.install(target)
            if isinstance(written, str):
                raise Refused("HARNESS_NOT_INSTALLED", f"the harness could not be installed in {target}: {written}")
            line["harness"] = "installed"
            attention.append(
                "The harness is new there and not committed: a worker in a workspace of its own sees only what is "
                "committed. For the commit, stage its files by name, and its scripts and git hooks with the executable "
                f"bit, which Windows does not record and other systems need: {harness.STAGE_EXECUTABLE}"
            )
            if written["appended"]:
                attention.append(
                    f"The project's .gitignore lacked {len(written['appended'])} line(s) of the harness's, which are now "
                    f"appended to it: {', '.join(written['appended'])}. Name them in your report."
                )
        # On every run: a harness installed before this was done, or a starter the owner has since taken, is filled too.
        filled = harness.fill(target)
        if filled:
            attention.append(
                f"The harness's placeholders are now filled in {', '.join(filled)}: the project's name, repository and "
                "branch as the folder and git show them, and a neutral value for what they do not show. Nothing waits "
                "for this. Name it in your report, for the owner to correct. A rule the owner's request already gives "
                "(a release rule, a tone) is yours to write there: alike in CLAUDE.md, AGENTS.md, .cursorrules and .windsurfrules."
            )
        line["setup"] = set_up_harness(target, attention)
        identity = (run(["git", "-C", str(target), "config", "--get", key]).stdout.strip() for key in ("user.name", "user.email"))
        if git_says(target, "--show-toplevel") and not all(identity):
            # Found before a worker has done its task: git refuses its commit ("Author identity unknown").
            attention.append(
                "git has no author name or email for this project (`git config user.name`, `user.email`), so nobody "
                "can commit there: not the harness, and not a worker its work. Which identity to set is the owner's decision."
            )
        if line["harness"] == "installed" or harness.git_bash() is None:
            attention += [f"Missing on this machine: {tool}" for tool in harness.missing_tools()]

    if repo is None:
        # After the setup, which makes a new project a git repository. Orca refuses a plain folder as a Git project.
        kind = "git" if git_says(target, "--show-toplevel") else "folder"
        answer = orca_answer("repo", "add", "--path", str(target), "--kind", kind)
        result = answer.get("result") if answer.get("ok") else None
        repo = result.get("repo") if isinstance(result, dict) else None
        if not isinstance(repo, dict):
            said = json.dumps(answer.get("error") or "an answer without `result.repo`")[:300] if answer else "nothing"
            raise Refused(
                "ORCA_REFUSED",
                f"Orca did not add {target} as a project. It said: {said}. What this line says of the harness is done; "
                "run this again once Orca takes the project.",
            )
        line["orca"] = "added"
    else:
        line["orca"] = "registered"
    line.update(kind=repo.get("kind"), worktree=f"path:{target}", attention=attention)


def prepare_project(directory: str, with_harness: bool = True) -> dict:
    """Make a directory ready for a worker and return the line that says what was done.

    Ready means that Orca knows it as a project, so that a worker can start in it, and that it carries the
    repository harness, so that the worker finds its rules there. Every step looks first and leaves alone
    what is done, so a second run changes nothing. Nothing that exists is overwritten.

    Without the harness it makes a master's own workspace known to Orca: a master follows its skill, and the
    harness's router would give it a worker's rules.
    """
    target = Path(directory).resolve()
    line: dict = {"stage": "prepare", "allow": False, "project": str(target)}
    try:
        make_ready(target, line, with_harness)
    except Refused as refusal:
        return {**line, "reason": refusal.reason, "message": str(refusal)}
    except OSError as error:  # a folder that cannot be read or written; the line says how far the work came
        return {**line, "reason": "NOT_FINISHED", "message": f"stopped by the file system: {error}"}
    return {**line, "allow": True}


def prepare(args: argparse.Namespace) -> int:
    line = prepare_project(args.directory)
    print(json.dumps(line, ensure_ascii=False))
    return 0 if line["allow"] else 2


def dispatch(args: argparse.Namespace) -> int:
    handle = os.environ.get("ORCA_TERMINAL_HANDLE")
    if not handle:
        # Outside an Orca terminal, worker-start borrows the identity of whichever pane is active:
        # the worker starts in that pane's workspace and reports into that pane's Run.
        return fail(
            "ORCA_TERMINAL_HANDLE is not set; run the master inside an Orca terminal. `start` opens one on this "
            "checkout and carries the conversation into a fresh master there"
        )
    # Orca refuses --effort without --model, so a Codex worker needs a model string as well.
    model = args.model or (codex_model() if args.agent == "codex" else None)
    if not model:
        return fail("pass --model; only codex has a default, the model set in ~/.codex/config.toml")
    problem = launch_problem(model) or top_tier_problem(model, args.effort)
    if problem:
        return fail(problem)
    entry = family(model)  # what the gate and the ledger see

    contract = str(Path(args.contract).resolve())
    # All of this before the gate: its check is recorded and counts towards a tier's cap.
    origin, tried = None, []
    if args.follows:
        # A retry or a fix is one more attempt at the task of the dispatch it follows, whatever its contract file is.
        origin, tried = next(((task, ids) for task, ids in attempts().items() if args.follows in ids), (None, []))
        if origin is None:
            return fail(
                f"--follows names {args.follows}, which this workspace's ledger does not hold as a registered dispatch. "
                "Name the `dispatch` of an earlier dispatch line, or leave --follows out for a new task"
            )
        if len(tried) >= ATTEMPTS:
            print(json.dumps({"stage": "check", "allow": False, "reason": "TASK_ATTEMPTS_EXHAUSTED", "origin": origin, "attempts": tried}))
            return fail(
                f"the task of {args.follows} has had {len(tried)} attempts ({', '.join(tried)}). Stop, and tell the owner "
                "what was tried and why each attempt fell short: the contract or the task is what has to change"
            )
    same = same_contract(contract) if Path(contract).is_file() else []
    held = unsettled_dispatch([*tried, *same])
    if held:
        running, outcome = held
        what, reason = ("this contract", "CONTRACT_ALREADY_RUNNING") if running in same else ("this task", "TASK_ALREADY_RUNNING")
        print(json.dumps({"stage": "check", "allow": False, "reason": reason, "dispatch": running, "outcome": outcome}))
        if outcome == "in_progress":
            return fail(
                f"{what} already runs as dispatch {running}, which Orca shows open. Talk to that worker "
                f"(`orca orchestration send --to dispatch:{running}`) or stop it first"
            )
        return fail(
            f"{what} was dispatched before as {running}, and Orca does not show that dispatch as settled "
            f"(its outcome: {outcome}). Look at it with `orca orchestration worker-show --dispatch {running}` and "
            f"dispatch again once Orca shows it settled"
            + ("; a contract that differs from the earlier one is not held" if running in same else "")
        )
    place = ["--worktree", args.worktree or "current"]
    new = args.worktree in ("new-top-level", "new-child")  # a worktree that Orca makes for this worker
    home = str(Path.cwd())  # the checkout a new worktree is made from
    if args.project:
        if args.worktree not in (None, "new-top-level"):
            return fail("with --project, --worktree takes only new-top-level: a worktree of the worker's own in that project")
        if not Path(contract).is_file():  # a mistyped contract must not leave a project registered and changed
            return fail(f"no contract at {contract}")
        # Before the gate as well: a project that cannot take a worker must not use up a tier's cap.
        prepared = prepare_project(args.project)
        home = prepared["project"]
        if prepared["allow"] and new and run(["git", "-C", home, "cat-file", "-e", "HEAD:.agent/Instructions.md"]).returncode != 0:
            prepared["attention"].append(
                "The harness is not committed on the project's branch, so this worker starts without its rules."
            )
        print(json.dumps(prepared, ensure_ascii=False))
        if not prepared["allow"]:
            return 2
        place = ["--worktree", prepared["worktree"]]
    if new:
        branch = run(["git", "-C", home, "rev-parse", "--abbrev-ref", "HEAD"]).stdout.strip()
        if branch in ("", "HEAD"):
            return fail(
                f"{home} has no branch checked out with a commit on it, so no worktree can be made from it. "
                "Commit there first, or dispatch without --worktree: the worker then starts in that checkout itself"
            )
        # Orca starts a new worktree from the remote's branch unless it is told the base, and what a checkout holds
        # beyond its remote (a harness, the work of earlier workers, the master's own commits) is on the local one.
        # Without --project the repository is the master's own, which Orca takes from the terminal it is asked from.
        repo = ["--repo", f"path:{home}"] if args.project else []
        place = ["--worktree", args.worktree, *repo, "--base-branch", branch]
    check = [
        "check", "--runtime", args.agent, "--model", entry, "--tier-policy", str(POLICY),
        "--contract", contract, "--agents", "1", "--completion-channel", "orchestration",
    ]
    if args.tier_override:
        check += ["--tier-override", args.tier_override]
    code, decision = gate(*check)
    if code != 0:
        print(json.dumps({"stage": "check", **decision}))
        return 2
    # The session-start hook reads this after a compaction, to remind the master and no other session here.
    # Written before the worker exists: a write that fails must not leave a running worker without its line.
    (state_dir() / "master").write_text(handle, encoding="utf-8")

    model = launch_model(args.agent, model)  # what Orca starts
    started_at = time.time()
    start = run([
        orca(), "orchestration", "worker-start", "--from", handle, "--spec-file", contract,
        "--task-title", contract_title(contract), "--agent", args.agent, "--model", model, "--effort", args.effort,
        *place, "--json", "--full",
    ])
    try:
        receipt = json.loads(start.stdout)["result"]
        dispatch_id, task_id = receipt["dispatchId"], receipt["taskId"]
    except (ValueError, KeyError, TypeError):
        receipt = None
    if start.returncode != 0 or receipt is None:
        # Never retried here: Orca's recovery guide says to read failedStage before any retry.
        print(start.stdout or start.stderr)
        return fail("worker-start failed; load Orca's recovery guide before retrying")

    # Orca names a workspace "<id>::<folder>".
    folder = next(
        (effect["id"].split("::", 1)[-1] for effect in receipt.get("effects", []) if effect.get("kind") == "worktree"),
        "",
    )
    register = [
        "register", "--job-id", dispatch_id, "--contract-sha", decision["contract_sha"],
        "--runtime", args.agent, "--tier-policy", str(POLICY), "--orchestration-task", task_id,
        "--declared-model", entry,
        # The gate's `card` column: the task this dispatch is an attempt at. Its own `attempt` counts one contract file.
        "--card", str(origin or task_id),
        "--probe-cmd",
        subprocess.list2cmdline([orca(), "orchestration", "worker-show", "--dispatch", dispatch_id, "--json"]),
    ]
    if folder:
        probe = [*PYTHON, str(Path(__file__).resolve()), "probe-model", args.agent, folder, str(started_at)]
        register += ["--model-probe-cmd", subprocess.list2cmdline(probe)]
    code, registered = gate(*register)
    # `workspace` is the folder the worker runs in: a worktree of its own holds the branch to review.
    started = {
        "stage": "register", "dispatch": dispatch_id, "task": task_id, "run": receipt.get("runId"), "model": model,
        "workspace": folder, "origin": origin or task_id, "task_attempt": len(tried) + 1,
    }
    if code != 0:
        print(json.dumps({**started, **registered}))
        return fail(f"register denied but worker {dispatch_id} is already running; stop it with `orca orchestration worker-stop`")
    # Orca's guide tells a coordinator to follow a receipt's `next`, so the step that must not be skipped goes there.
    wait = f"keep one `ORCHESTRATION watch --run {receipt.get('runId')}` running in the background, then end your turn"
    print(json.dumps({**started, **registered, "next": wait}))
    return 0


class OrcaMissing(Exception):
    """The Orca CLI could not be started at all."""


def orca_answer(*arguments: str) -> dict:
    """Return Orca's answer to a read-only call, or {} while it gives none: away, starting or too slow."""
    try:
        # While the app is down some calls wait for it instead of failing, so every call is bounded.
        done = run([orca(), *arguments, "--json"], timeout=90)
    except OSError as error:
        # The CLI itself does not start. That is not Orca being away, and waiting does not mend it.
        raise OrcaMissing(
            f"the Orca CLI `{orca()}` cannot be run: {error}. Nothing was changed. See that Orca is installed and that "
            "`orca status` answers in this terminal, then run this again"
        ) from error
    except subprocess.TimeoutExpired:
        return {}
    try:
        answer = json.loads(done.stdout)
    except ValueError:
        # Text that is not JSON is an answer too, and waiting does not mend it: an option this Orca does not take.
        said = (done.stdout or done.stderr).strip()
        return {"ok": False, "error": said[-300:]} if said else {}
    return answer if isinstance(answer, dict) else {"ok": False, "error": "its answer is not a JSON object"}


def orca_result(*arguments: str) -> dict | None:
    """Return the result of a read-only Orca call, or None if Orca gave none."""
    answer = orca_answer(*arguments)
    result = answer.get("result") if answer.get("ok") else None
    return result if isinstance(result, dict) else None


def runtime_id() -> str | None:
    """Return the id of the Orca runtime that answers, or None while none does: away, starting or too slow."""
    return ((orca_result("status") or {}).get("runtime") or {}).get("runtimeId")


def unknown_shape(key: str) -> str:
    return f"`{key}` is not in the shape this wrapper reads; the Orca CLI may have changed"


def listed(answer: dict, key: str) -> list[dict] | str | None:
    """Return the rows an Orca answer holds under result[key].

    Return None for no answer and text for one that cannot be used: a refusal, or a shape this code does not
    know. An answer read loosely turns a renamed field into "no workers, no mail, all settled".
    """
    if not answer.get("ok"):
        return json.dumps(answer.get("error"))[:300] if answer else None
    result = answer.get("result")
    rows = result.get(key) if isinstance(result, dict) else None
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        return unknown_shape(key)
    return rows


def observe(run_id: str) -> tuple[str, list[dict], int] | str | None:
    """Return Orca's runtime id, every worker of the Run and the count of its unread mail.

    Return None while Orca gives no answer, and the reason as text when it answers what cannot be used.
    Only reads: `check` would take the mail, and then the master would never see it.
    """
    # shortcut: a status answer without a runtime id is read as Orca being away, however it is worded, because
    # what Orca says while it starts has not been measured. A renamed field would make this watch wait for ever;
    # tell the two apart once those states are known.
    runtime = runtime_id()
    if not runtime:
        return None
    workers: list[dict] = []
    cursor = ""
    while True:
        page = orca_answer("orchestration", "worker-list", "--run", run_id, "--full", *(["--cursor", cursor] if cursor else []))
        rows = listed(page, "workers")
        if not isinstance(rows, list):
            return rows
        for row in rows:
            projection = row.get("projection")
            if not isinstance(projection, dict) or not all(isinstance(projection.get(name), str) for name in ("dispatchId", "outcome")):
                return unknown_shape("workers")
            # The row's `resource` says whether the terminal was released. The projection's own only says who owns it.
            workers.append({**projection, "terminal": row.get("agentTerminalHandle"), "resource": row.get("resource")})
        more = page["result"].get("page")
        if not isinstance(more, dict) or not isinstance(more.get("hasMore"), bool):
            return unknown_shape("page")  # without it nothing says that these rows are the whole Run
        if not more["hasMore"]:
            break
        following = more.get("nextCursor")
        # More rows and no way on to them, and asking again would never end.
        if not rows or not isinstance(following, str) or following in ("", cursor):
            return unknown_shape("page")
        if len(workers) >= 2000:
            return "the Run holds more than 2,000 dispatches, and this watch reads no further"
        cursor = following
    # The inbox lists the newest mail first, so unread mail is in its first rows.
    inbox = listed(orca_answer("orchestration", "inbox", "--terminal", f"run:{run_id}", "--limit", "50", "--full"), "messages")
    if not isinstance(inbox, list):
        return inbox
    return runtime, workers, sum(not message.get("read") for message in inbox)


def next_step(worker: dict) -> str | None:
    """Return the command Orca itself names as this worker's next step, if it names one."""
    action = worker.get("nextAction")
    argv = action.get("argv") if isinstance(action, dict) else None
    if not isinstance(argv, list) or not argv or action.get("kind") in (None, "none"):
        return None
    return subprocess.list2cmdline(["orca", *map(str, argv)])


def kept_open(worker: dict) -> str | None:
    """Say that a settled worker still holds its terminal, if Orca shows that it does.

    Orca closes a finished worker by itself, but not one that got a worktree of its own: measured in 1.4.258,
    it keeps that terminal (`resource.releaseState: retained`) and names no next step for it.
    """
    resource = worker.get("resource")
    if not isinstance(resource, dict) or resource.get("releaseState") != "retained":
        return None
    return (
        f"watch: dispatch {worker['dispatchId']} has settled and Orca keeps its terminal open "
        f"({resource.get('retainedReason')}). Once its work is merged or given up, release it: "
        f"orca orchestration worker-release --dispatch {worker['dispatchId']}"
    )


def last_printed(worker: dict) -> float:
    """Return when a worker's terminal last printed, in epoch seconds, or 0 if it has not since Orca started."""
    shown = orca_result("terminal", "show", "--terminal", worker["terminal"]) if worker.get("terminal") else None
    return (((shown or {}).get("terminal") or {}).get("lastOutputAt") or 0) / 1000


def watch(args: argparse.Namespace) -> int:
    """Wait for a Run's workers. Return, which wakes a master that runs this in the background, once the master
    has to act and Orca has not woken it, or once nothing is left to wait for.

    Orca wakes a master by typing into its terminal. After a restart it types into no terminal that it has not
    heard from since, so a report that arrives then stays unread behind an idle master.
    """
    # One watch per master and Run: a second one would only wake the master twice for the same thing.
    lock = open(state_dir() / f"watch-{os.environ.get('ORCA_TERMINAL_HANDLE', 'no-terminal')}-{args.run}.lock", "a+b")
    lock.seek(0)
    try:
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        print(f"watch: you already have a watch on {args.run}. Nothing to do.")
        return 0
    try:
        first_runtime = unread_since = None
        quiet_since: dict[str, float] = {}
        refused = 0
        while True:
            seen = observe(args.run)
            if not isinstance(seen, tuple):
                # No answer is what this watch waits out: Orca is away, starting or busy. An answer that cannot be
                # used would never end, and the master would wait on a watch that sees nothing.
                refused = refused + 1 if seen else 0
                if refused >= 5:
                    return fail(
                        f"watch: Orca has given no usable answer about {args.run} {refused} times in a row, so nothing "
                        f"watches it. The last one: {seen}"
                    )
                time.sleep(args.poll)
                continue
            refused = 0
            runtime, listed_workers, unread = seen
            if not listed_workers:
                # Orca answers a Run it does not know with an empty list, and a Run keeps its settled dispatches.
                return fail(
                    f"watch: Orca lists no dispatch in {args.run}, so there is nothing to watch. Check the Run id: "
                    f"the dispatch line gives it, and so does `orca orchestration run-current --json`"
                )
            workers = [worker for worker in listed_workers if worker["outcome"] not in SETTLED]
            first_runtime = first_runtime or runtime
            restarted = " Orca was restarted while you waited." if runtime != first_runtime else ""
            now = time.time()
            unread_since = (unread_since or now) if unread else None
            # Orca does not show every busy worker as working: none after a restart until it reports, and none whose
            # agent sends no status (activity `unknown`, liveness `unverifiable` with reason `missing_status`, measured
            # in 1.4.258 on a healthy worker). Its word alone would call such a worker stopped.
            # The terminal still tells: an agent in a command keeps printing, one at its prompt does not.
            # shortcut: any output counts, and an idle terminal redraws now and then, which starts its window again.
            # Compare the screen's text between looks if a stopped worker gets reported late.
            quiet_since = {
                worker["dispatchId"]: max(quiet_since.get(worker["dispatchId"], now), last_printed(worker))
                for worker in workers
                if (worker.get("stage") or {}).get("activity") != "working"
            }
            wake = []
            if unread and now - unread_since >= args.grace:
                wake.append(
                    f"watch: {unread} message(s) for {args.run} are unread and Orca has not woken you.{restarted} "
                    f"Read them: orca orchestration check --run {args.run}"
                )
            for worker in workers:
                dispatch_id = worker["dispatchId"]
                if dispatch_id in quiet_since and now - quiet_since[dispatch_id] >= args.stalled_after:
                    activity = (worker.get("stage") or {}).get("activity")
                    liveness = (worker.get("liveness") or {}).get("verdict")
                    shown = orca_result("orchestration", "worker-show", "--dispatch", dispatch_id, "--full") or {}
                    parked = (shown.get("observation") or {}).get("agentWait")
                    waits = f" Orca sees it parked on a prompt that only a human can answer: {json.dumps(parked)[:200]}." if parked else ""
                    if worker["outcome"] != "in_progress":
                        waits += f" Orca gives it the outcome {worker['outcome']!r}, which this watch does not know and reads as open."
                    wake.append(
                        f"watch: dispatch {dispatch_id} is open and its worker's screen in {worker['terminal']} has not "
                        f"changed for {int(now - quiet_since[dispatch_id])} s (activity {activity}, liveness {liveness}).{restarted}{waits} "
                        f"Look at it: {next_step(worker) or f'orca orchestration worker-read --dispatch {dispatch_id}'}"
                    )
            if not workers and not unread:
                # Orca closes a finished worker by itself, unless it names a step for it: a terminal to release.
                steps = [(worker["dispatchId"], next_step(worker)) for worker in listed_workers]
                steps = [f"watch: Orca names a next step for dispatch {dispatch_id}: {step}" for dispatch_id, step in steps if step]
                steps += [kept for kept in (kept_open(worker) for worker in listed_workers if not next_step(worker)) if kept]
                # Orca answers a `send` to a settled dispatch by starting a new worker, which no gate has checked.
                # Only where this workspace has dispatched: a watch elsewhere has no ledger to compare with.
                gated = {row.get("job_id") for row in ledger_rows()} - {None}
                steps += [
                    f"watch: dispatch {worker['dispatchId']} of {args.run} is not in this workspace's ledger, so it did "
                    "not pass the gate. A `send` to a settled dispatch starts such a worker. Name it in your report."
                    for worker in listed_workers if gated and worker["dispatchId"] not in gated
                ]
                wake.append(
                    f"watch: every dispatch of {args.run} has settled and its mail is read."
                    + ("" if steps else " Nothing to do.")
                )
                wake += steps
            for line in wake:
                print(line)
            if wake:
                return 0
            time.sleep(args.poll)
    finally:
        lock.close()
        try:
            Path(lock.name).unlink()
        except OSError:  # another watch opened it to find it locked
            pass


def session_records(session: Path):
    """Yield the JSON objects of a session file, one per line; a line that is not one is skipped."""
    with open(session, encoding="utf-8", errors="replace") as lines:
        for line in lines:
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if isinstance(entry, dict):
                yield entry


def rollout_folder(session: Path) -> str:
    """Return the folder a Codex rollout says it runs in. Its first record holds it."""
    payload = next(session_records(session), {}).get("payload")
    return os.path.normcase(os.path.normpath(str(payload.get("cwd")))) if isinstance(payload, dict) else ""


def session_model(agent: str, session: Path) -> str | None:
    """Return the model the last turn of a session ran on, or None before its first turn."""
    model = None
    for entry in session_records(session):
        if agent == "codex":
            payload = entry.get("payload") if entry.get("type") == "turn_context" else None
            candidate = payload.get("model") if isinstance(payload, dict) else None
        else:
            main = entry.get("type") == "assistant" and not entry.get("isSidechain")
            candidate = (entry.get("message") or {}).get("model") if main else None
        if isinstance(candidate, str) and candidate and not candidate.startswith("<"):
            model = candidate
    return model


def probe_model(args: argparse.Namespace) -> int:
    """Print the model family of the newest session an agent started in a folder since a given time."""
    if args.agent == "codex":
        # Measured with Orca 1.4.257: its Codex workers write ~/.codex/sessions/<year>/<month>/<day>/rollout-*.jsonl.
        sessions, pattern = Path.home() / ".codex" / "sessions", "*/*/*/rollout-*.jsonl"
    else:
        # Claude Code stores a folder's transcripts under its path with every non-alphanumeric character replaced by "-".
        sessions, pattern = Path.home() / ".claude" / "projects" / re.sub(r"[^A-Za-z0-9]", "-", args.folder), "*.jsonl"
    folder = os.path.normcase(os.path.normpath(args.folder))
    deadline = time.monotonic() + 25  # the gate kills this probe after 30 seconds
    while time.monotonic() < deadline:
        # shortcut: takes the newest session born after the dispatch; another session started in the same folder
        # in that window could be read instead. Switch to the session id once Orca reports it in the receipt.
        born = [path for path in sessions.glob(pattern) if path.stat().st_birthtime >= args.since - 2]
        if args.agent == "codex":  # one folder holds the rollouts of every Codex session on the machine
            born = [path for path in born if rollout_folder(path) == folder]
        for session in sorted(born, key=lambda path: path.stat().st_birthtime, reverse=True)[:1]:
            model = session_model(args.agent, session)
            if model:
                # The session holds a versioned id; the gate compares families. An id outside the policy stays as it is.
                print(family(model) or model)
                return 0
        time.sleep(1)
    return 1


def sections(document: str) -> dict[str, str]:
    """Map every `## ` heading of a document to the text under it.

    A `## ` line inside a code fence is text: `git status -sb`, pasted as a baseline, prints one.
    """
    found: dict[str, list[str]] = {}
    title, fenced = None, False
    for line in document.splitlines():
        fenced ^= line.lstrip().startswith("```")
        heading = None if fenced else re.fullmatch(r"## +(.+?) *", line)
        if heading:
            title = heading.group(1)
            found[title] = []
        elif title is not None:
            found[title].append(line)
    return {title: "\n".join(body).strip() for title, body in found.items()}


def handoff_problems(document: str) -> list[str]:
    """Name what a successor could not work from: the handoff is all it gets of the predecessor's state."""
    written, template = sections(document), sections(HANDOFF_TEMPLATE.read_text(encoding="utf-8"))
    problems = []
    for heading in HANDOFF_HEADINGS:
        if heading not in written:
            problems.append(f"`## {heading}` is missing")
        elif not written[heading]:
            problems.append(f"`## {heading}` is empty; write `none` where there is nothing to hand over")
        elif heading != "Role State" and written[heading] == template.get(heading):
            problems.append(f"`## {heading}` still holds the template's text")
    state = dict(re.findall(r"(?m)^(Current Role|Role Lock|Frozen|Unlock): *(\S.*?) *$", written.get("Role State", "")))
    if "Role State" in written:
        if state.get("Current Role") not in ROLES:
            problems.append("Role State: `Current Role` must be one of " + ", ".join(ROLES))
        if state.get("Role Lock") not in ("ENABLED", "DISABLED"):
            problems.append("Role State: `Role Lock` must be ENABLED or DISABLED")
        problems += [f"Role State: `{name}` is missing; write `none`" for name in ("Frozen", "Unlock") if name not in state]
    return problems


def workspace_known() -> bool:
    """See that a workspace of Orca's holds the current folder, which a launch needs, and say whether one does."""
    unknown = orca_answer("worktree", "current").get("error")  # text when Orca does not answer in JSON
    if isinstance(unknown, dict) and unknown.get("code") == "selector_not_found":
        # A first master that starts outside Orca: its folder is none of Orca's workspaces yet.
        known = prepare_project(str(Path.cwd()), with_harness=False)
        print(json.dumps(known, ensure_ascii=False))
        return known["allow"]
    return True


def start(args: argparse.Namespace) -> int:
    """Carry a conversation that began outside Orca into a fresh master in an Orca terminal on this checkout.

    A master dispatches from its terminal's handle, and a session in any other terminal has none. `agent launch`
    with the brief as its prompt is how Orca's own guide hands work to a fresh agent in an existing checkout.
    """
    own = os.environ.get("ORCA_TERMINAL_HANDLE")
    if own:
        return fail(f"this session already runs in an Orca terminal ({own}): it is the master itself, and starts no second one")
    if not runtime_id():
        try:
            # Without pipes: the app that `orca open` starts inherits them, and a read of them ends only when Orca is closed.
            # shortcut: that app also inherits this session's environment. Orca 1.4.258 keeps CLAUDECODE and the session
            # ids out of its terminals and lets a setting through (CLAUDE_CODE_ENABLE_PROMPT_SUGGESTION, measured);
            # start it with the logon environment once a setting that gets through harms the sessions in Orca.
            subprocess.run(
                [orca(), "open"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=OPEN_SECONDS,
            )
        except subprocess.TimeoutExpired:
            pass  # the status below says whether Orca came up
        if not runtime_id():
            status = orca_answer("status")  # an error, or a result whose runtime is not running
            said = json.dumps(status.get("error") or (status.get("result") or {}).get("runtime") or "nothing")[:300]
            return fail(
                "Orca cannot be reached, so no master was started and nothing was changed. `orca open` was tried, and "
                f"`orca status` still says: {said}\n"
                "  1. Ask the owner to open the Orca app, and to say when its window is up.\n"
                "  2. See that `orca status` then shows its runtime as ready, and run this command again as it was.\n"
                "  3. If Orca does not come up, stop and tell the owner what `orca status` says. A master runs only in "
                "Orca: dispatch nothing from this terminal."
            )
    if not workspace_known():
        return 2
    # As one line: what a line break does on the way into the agent's terminal has not been measured.
    words = " ".join((args.said or "").split())
    carried = (
        f"This is what the owner said there, in the owner's words: {words} (end of the owner's words). If the owner "
        "gave a task there, it is your first task: name it in one line and start on it. If the owner gave none, or "
        "only said that one will follow, start on nothing: tell the owner in a line or two that you are ready and "
        "what you expect, and wait for the task."
    ) if words else (
        "The owner gave no task there. Tell the owner in a line or two that you are ready for the first task, and "
        "wait for it."
    )
    kickoff = (
        "You are the orchestration master of this workspace, started fresh. The owner asked a Claude Code session in "
        "a terminal outside Orca to act as master here. A master dispatches from an Orca terminal, which that session "
        "did not have, so it started you in its place and has stopped: you are the only master, and nothing has been "
        f"dispatched. Load the orchestration-kit:master skill and do what its section Before anything says. {carried}"
    )
    launch = run([
        # --surface as for a successor: a master in a chat has no handle to dispatch from. No --model and no --effort:
        # the agent starts as a new agent tab of the owner's does, which is how the owner's own session started.
        orca(), "agent", "launch", "--agent", "claude", "--surface", "terminal", "--focus", "--prompt", kickoff, "--json",
    ])
    try:
        result = json.loads(launch.stdout)["result"]
        terminal = result["outcome"]["handle"] if result["outcome"]["kind"] == "terminal" else None
        delivered = result["prompt"]["outcome"]
    except (ValueError, KeyError, TypeError):
        terminal = delivered = None
    here = Path.cwd()
    if launch.returncode != 0 or not isinstance(terminal, str):
        print(launch.stdout or launch.stderr)
        return fail(
            "orca agent launch named no terminal with a master in it. Look before you run this again, because a second "
            f"run would start a second master: `orca terminal list --worktree path:{here} --json`"
        )
    if delivered not in ("journaled", "handed-to-terminal"):  # what Orca's guide calls a delivered brief
        return fail(
            f"Orca started terminal {terminal} and does not say that the kickoff reached it (prompt outcome: "
            f"{delivered!r}). Read its screen with `orca terminal read --terminal {terminal} --screen`, and tell the "
            "owner what it shows. Do not run this again: the agent is there"
        )
    step = (
        "tell the owner that the master now runs in Orca, in the tab that is in front there, and that it takes the "
        "first task there. Then stop: this session is not the master, and dispatches nothing"
    )
    print(json.dumps({"stage": "start", "allow": True, "terminal": terminal, "workspace": str(here), "next": step}, ensure_ascii=False))
    return 0


def succeed(args: argparse.Namespace) -> int:
    problem = launch_problem(args.model) or top_tier_problem(args.model, args.effort)
    if problem:
        return fail(problem)
    handoff = Path(args.handoff).resolve()
    problems = handoff_problems(handoff.read_text(encoding="utf-8"))
    if problems:
        return fail("the handoff is not ready for a successor:\n  " + "\n  ".join(problems))

    takeover = f"Take over the Orca Run with: orca orchestration run-use --id {args.run}. " if args.run else ""
    own = os.environ.get("ORCA_TERMINAL_HANDLE")
    # inherit needs the predecessor's handle, and nothing else the successor reads is sure to hold it.
    parent = f"Its --parent-session is {own}, your predecessor's terminal. " if own else (
        "Your predecessor ran in no Orca terminal, so its --parent-session is a name for that session. "
    )
    kickoff = (
        f"You are the successor master for this workspace. Read the handoff at {handoff} and load the "
        f"orchestration-kit:master skill. {takeover}Then record the succession with the inherit command "
        f"described in that skill. {parent}Report what you inherited before doing any other work. That report "
        f"does not end your turn: the skill says when to carry on with the handoff's next step and when to wait."
    )
    if not workspace_known():
        return 2
    launch = run([
        orca(), "agent", "launch", "--agent", "claude", "--model", launch_model("claude", args.model), "--effort", args.effort,
        # A master dispatches from a terminal's handle. Without this, the owner's Chat | CLI setting decides.
        "--surface", "terminal", "--prompt", kickoff, "--json",
    ])
    print(launch.stdout or launch.stderr)
    return 0 if launch.returncode == 0 else fail("orca agent launch failed")


def retirement(predecessor: str) -> str:
    """Say whether the predecessor's terminal is still open in Orca: two masters on one Run both take its mail.

    Upstream proves retirement with ps and tty probes, which do not exist on Windows. Orca's list of live
    terminals is what can be measured here, and closing the terminal stays the owner's decision.
    """
    try:
        answer = orca_answer("terminal", "list")
    except OrcaMissing:
        answer = {}
    terminals = listed(answer, "terminals")
    if not isinstance(terminals, list) or answer["result"].get("truncated"):
        return "not verified (Orca gave no full list of its terminals)"
    handles = {terminal.get("handle") for terminal in terminals}
    if predecessor in handles:
        return f"not verified (terminal {predecessor} is still open)"
    # Absence proves something only if the list is read rightly, and this terminal's own handle in it shows that.
    own = os.environ.get("ORCA_TERMINAL_HANDLE")
    if own not in handles:
        return "not verified (Orca's list of terminals does not show this terminal either)"
    if predecessor.split("_")[0] != own.split("_")[0]:
        # A predecessor outside Orca is given by name. Nothing was looked for, so nothing is called verified.
        return f"no terminal to verify ({predecessor!r} is a session's name, not a terminal handle of Orca's)"
    return f"terminal {predecessor} is not among Orca's live terminals"


def inherit(args: argparse.Namespace) -> int:
    sys.path.insert(0, str(VENDOR / "src"))
    from master_runtime.core.lineage import LineageValidationError, append_entry

    ledger = state_dir() / "lineage.md"
    existing = ledger.read_text(encoding="utf-8") if ledger.exists() else ""
    entry: dict[str, object] = {name: getattr(args, name) for name in LINEAGE_TEXT_FIELDS}
    entry.update(
        generation=len(re.findall(r"^## Gen ", existing, flags=re.MULTILINE)) + 1,
        timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        recovery_sources=str(Path(args.handoff).resolve()),
        verification=args.verification,
        repeated_question_count=args.repeated_question_count,
        reopened_decision_count=args.reopened_decision_count,
        predecessor_retirement_verified=retirement(args.parent_session),
    )
    try:
        append_entry(ledger, entry)
    except LineageValidationError as error:
        return fail(str(error))
    if os.environ.get("ORCA_TERMINAL_HANDLE"):
        (state_dir() / "master").write_text(os.environ["ORCA_TERMINAL_HANDLE"], encoding="utf-8")
    print(f"lineage: recorded generation {entry['generation']} in {ledger}")
    print(f"lineage: predecessor retirement: {entry['predecessor_retirement_verified']}")
    return 0


def compacted(args: argparse.Namespace) -> int:
    """Session-start hook after a compaction: tell this workspace's master, and no other session, to hand over.

    A compacted master works from a summary of what it knew. The hook runs in every session of every project
    that has the plugin, so it says nothing unless this terminal is the one that last dispatched or inherited here.
    """
    marker = Path.cwd() / ".orchestration" / "master"  # not state_dir(): a hook leaves no folder behind
    handle = os.environ.get("ORCA_TERMINAL_HANDLE")
    if handle and marker.is_file() and marker.read_text(encoding="utf-8").strip() == handle:
        print(
            "orchestration-kit: this session is the orchestration master of this workspace, and its context has "
            "just been compacted. You now work from a summary. Load the orchestration-kit:master skill again and "
            'follow its section "Hand over to a successor": tell the owner that a handoff is due and write it '
            "before any other work. Open dispatches are in .orchestration/dispatch-ledger.jsonl, and "
            "`orca orchestration run-current --json` gives the Run."
        )
    return 0


def report(args: argparse.Namespace) -> int:
    ledger = Path.cwd() / ".orchestration" / "dispatch-ledger.jsonl"
    if not ledger.is_file():  # an empty summary from the wrong folder would read as "nothing was dispatched"
        return fail(f"no dispatch ledger at {ledger}; run this from the workspace root")
    done = gate_run("report", *(["--today"] if args.today else []))
    print(done.stdout + done.stderr, end="")
    # The gate counts per contract file. What a master and its successor need is the count per task.
    retried = {task: ids for task, ids in attempts().items() if len(ids) > 1}
    print("Tasks with more than one attempt (all ledger entries, oldest first):")
    print("\n".join(f"  {task}: {', '.join(ids)}" for task, ids in retried.items()) or "  <none>")
    return done.returncode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="orchestration", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    commands = parser.add_subparsers(dest="command", required=True)

    def launch_options(sub: argparse.ArgumentParser, model_required: bool) -> None:
        model_help = "a family listed in config/model-tier-policy.json, or a full id of that family to pin a version"
        sub.add_argument("--model", required=model_required, help=model_help)
        sub.add_argument("--effort", required=True, choices=("low", "medium", "high", "xhigh"))

    sub = commands.add_parser("prepare", help="register a project directory in Orca and give it the repository harness")
    sub.add_argument("directory", help="the project's root; a new folder is made when its parent exists")
    sub.set_defaults(handler=prepare)

    sub = commands.add_parser("dispatch", help="gate a contract, start one Orca worker, register it")
    sub.add_argument("--contract", required=True, help="contract file; it is also the worker's task spec")
    sub.add_argument("--agent", required=True, choices=("claude", "codex"))
    launch_options(sub, model_required=False)  # codex defaults to the model in its own config
    sub.add_argument(
        "--worktree",
        help="Orca worktree selector for the worker (default: current). new-top-level and new-child give the worker "
        "a worktree of its own, made from the branch that is checked out here, or in the project with --project, "
        "where only new-top-level is taken",
    )
    sub.add_argument(
        "--project", metavar="DIRECTORY",
        help="the project directory the task is for: it is prepared as `prepare` does, and the worker starts in it",
    )
    sub.add_argument("--tier-override", metavar="REASON", help="pass one request over a tier fan-out cap")
    sub.add_argument(
        "--follows", metavar="DISPATCH",
        help="the earlier dispatch this one retries or fixes: the ledger then holds both under one task, "
        f"and a task gets {ATTEMPTS} attempts",
    )
    sub.set_defaults(handler=dispatch)

    sub = commands.add_parser("watch", help="wait for a Run's workers; run it in the background")
    sub.add_argument("--run", required=True, help="Orca Run id, as the dispatch line gives it")
    sub.add_argument("--poll", type=float, default=10, help="seconds between looks at Orca (default: 10)")
    sub.add_argument("--grace", type=float, default=30, help="seconds mail may stay unread before you are woken (default: 30)")
    sub.add_argument(
        "--stalled-after", type=float, default=180,
        help="seconds the screen of an open worker that Orca does not show working may stand still before you are woken "
        "(default: 180)",
    )
    sub.set_defaults(handler=watch)

    sub = commands.add_parser("start", help="from a terminal outside Orca: start a fresh master in Orca on this checkout")
    sub.add_argument(
        "--said", metavar="WORDS",
        help="what the owner has asked for so far, in the owner's words; leave it out if that was only to act as master",
    )
    sub.set_defaults(handler=start)

    sub = commands.add_parser("succeed", help="launch a successor master from a handoff document")
    sub.add_argument("--handoff", required=True)
    launch_options(sub, model_required=True)
    sub.add_argument("--run", help="Orca Run id the successor should take over")
    sub.set_defaults(handler=succeed)

    sub = commands.add_parser("inherit", help="record the succession in .orchestration/lineage.md")
    sub.add_argument("--handoff", required=True)
    sub.add_argument("--verification", required=True, choices=("PASS", "PARTIAL", "FAILED"))
    for name in LINEAGE_TEXT_FIELDS:
        sub.add_argument("--" + name.replace("_", "-"), required=True)
    sub.add_argument("--repeated-question-count", type=int, default=0)
    sub.add_argument("--reopened-decision-count", type=int, default=0)
    sub.set_defaults(handler=inherit)

    sub = commands.add_parser("report", help="summarise the dispatch ledger")
    sub.add_argument("--today", action="store_true", help="only today's entries (UTC)")
    sub.set_defaults(handler=report)

    sub = commands.add_parser("probe-model")  # internal: called by the gate during register
    sub.add_argument("agent", choices=("claude", "codex"))
    sub.add_argument("folder")
    sub.add_argument("since", type=float)
    sub.set_defaults(handler=probe_model)

    commands.add_parser("compacted").set_defaults(handler=compacted)  # internal: the plugin's session-start hook

    args = parser.parse_args(argv)
    try:
        return args.handler(args)
    except OrcaMissing as error:
        return fail(str(error))


if __name__ == "__main__":
    sys.exit(main())
