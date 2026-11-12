"""orchestration-kit: gated worker dispatch and master succession on top of the Orca CLI.

Run from the workspace root as `python -X utf8 -s -B orchestration.py <command>`:
  dispatch  check a contract with the vendored dispatch gate, start one Orca worker, register it
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
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENDOR = ROOT / "vendor" / "mogui"
GATE = VENDOR / "scripts" / "dispatch-gate"
POLICY = ROOT / "config" / "model-tier-policy.json"
PYTHON = [sys.executable, "-X", "utf8", "-s", "-B"]
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


def launch_problem(model: str) -> str | None:
    """Return why a model may not be launched, or None. Only models listed in the policy are allowed."""
    tiers = json.loads(POLICY.read_text(encoding="utf-8"))["tiers"]
    if not any(model.casefold() in map(str.casefold, models) for models in tiers.values()):
        return f"model {model!r} is not listed in {POLICY}"
    return None


def top_tier_problem(model: str, effort: str) -> str | None:
    """Top-tier models are reserved for re-runs at medium effort."""
    top = json.loads(POLICY.read_text(encoding="utf-8"))["tiers"].get("top", [])
    if model.casefold() in map(str.casefold, top) and effort != "medium":
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


def dispatch(args: argparse.Namespace) -> int:
    handle = os.environ.get("ORCA_TERMINAL_HANDLE")
    if not handle:
        # Outside an Orca terminal, worker-start borrows the identity of whichever pane is active:
        # the worker starts in that pane's workspace and reports into that pane's Run.
        return fail("ORCA_TERMINAL_HANDLE is not set; run the master inside an Orca terminal")
    problem = launch_problem(args.model) or top_tier_problem(args.model, args.effort)
    if problem:
        return fail(problem)

    contract = str(Path(args.contract).resolve())
    check = [
        "check", "--runtime", args.agent, "--model", args.model, "--tier-policy", str(POLICY),
        "--contract", contract, "--agents", "1", "--completion-channel", "orchestration",
    ]
    if args.tier_override:
        check += ["--tier-override", args.tier_override]
    code, decision = gate(*check)
    if code != 0:
        print(json.dumps({"stage": "check", **decision}))
        return 2

    started_at = time.time()
    start = run([
        orca(), "orchestration", "worker-start", "--from", handle, "--spec-file", contract,
        "--agent", args.agent, "--model", args.model, "--effort", args.effort,
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
        "--declared-model", args.model,
        "--probe-cmd",
        subprocess.list2cmdline([orca(), "orchestration", "worker-show", "--dispatch", dispatch_id, "--json"]),
    ]
    if args.agent == "claude" and folder:
        # shortcut: only Claude workers are measured; Codex is recorded as MODEL_UNVERIFIED until a rollout probe is added.
        probe = [*PYTHON, str(Path(__file__).resolve()), "probe-model", folder, str(started_at)]
        register += ["--model-probe-cmd", subprocess.list2cmdline(probe)]
    code, registered = gate(*register)
    print(json.dumps({"stage": "register", "dispatch": dispatch_id, "task": task_id, "run": receipt.get("runId"), **registered}))
    if code != 0:
        return fail(f"register denied but worker {dispatch_id} is already running; stop it with `orca orchestration worker-stop`")
    return 0


def probe_model(args: argparse.Namespace) -> int:
    """Print the model of the newest Claude Code session started in a folder since a given time."""
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
                print(model)
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
        f"described in that skill, and report what you inherited before doing any other work."
    )
    launch = run([
        orca(), "agent", "launch", "--agent", "claude", "--model", args.model, "--effort", args.effort,
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

    def launch_options(sub: argparse.ArgumentParser) -> None:
        sub.add_argument("--model", required=True, help="provider model id listed in config/model-tier-policy.json")
        sub.add_argument("--effort", required=True, choices=("low", "medium", "high", "xhigh"))

    sub = commands.add_parser("dispatch", help="gate a contract, start one Orca worker, register it")
    sub.add_argument("--contract", required=True, help="contract file; it is also the worker's task spec")
    sub.add_argument("--agent", required=True, choices=("claude", "codex"))
    launch_options(sub)
    sub.add_argument("--worktree", default="current", help="Orca worktree selector for the worker (default: current)")
    sub.add_argument("--tier-override", metavar="REASON", help="pass one request over a tier fan-out cap")
    sub.set_defaults(handler=dispatch)

    sub = commands.add_parser("succeed", help="launch a successor master from a handoff document")
    sub.add_argument("--handoff", required=True)
    launch_options(sub)
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
