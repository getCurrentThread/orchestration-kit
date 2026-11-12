"""orchestration-kit: gated worker dispatch and master succession on top of the Orca CLI.

Run from the workspace root as `python -X utf8 -s -B orchestration.py <command>`:
  dispatch  check a contract with the vendored dispatch gate, start one Orca worker, register it
  watch     wait for a Run's workers; return when the master must act and Orca has not woken it
  succeed   validate a handoff document and launch a successor master in Orca
  inherit   append the succession to the lineage ledger (run by the successor)

State is kept in ./.orchestration/ of the current directory.
"""

from __future__ import annotations

import argparse
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


def gate(*arguments: str) -> tuple[int, dict]:
    """Run the vendored dispatch gate and return its exit code and its one-line JSON decision."""
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
        result = run(command, env=env)
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


def dispatch(args: argparse.Namespace) -> int:
    handle = os.environ.get("ORCA_TERMINAL_HANDLE")
    if not handle:
        # Outside an Orca terminal, worker-start borrows the identity of whichever pane is active:
        # the worker starts in that pane's workspace and reports into that pane's Run.
        return fail("ORCA_TERMINAL_HANDLE is not set; run the master inside an Orca terminal")
    # Orca refuses --effort without --model, so a Codex worker needs a model string as well.
    model = args.model or (codex_model() if args.agent == "codex" else None)
    if not model:
        return fail("pass --model; only codex has a default, the model set in ~/.codex/config.toml")
    problem = launch_problem(model) or top_tier_problem(model, args.effort)
    if problem:
        return fail(problem)
    entry = family(model)  # what the gate and the ledger see

    contract = str(Path(args.contract).resolve())
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

    model = launch_model(args.agent, model)  # what Orca starts
    started_at = time.time()
    start = run([
        orca(), "orchestration", "worker-start", "--from", handle, "--spec-file", contract,
        "--task-title", contract_title(contract), "--agent", args.agent, "--model", model, "--effort", args.effort,
        "--worktree", args.worktree, "--json", "--full",
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
        "--probe-cmd",
        subprocess.list2cmdline([orca(), "orchestration", "worker-show", "--dispatch", dispatch_id, "--json"]),
    ]
    if args.agent == "claude" and folder:
        # shortcut: only Claude workers are measured; Codex is recorded as MODEL_UNVERIFIED until a rollout probe is added.
        probe = [*PYTHON, str(Path(__file__).resolve()), "probe-model", folder, str(started_at)]
        register += ["--model-probe-cmd", subprocess.list2cmdline(probe)]
    code, registered = gate(*register)
    started = {"stage": "register", "dispatch": dispatch_id, "task": task_id, "run": receipt.get("runId"), "model": model}
    if code != 0:
        print(json.dumps({**started, **registered}))
        return fail(f"register denied but worker {dispatch_id} is already running; stop it with `orca orchestration worker-stop`")
    # Orca's guide tells a coordinator to follow a receipt's `next`, so the step that must not be skipped goes there.
    wait = f"keep one `ORCHESTRATION watch --run {receipt.get('runId')}` running in the background, then end your turn"
    print(json.dumps({**started, **registered, "next": wait}))
    return 0


def orca_answer(*arguments: str) -> dict:
    """Return Orca's answer to a read-only call, or {} while it gives none: away, starting or too slow."""
    try:
        # While the app is down some calls wait for it instead of failing, so every call is bounded.
        answer = json.loads(run([orca(), *arguments, "--json"], timeout=90).stdout)
    except OSError as error:
        # The CLI itself does not start. That is not Orca being away, and waiting does not mend it.
        raise SystemExit(fail(f"watch: the Orca CLI `{orca()}` cannot be run, so nothing watches the Run: {error}"))
    except (ValueError, subprocess.TimeoutExpired):
        return {}
    return answer if isinstance(answer, dict) else {}


def orca_result(*arguments: str) -> dict | None:
    """Return the result of a read-only Orca call, or None if Orca gave none."""
    answer = orca_answer(*arguments)
    return answer.get("result") if answer.get("ok") else None


def observe(run_id: str) -> tuple[str, list[dict], int] | str | None:
    """Return Orca's runtime id, the Run's open workers and the count of its unread mail.

    Return None while Orca gives no answer, and its error as text when it answers that it will not show the Run.
    Only reads: `check` would take the mail, and then the master would never see it.
    """
    runtime = ((orca_result("status") or {}).get("runtime") or {}).get("runtimeId")
    if not runtime:
        return None
    listed = orca_answer("orchestration", "worker-list", "--run", run_id, "--full")
    # The inbox lists the newest mail first, so unread mail is in its first rows.
    inbox = orca_answer("orchestration", "inbox", "--terminal", f"run:{run_id}", "--limit", "50", "--full")
    for answer in (listed, inbox):
        if not answer.get("ok"):
            return json.dumps(answer.get("error"))[:300] if answer else None
    rows = (listed.get("result") or {}).get("workers") or []
    workers = [{**(row.get("projection") or {}), "terminal": row.get("agentTerminalHandle")} for row in rows]
    unread = [message for message in (inbox.get("result") or {}).get("messages") or [] if not message.get("read")]
    return runtime, [worker for worker in workers if worker.get("outcome") == "in_progress"], len(unread)


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
                # No answer is what this watch waits out: Orca is away, starting or busy. A refusal would never end,
                # and the master would wait on a watch that sees nothing.
                refused = refused + 1 if seen else 0
                if refused >= 5:
                    return fail(
                        f"watch: Orca has refused to show {args.run} {refused} times in a row, so nothing watches it. "
                        f"Its answer: {seen}"
                    )
                time.sleep(args.poll)
                continue
            refused = 0
            runtime, workers, unread = seen
            first_runtime = first_runtime or runtime
            restarted = " Orca was restarted while you waited." if runtime != first_runtime else ""
            now = time.time()
            unread_since = (unread_since or now) if unread else None
            # After a restart Orca shows no worker as working until it reports, so its word alone would call a busy
            # worker stopped. The terminal still tells: an agent in a command keeps printing, one at its prompt does not.
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
                    wake.append(
                        f"watch: dispatch {dispatch_id} is open and its worker's screen in {worker['terminal']} has not "
                        f"changed for {int(now - quiet_since[dispatch_id])} s (activity {activity}, liveness {liveness}).{restarted} "
                        f"Look at it: orca orchestration worker-read --dispatch {dispatch_id}"
                    )
            if not workers and not unread:
                wake.append(f"watch: every dispatch of {args.run} has settled and its mail is read. Nothing to do.")
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


def probe_model(args: argparse.Namespace) -> int:
    """Print the model family of the newest Claude Code session started in a folder since a given time."""
    # Claude Code stores a folder's transcripts under its path with every non-alphanumeric character replaced by "-".
    transcripts = Path.home() / ".claude" / "projects" / re.sub(r"[^A-Za-z0-9]", "-", args.folder)
    deadline = time.monotonic() + 25  # the gate kills this probe after 30 seconds
    while time.monotonic() < deadline:
        # shortcut: takes the newest session born after the dispatch; another session started in the same folder
        # in that window could be read instead. Switch to the session id once Orca reports it in the receipt.
        born = [path for path in transcripts.glob("*.jsonl") if path.stat().st_birthtime >= args.since - 2]
        for transcript in sorted(born, key=lambda path: path.stat().st_birthtime, reverse=True)[:1]:
            model = None
            for line in transcript.read_text(encoding="utf-8", errors="replace").splitlines():
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(entry, dict) or entry.get("type") != "assistant" or entry.get("isSidechain"):
                    continue
                candidate = (entry.get("message") or {}).get("model")
                if isinstance(candidate, str) and candidate and not candidate.startswith("<"):
                    model = candidate
            if model:
                # The transcript holds a versioned id; the gate compares families. An id outside the policy stays as it is.
                print(family(model) or model)
                return 0
        time.sleep(1)
    return 1


def succeed(args: argparse.Namespace) -> int:
    problem = launch_problem(args.model) or top_tier_problem(args.model, args.effort)
    if problem:
        return fail(problem)
    handoff = Path(args.handoff).resolve()
    text = handoff.read_text(encoding="utf-8")
    missing = [heading for heading in HANDOFF_HEADINGS if f"## {heading}" not in text]
    if missing:
        return fail(f"handoff is missing sections: {', '.join(missing)}")

    takeover = f"Take over the Orca Run with: orca orchestration run-use --id {args.run}. " if args.run else ""
    kickoff = (
        f"You are the successor master for this workspace. Read the handoff at {handoff} and load the "
        f"orchestration-kit:master skill. {takeover}Then record the succession with the inherit command "
        f"described in that skill, and report what you inherited before doing any other work. That report does "
        f"not end your turn: the skill says when to carry on with the handoff's next step and when to wait."
    )
    launch = run([
        orca(), "agent", "launch", "--agent", "claude", "--model", launch_model("claude", args.model), "--effort", args.effort,
        "--prompt", kickoff, "--json",
    ])
    print(launch.stdout or launch.stderr)
    return 0 if launch.returncode == 0 else fail("orca agent launch failed")


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
        # Upstream proves retirement with ps and tty probes, which do not exist on Windows.
        predecessor_retirement_verified="not verified (predecessor closed by hand)",
    )
    try:
        append_entry(ledger, entry)
    except LineageValidationError as error:
        return fail(str(error))
    print(f"lineage: recorded generation {entry['generation']} in {ledger}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="orchestration", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    commands = parser.add_subparsers(dest="command", required=True)

    def launch_options(sub: argparse.ArgumentParser, model_required: bool) -> None:
        model_help = "a family listed in config/model-tier-policy.json, or a full id of that family to pin a version"
        sub.add_argument("--model", required=model_required, help=model_help)
        sub.add_argument("--effort", required=True, choices=("low", "medium", "high", "xhigh"))

    sub = commands.add_parser("dispatch", help="gate a contract, start one Orca worker, register it")
    sub.add_argument("--contract", required=True, help="contract file; it is also the worker's task spec")
    sub.add_argument("--agent", required=True, choices=("claude", "codex"))
    launch_options(sub, model_required=False)  # codex defaults to the model in its own config
    sub.add_argument("--worktree", default="current", help="Orca worktree selector for the worker (default: current)")
    sub.add_argument("--tier-override", metavar="REASON", help="pass one request over a tier fan-out cap")
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

    sub = commands.add_parser("probe-model")  # internal: called by the gate during register
    sub.add_argument("folder")
    sub.add_argument("since", type=float)
    sub.set_defaults(handler=probe_model)

    args = parser.parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    sys.exit(main())
