"""Stand-in for the Orca CLI in tests: logs every call and replays the JSON shapes of Orca 1.4.257.

`worktree current`, `agent launch` and the two answers of `status` while the app is closed were measured on 1.4.258.
`worker-show`'s `result.worker.worktreeId`, `result.worker.startOptions.agent` and `result.dispatch.dispatchedAt`
(UTC, "YYYY-MM-DD HH:MM:SS") were measured on 1.4.259.

Measured on 1.4.259 on 2026-11-15, for `resume`: `worker-show` of a worker whose agent ended with its terminal kept
(`projection.liveness` {verdict: unverifiable, reason: missing_status}, `worker.agentTerminalHandle`, `dispatch.runId`) and of
a healthy one (verdict live); `worker-show` of an id that never existed (ok false, error.code dispatch_not_found, exit 1);
`terminal read` (`result.terminal`: handle, status, tail as a list of lines), of an ended agent (Claude's resume line and a shell
prompt end the tail) and of a released terminal (status exited, empty tail); `terminal send` (`result.send.accepted`) and, to a
closed terminal, ok false with error.code terminal_not_writable; `terminal wait --for tui-idle` (`result.wait.satisfied`) and, on
timeout, ok false with error.code timeout; `orchestration send --to dispatch:<id>` (`result.messageId`).

Measured on 1.4.259 on 2026-11-16, for `inherit`: `terminal close --terminal <handle> --json` of a terminal that holds an idle
Claude session (exit 0, `result.close` {handle, tabId, ptyKilled: true}), and of a handle Orca does not know (exit 1, ok false,
error.code terminal_handle_stale). The fake answers the first for a handle it lists, the second for any other, and leaves a
closed handle out of every later `terminal list` of the test. Three plain shell terminals, each closed and then listed again and
again: the list asked about 0.4 s after the close had answered still held the handle in 3 of 3 runs; it was gone 0.8 s after
the answer in two runs and 1.2 s after it in the third.
  FAKE_ORCA_CLOSE_LAG=<n>            a closed handle stays in the next <n> `terminal list` answers, then is left out: stands for
                                     the measured delay (about a second), counted in lists instead of time.

Switches that were NOT measured against Orca. Each makes the fake answer in a way a test needs and stands for a doubt the
wrapper must survive, not for an answer Orca was seen to give:
  FAKE_ORCA_CLOSE=stale              `terminal close` of a listed handle answers terminal_handle_stale, as Orca did for an unknown
                                     one (measured): stands for a terminal that was gone between the list and the close.
  FAKE_ORCA_CLOSE=not carried out    `terminal close` answers ok as measured and closes nothing, the handle stays listed: stands
                                     for a close that Orca accepts and does not carry out. Not measured.
  FAKE_ORCA_CLOSE=odd answer         `terminal close` exits 0 with ok true and a result without `close`: stands for a shape this
                                     wrapper does not read. Not measured.
  FAKE_ORCA_START=exit 1             `worker-start` prints an ok-false answer and exits 1 before it starts anything: stands for
                                     any refusal, with no worker.
  FAKE_ORCA_START=no dispatchId      `worker-start` exits 0 with a receipt that lacks `dispatchId`, the worker started: stands for
                                     a receipt of a shape this wrapper does not read.
  FAKE_ORCA_START=effects without id `worker-start` exits 0 with an `effects` entry that has no `id`; "effects without kind"
                                     the same with no `kind`; "no effects" without `effects`: stand for a receipt of a
                                     shape this wrapper does not read.
  FAKE_ORCA_DECOY=<model>            after the worker's session file, a newer one in the same folder on that model whose first
                                     prompt does not name the dispatch: stands for a second worker there, or the reflector
                                     session that the owner's autoharness starts when a session ends.
  FAKE_ORCA_LAUNCH=no terminal       `agent launch` exits 0 and names no terminal (outcome kind `chat`).
  FAKE_ORCA_LAUNCH=not delivered     `agent launch` names a terminal and gives the prompt another outcome (`rejected`): stand for
                                     the two doubts the wrapper's launch check is written for.
"""

