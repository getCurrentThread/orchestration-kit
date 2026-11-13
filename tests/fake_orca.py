"""Stand-in for the Orca CLI in tests: logs every call and replays the JSON shapes of Orca 1.4.257.

`worktree current` was measured on 1.4.258.
"""

import json
import os
import re
import sys
import time
from pathlib import Path

arguments = sys.argv[1:]
with open(os.environ["FAKE_ORCA_LOG"], "a", encoding="utf-8") as log:
    log.write(json.dumps(arguments) + "\n")

command = " ".join(arguments[:2])
folder = os.environ["FAKE_ORCA_WORKTREE"]


def option(name):
    return arguments[arguments.index(name) + 1] if name in arguments else None


def frame():
    """Return what a test scripted Orca to show now. Every `status` call, this one included, moves one frame on."""
    if "FAKE_ORCA_FRAMES" not in os.environ:
        return {}
    frames = json.loads(Path(os.environ["FAKE_ORCA_FRAMES"]).read_text(encoding="utf-8"))
    calls = Path(os.environ["FAKE_ORCA_LOG"]).read_text(encoding="utf-8").splitlines()
    return frames[min(sum(json.loads(call)[0] == "status" for call in calls), len(frames)) - 1]


def worker(dispatch, activity, outcome="in_progress", step=None, retained=False):
    liveness = {"verdict": "unverifiable" if activity == "unknown" else "live"}
    action = {"kind": step, "argv": ["orchestration", f"worker-{step}", "--dispatch", dispatch]} if step else {"kind": "none", "argv": []}
    projection = {
        "dispatchId": dispatch, "outcome": outcome, "stage": {"activity": activity}, "liveness": liveness, "nextAction": action,
    }
    row = {"dispatchId": dispatch, "agentTerminalHandle": f"term_{dispatch}", "projection": projection}
    if retained:
        # 1.4.258, a settled worker in a worktree of its own: its terminal stays, and no next step is named.
        # The row says so. The projection has a `resource` too, which only says who owns it.
        projection["resource"] = {"state": "owned"}
        row["resource"] = {"ownershipState": "owned", "releaseState": "retained", "retainedReason": "user_requested"}
    return row


def rollout(name, cwd, model):
    """Write what a Codex session leaves: one file for the whole machine's sessions, each naming its folder."""
    day = Path.home() / ".codex" / "sessions" / "2026" / "01" / "01"
    day.mkdir(parents=True, exist_ok=True)
    records = [
        {"type": "session_meta", "payload": {"cwd": cwd}},
        {"type": "turn_context", "payload": {"cwd": cwd, "model": model, "effort": "low"}},
    ]
    (day / f"rollout-{name}.jsonl").write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")


if command == "orchestration worker-start":
    # A real worker session writes its transcript as soon as it answers.
    model = os.environ["FAKE_ORCA_WORKER_MODEL"]
    if option("--agent") == "codex":
        rollout("worker", folder.replace("/", "\\"), model)  # Codex writes the folder with backslashes
        rollout("elsewhere", "C:\\elsewhere", "another-model")  # a newer session in another folder
    else:
        transcripts = Path.home() / ".claude" / "projects" / re.sub(r"[^A-Za-z0-9]", "-", folder)
        transcripts.mkdir(parents=True, exist_ok=True)
        entry = {"type": "assistant", "isSidechain": False, "message": {"model": model}}
        (transcripts / "worker.jsonl").write_text(json.dumps(entry) + "\n", encoding="utf-8")
    name = os.environ.get("FAKE_ORCA_DISPATCH", "test")  # a test that starts a second worker names it
    result = {
        "state": "ready",
        "runId": "run_test",
        "taskId": f"task_{name}",
        "dispatchId": f"ctx_{name}",
        "effects": [{"kind": "worktree", "action": "reused", "id": f"00000000::{folder}"}],
    }
elif command == "orchestration worker-show":
    dispatch = option("--dispatch")
    outcome = os.environ.get("FAKE_ORCA_OUTCOME", "in_progress")
    if outcome == "no answer":  # not "": Windows drops a variable that is set to nothing
        sys.exit(1)
    result = {
        "dispatch": {"id": "ctx_test", "taskId": "task_test", "status": "dispatched"},
        "projection": {"dispatchId": dispatch, "outcome": outcome},
        # Orca leaves the field out when it did not look, and gives null when it looked and found no wait.
        "observation": {"agentWait": {"evidence": "prompt-text"} if dispatch in frame().get("parked", []) else None},
    }
elif command == "orchestration dispatch-show":
    result = {"dispatch": {"id": "ctx_test", "task_id": "task_test", "status": "dispatched"}}
elif command == "agent launch":
    result = {"outcome": {"kind": "terminal"}}
