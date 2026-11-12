"""Stand-in for the Orca CLI in tests: logs every call and replays the JSON shapes of Orca 1.4.256."""

import json
import os
import re
import sys
from pathlib import Path

arguments = sys.argv[1:]
with open(os.environ["FAKE_ORCA_LOG"], "a", encoding="utf-8") as log:
    log.write(json.dumps(arguments) + "\n")

command = " ".join(arguments[:2])
folder = os.environ["FAKE_ORCA_WORKTREE"]
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
else:
    print(json.dumps({"ok": False, "error": {"code": "unexpected_command"}}))
    sys.exit(1)
print(json.dumps({"ok": True, "result": result}))