import json
import os
import re
import sys
import time
import uuid
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
    if os.environ.get("FAKE_ORCA_START") == "exit 1":
        print(json.dumps({"ok": False, "error": {"code": "runtime_error", "message": "worker-start refused"}}))
        sys.exit(1)
    # A real worker session writes its transcript as soon as it answers.
    model = os.environ["FAKE_ORCA_WORKER_MODEL"]
    if option("--agent") == "codex":
        rollout("worker", folder.replace("/", "\\"), model)  # Codex writes the folder with backslashes
        rollout("elsewhere", "C:\\elsewhere", "another-model")  # a newer session in another folder
    else:
        transcripts = Path.home() / ".claude" / "projects" / re.sub(r"[^A-Za-z0-9]", "-", folder)
        transcripts.mkdir(parents=True, exist_ok=True)
        name = os.environ.get("FAKE_ORCA_DISPATCH", "test")  # a test that starts a second worker names it
        Path(os.environ["FAKE_ORCA_LOG"] + ".dispatched").write_text(time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()), encoding="utf-8")
        prompt = {"type": "user", "isSidechain": False, "message": {"role": "user", "content": f"You are a dispatched worker. Your Dispatch ID is ctx_{name}"}}
        usage = {"input_tokens": 1000, "cache_creation_input_tokens": 200, "cache_read_input_tokens": 700, "output_tokens": 300}
        entry = {"type": "assistant", "isSidechain": False, "message": {"id": "msg_1", "model": model, "usage": usage}}
        # A file of its own per worker, named by its session id as Claude Code names it: the wrapper reads the one born after the dispatch.
        session = str(uuid.uuid4())
        Path(os.environ["FAKE_ORCA_LOG"] + ".session").write_text(session, encoding="utf-8")
        (transcripts / f"{session}.jsonl").write_text(json.dumps(prompt) + "\n" + json.dumps(entry) + "\n", encoding="utf-8")
        if "FAKE_ORCA_DECOY" in os.environ:
            time.sleep(0.05)  # born after the worker's file, so that it is the newest
            other = {"type": "user", "isSidechain": False, "message": {"role": "user", "content": "A session that is not this worker's"}}
            reply = {"type": "assistant", "isSidechain": False, "message": {"id": "msg_2", "model": os.environ["FAKE_ORCA_DECOY"], "usage": usage}}
            (transcripts / f"{uuid.uuid4()}.jsonl").write_text(json.dumps(other) + "\n" + json.dumps(reply) + "\n", encoding="utf-8")
    name = os.environ.get("FAKE_ORCA_DISPATCH", "test")
    result = {
        "state": "ready",
        "runId": "run_test",
        "taskId": f"task_{name}",
        "dispatchId": f"ctx_{name}",
        "effects": [{"kind": "worktree", "action": "reused", "id": f"00000000::{folder}"}],
    }
    if os.environ.get("FAKE_ORCA_START") == "no dispatchId":
        del result["dispatchId"]
    if os.environ.get("FAKE_ORCA_START") == "effects without id":
        del result["effects"][0]["id"]
    if os.environ.get("FAKE_ORCA_START") == "effects without kind":
        del result["effects"][0]["kind"]
    if os.environ.get("FAKE_ORCA_START") == "no effects":
        del result["effects"]