elif command == "worktree current":
    if os.environ.get("FAKE_ORCA_CURRENT") == "not json":  # a CLI that answers this call in words
        print("error: unknown command 'current'", file=sys.stderr)
        sys.exit(1)
    if os.environ.get("FAKE_ORCA_CURRENT") == "none":  # what Orca says in a folder that none of its workspaces holds
        message = f"No Orca workspace contains the current directory: {Path.cwd()}"
        print(json.dumps({"ok": False, "error": {"code": "selector_not_found", "message": message}}))
        sys.exit(1)
    result = {"worktree": {"path": folder, "isMainWorktree": True}}
elif command in ("repo list", "repo add"):
    if os.environ.get("FAKE_ORCA_REPOS") == "no answer":
        sys.exit(1)
    # Orca keeps its projects between calls. A test may plant one, written as Orca would have been given it.
    kept = Path(os.environ["FAKE_ORCA_LOG"] + ".repos")
    repos = json.loads(kept.read_text(encoding="utf-8")) if kept.exists() else []
    if command == "repo list":
        result = {"repos": repos}
    else:
        path = option("--path")
        if option("--kind") != "folder" and not Path(path, ".git").exists():
            print(json.dumps({"ok": False, "error": {"code": "runtime_error", "message": f"Not a valid git repository: {path}"}}))
            sys.exit(1)
        # Orca stores a Git project's path with forward slashes, and answers a second add of one path with its record.
        repo = {"id": "repo_test", "path": path.replace("\\", "/"), "displayName": Path(path).name, "kind": option("--kind") or "git"}
        if repo not in repos:
            kept.write_text(json.dumps(repos + [repo]), encoding="utf-8")
        result = {"repo": repo}
elif command == "terminal list":
    handles = os.environ.get("FAKE_ORCA_TERMINALS", "term_new").split(",")
    result = {"terminals": [{"handle": handle} for handle in handles], "totalCount": len(handles), "truncated": False}
elif arguments[0] == "status":
    runtime = frame()["runtime"]
    result = {"app": {"running": bool(runtime)}, "runtime": {"state": "ready", "runtimeId": runtime} if runtime else {"state": "not_running"}}
elif command in ("orchestration worker-list", "orchestration inbox") and frame().get("slow"):
    sys.exit(1)  # no answer, as from a call that ran into its time limit
elif command == "orchestration worker-list" and frame().get("refuses"):
    print(json.dumps({"ok": False, "error": {"code": "invalid_argument"}}))
    sys.exit(1)
elif command == "orchestration worker-list" and frame().get("not_json"):
    print("error: unknown option '--full'", file=sys.stderr)  # a CLI that no longer takes what the watch passes
    sys.exit(1)
elif command == "orchestration worker-list" and frame().get("renamed"):
    result = {"dispatches": [worker("ctx_test", "working")]}  # the rows are there, under a key the watch does not read
elif command == "orchestration worker-list" and frame().get("unknown_run"):
    # What Orca says of a Run it does not know.
    result = {"workers": [], "page": {"limit": 100, "total": 0, "hasMore": False, "nextCursor": None}, "scope": {"run": option("--run"), "source": "flag"}}
elif command == "orchestration worker-list" and option("--cursor"):
    result = {"workers": [worker(*item) for item in frame()["older"].items()], "page": {"hasMore": False}}
elif command == "orchestration worker-list":
    # A settled worker is always listed too: a Run keeps the ones that have reported.
    settled = [worker("ctx_settled", "unknown", "succeeded")] + [worker(done, "unknown", "failed", "release") for done in frame().get("release", [])]
    settled += [worker(kept, "unknown", "succeeded", retained=True) for kept in frame().get("retained", [])]
    outcomes = frame().get("outcomes", {})
    result = {
        "workers": [worker(name, activity, outcomes.get(name, "in_progress")) for name, activity in frame()["workers"].items()] + settled,
        "page": {"hasMore": True, "nextCursor": "page-two"} if frame().get("older") else {"hasMore": False},
    }
elif command == "terminal show":
        # A terminal that keeps printing has always printed after the watch last looked: an hour ahead stands for that.
        printing = arguments[3].removeprefix("term_") in frame().get("printing", [])
        result = {"terminal": {"handle": arguments[3], "lastOutputAt": (time.time() + 3600) * 1000 if printing else None}}
elif command == "orchestration inbox":
    result = {"messages": [{"id": "msg_read", "read": 1}] + [{"id": f"msg_{number}", "read": 0} for number in range(frame()["unread"])]}
else:
    print(json.dumps({"ok": False, "error": {"code": "unexpected_command"}}))
    sys.exit(1)
print(json.dumps({"ok": True, "result": result}))
