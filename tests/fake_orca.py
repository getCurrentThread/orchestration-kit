"""Stand-in for the Orca CLI in tests: logs every call and replays the JSON shapes of Orca 1.4.257."""

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


def frame():
    """Return what a test scripted Orca to show now. Every `status` call, this one included, moves one frame on."""
    frames = json.loads(Path(os.environ["FAKE_ORCA_FRAMES"]).read_text(encoding="utf-8"))
    calls = Path(os.environ["FAKE_ORCA_LOG"]).read_text(encoding="utf-8").splitlines()
    return frames[min(sum(json.loads(call)[0] == "status" for call in calls), len(frames)) - 1]


def worker(dispatch, activity, outcome="in_progress"):
    liveness = {"verdict": "unverifiable" if activity == "unknown" else "live"}
    projection = {"dispatchId": dispatch, "outcome": outcome, "stage": {"activity": activity}, "liveness": liveness}
    return {"dispatchId": dispatch, "agentTerminalHandle": f"term_{dispatch}", "projection": projection}


if command == "orchestration worker-start":
    # A real worker session writes its transcript as soon as it answers.
    transcripts = Path.home() / ".claude" / "projects" / re.sub(r"[^A-Za-z0-9]", "-", folder)
    transcripts.mkdir(parents=True, exist_ok=True)
    entry = {"type": "assistant", "isSidechain": False, "message": {"model": os.environ["FAKE_ORCA_WORKER_MODEL"]}}
    (transcripts / "worker.jsonl").write_text(json.dumps(entry) + "\n", encoding="utf-8")
    result = {
        "state": "ready",
        "runId": "run_test",
        "taskId": "task_test",
        "dispatchId": "ctx_test",
        "effects": [{"kind": "worktree", "action": "reused", "id": f"00000000::{folder}"}],
    }
elif command == "orchestration worker-show":
    result = {"dispatch": {"id": "ctx_test", "taskId": "task_test", "status": "dispatched"}}
elif command == "orchestration dispatch-show":
    result = {"dispatch": {"id": "ctx_test", "task_id": "task_test", "status": "dispatched"}}
elif command == "agent launch":
    result = {"outcome": {"kind": "terminal"}}
elif arguments[0] == "status":
    runtime = frame()["runtime"]
    result = {"app": {"running": bool(runtime)}, "runtime": {"state": "ready", "runtimeId": runtime} if runtime else {"state": "not_running"}}
elif command in ("orchestration worker-list", "orchestration inbox") and frame().get("slow"):
    sys.exit(1)  # no answer, as from a call that ran into its time limit
elif command == "orchestration worker-list" and frame().get("refuses"):
    print(json.dumps({"ok": False, "error": {"code": "invalid_argument"}}))
    sys.exit(1)
elif command == "orchestration worker-list":
    # A settled worker is always listed too: a Run keeps the ones that have reported.
    result = {"workers": [worker(*item) for item in frame()["workers"].items()] + [worker("ctx_settled", "unknown", "succeeded")]}
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