elif command == "orchestration worker-show":
    dispatch = option("--dispatch")
    outcome = os.environ.get("FAKE_ORCA_OUTCOME", "in_progress")
    if outcome == "no answer":  # not "": Windows drops a variable that is set to nothing
        sys.exit(1)
    # An id that Orca never had, or has lost to a reset; FAKE_ORCA_FRESH names a dispatch started after it, which Orca knows.
    if outcome == "unknown dispatch" and dispatch != "ctx_" + os.environ.get("FAKE_ORCA_FRESH", ""):
        error = {"code": "dispatch_not_found", "message": f"Worker Dispatch {dispatch} was not found."}
        print(json.dumps({"ok": False, "error": error}))
        sys.exit(1)
    if outcome == "other refusal":  # an answer that is neither a dispatch nor dispatch_not_found
        print(json.dumps({"ok": False, "error": {"code": "invalid_argument", "message": "no"}}))
        sys.exit(1)
    dispatched = Path(os.environ["FAKE_ORCA_LOG"] + ".dispatched")
    result = {
        "dispatch": {"id": "ctx_test", "taskId": "task_test", "runId": "run_test", "status": "dispatched", "dispatchedAt": dispatched.read_text(encoding="utf-8") if dispatched.exists() else "2000-01-01 00:00:00"},
        "worker": {
            "dispatchId": dispatch, "worktreeId": f"00000000::{folder}", "agentTerminalHandle": f"term_worker_{dispatch}",
            "startOptions": {"agent": os.environ.get("FAKE_ORCA_AGENT", "claude")},
        },
        "projection": {"dispatchId": dispatch, "outcome": outcome, "liveness": {"verdict": os.environ.get("FAKE_ORCA_LIVENESS", "live")}},
        # Orca leaves the field out when it did not look, and gives null when it looked and found no wait. The
        # shape of a wait was measured on Orca 1.4.259 on 2026-11-16, from a Claude worker that waited on an
        # AskUserQuestion prompt: how Orca knows and since when (ms), and nothing of what the prompt asks.
        "observation": {"agentWait": {"source": "hook", "since": 1794824861316} if dispatch in frame().get("parked", []) else None},
    }
    for path in filter(None, os.environ.get("FAKE_ORCA_WITHOUT", "").split(",")):  # dotted paths a test takes out
        *parents, last = path.split(".")
        holder = result
        for key in parents:
            holder = holder[key]
        del holder[last]
elif command == "orchestration dispatch-show":
    result = {"dispatch": {"id": "ctx_test", "task_id": "task_test", "status": "dispatched"}}
elif command == "agent launch":
    result = {
        "outcome": {"kind": "terminal", "handle": "term_launched"},
        "worktreeId": f"00000000::{folder}",
        "prompt": {"delivery": "submit", "outcome": "handed-to-terminal"},
    }
    if os.environ.get("FAKE_ORCA_LAUNCH") == "no terminal":
        result["outcome"] = {"kind": "chat"}
    if os.environ.get("FAKE_ORCA_LAUNCH") == "not delivered":
        result["prompt"]["outcome"] = "rejected"
elif arguments[0] == "open":
    result = {}  # the wrapper starts the app with it and reads nothing of what it prints
elif command == "worktree current":
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
elif command == "terminal read":
    # An ended agent: Claude's line on exit, with the session id of the worker the fake last started, and a prompt.
    if os.environ.get("FAKE_ORCA_READ") == "no answer":
        sys.exit(1)
    kept = Path(os.environ["FAKE_ORCA_LOG"] + ".session")  # a watch test starts no worker
    session = kept.read_text(encoding="utf-8") if kept.exists() else str(uuid.uuid4())
    ended = [
        "This session resumes only with the environment variable CLAUDE_CODE_EXPERIMENTAL_CODE_MODE equal to 1",
        "Resume this session with:",
        "claude --dangerously-skip-permissions --model claude-haiku-5-5 --effort low --resume {session}",
        f"PS {folder}>",
    ]
    tail = json.loads(os.environ["FAKE_ORCA_TAIL"]) if "FAKE_ORCA_TAIL" in os.environ else ended
    tail = [line.replace("{session}", session) for line in tail]
    result = {"terminal": {
        "handle": option("--terminal"), "status": "running", "tail": tail, "truncated": False, "limited": False,
        "oldestCursor": "0", "nextCursor": str(len(tail)), "latestCursor": str(len(tail)), "returnedLineCount": len(tail), "source": "stream",
    }}
elif command == "terminal send":
    if os.environ.get("FAKE_ORCA_SEND") == "refused":  # to a closed terminal, as measured
        print(json.dumps({"ok": False, "error": {"code": "terminal_not_writable", "message": "terminal_not_writable"}}))
        sys.exit(1)
    prompt = {"stages": ["input_accepted"], "provider": "claude", "observation": "supported"}
    result = {"send": {"handle": option("--terminal"), "accepted": True, "bytesWritten": len(option("--text")) + 1, "prompt": prompt}, "mutation": {"replayed": False}}
elif command == "terminal wait":
    if os.environ.get("FAKE_ORCA_IDLE") == "never":  # what a wait that ran out of time answers, as measured
        print(json.dumps({"ok": False, "error": {"code": "timeout", "message": "timeout"}}))
        sys.exit(1)
    result = {"wait": {"handle": option("--terminal"), "condition": option("--for"), "satisfied": True, "status": "running", "exitCode": None}}
elif command == "orchestration send":
    result = {"messageId": "msg_test"}
elif command == "terminal list":
    closed = Path(os.environ["FAKE_ORCA_LOG"] + ".closed")
    gone = closed.read_text(encoding="utf-8").split() if closed.exists() else []
    lag = Path(os.environ["FAKE_ORCA_LOG"] + ".lag")
    left = int(lag.read_text(encoding="utf-8")) if lag.exists() else 0
    if left:  # the measured delay: the lists right after a close still hold the handle
        lag.write_text(str(left - 1), encoding="utf-8")
        gone = []
    handles = [handle for handle in os.environ.get("FAKE_ORCA_TERMINALS", "term_new").split(",") if handle not in gone]
    result = {"terminals": [{"handle": handle} for handle in handles], "totalCount": len(handles), "truncated": False}
elif command == "terminal close":
    handle = option("--terminal")
    closed = Path(os.environ["FAKE_ORCA_LOG"] + ".closed")
    gone = closed.read_text(encoding="utf-8").split() if closed.exists() else []
    if handle not in os.environ.get("FAKE_ORCA_TERMINALS", "term_new").split(",") or handle in gone or os.environ.get("FAKE_ORCA_CLOSE") == "stale":
        error = {"code": "terminal_handle_stale", "message": "terminal_handle_stale", "data": {"nextSteps": ["List the terminals again."]}}
        print(json.dumps({"id": str(uuid.uuid4()), "ok": False, "error": error, "_meta": {"runtimeId": str(uuid.uuid4())}}))
        sys.exit(1)
    if os.environ.get("FAKE_ORCA_CLOSE") != "not carried out":
        closed.write_text(" ".join(gone + [handle]), encoding="utf-8")
        Path(os.environ["FAKE_ORCA_LOG"] + ".lag").write_text(os.environ.get("FAKE_ORCA_CLOSE_LAG", "0"), encoding="utf-8")
    result = {"close": {"handle": handle, "tabId": str(uuid.uuid4()), "ptyKilled": True}}
    if os.environ.get("FAKE_ORCA_CLOSE") == "odd answer":
        result = {}
elif arguments[0] == "status":
    runtime = frame()["runtime"]
    if frame().get("stale"):  # no app runs, and Orca's CLI still tries to connect: found so once, the cause not looked into
        error = {"code": "runtime_unavailable", "message": "This terminal could not connect to Orca."}
        print(json.dumps({"id": "local", "ok": False, "error": error, "_meta": {"runtimeId": None}}))
        sys.exit(1)
    closed = {"state": "not_running", "reachable": False, "runtimeId": None}  # after Orca was closed by its window
    result = {"app": {"running": bool(runtime)}, "runtime": {"state": "ready", "runtimeId": runtime} if runtime else closed}
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
