"""End-to-end checks of scripts/orchestration.py: the real vendored gate against a fake Orca CLI.

Run from the repository root: python -X utf8 -s -B -m unittest discover -s tests -v
"""

import contextlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import harness  # noqa: E402
import orchestration  # noqa: E402

# Families come from the policy, so the tests name no model and survive a change of the families listed there.
POLICY_TIERS = json.loads(orchestration.POLICY.read_text(encoding="utf-8"))["tiers"]
EFFICIENT, TOP = POLICY_TIERS["efficient"][0], POLICY_TIERS["top"][0]


def versioned(family):
    """A full model id of a family, as a worker transcript reports it. The version is made up on purpose."""
    return f"claude-{family}-9-9"


def tree(folder):
    """Every file of a project with its bytes, git's own folder aside, to prove that a run left it alone."""
    files = (path for path in folder.rglob("*") if path.is_file() and path.relative_to(folder).parts[0] != ".git")
    return {path.relative_to(folder).as_posix(): path.read_bytes() for path in files}


LINEAGE_ARGUMENTS = [
    "--verification", "PASS", "--parent-session", "term_old", "--successor-session", "term_new",
    "--inherited-role", "Maintenance", "--succession-reason", "owner request",
    "--inherited-open-tracks", "none", "--context-loss-summary", "none",
]


class OrchestrationTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(temporary.cleanup)
        base = Path(temporary.name).resolve()
        self.work = base / "work"
        self.work.mkdir()
        (base / "home").mkdir()
        self.log = base / "orca-calls.jsonl"
        environment = {
            # Without the perl folders of Git for Windows, as on a GitHub runner: see test_harness.BARE_PATH.
            "PATH": os.pathsep.join(
                entry for entry in os.environ["PATH"].split(os.pathsep) if not entry.rstrip("\\/").lower().endswith("_perl")
            ),
            "USERPROFILE": str(base / "home"),
            "ORCA_CLI_COMMAND": str(ROOT / "tests" / "fake-orca.cmd"),
            "CLAUDE_CLI_COMMAND": str(ROOT / "tests" / "fake-claude.cmd"),
            "ORCA_TERMINAL_HANDLE": "term_test",
            "FAKE_ORCA_LOG": str(self.log),
            "FAKE_ORCA_WORKTREE": self.work.as_posix(),
            "FAKE_ORCA_WORKER_MODEL": versioned(EFFICIENT),
        }
        patcher = mock.patch.dict(os.environ, environment)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(os.chdir, Path.cwd())
        os.chdir(self.work)

    def contract(self, tier="efficient", runtime="claude", front=""):
        path = self.work / "contract.md"
        text = f"---\ntier: {tier}\nruntime: {runtime}\nrepo: demo\n{front}---\n\n# Say READY\n\nReply READY.\n"
        path.write_text(text, encoding="utf-8")
        return str(path)

    def cli(self, *arguments):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            try:
                code = orchestration.main(list(arguments))
            except SystemExit as stop:  # argparse rejects bad options this way
                code = stop.code
        return code, output.getvalue()

    def dispatch(self, *place, model=EFFICIENT, effort="low", tier="efficient", agent="claude", front=""):
        launch = ["--model", model] if model else []
        return self.cli("dispatch", "--contract", self.contract(tier, agent, front), "--agent", agent, *launch, "--effort", effort, *place)

    def ready(self, name):
        """A project that already carries a harness whose setup has run."""
        project = self.work.parent / name
        (project / ".agent").mkdir(parents=True)
        (project / ".agent" / "Instructions.md").write_text("rules\n", encoding="utf-8")
        (project / ".setup-done").touch()
        return project

    def started_model(self):
        start = self.worker_starts()[-1]
        return start[start.index("--model") + 1]

    def orca_calls(self):
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines()]

    def worker_starts(self):
        return [call for call in self.orca_calls() if call[:2] == ["orchestration", "worker-start"]]

    def sends(self):
        """The calls that reach a worker: text typed into a terminal, a wait on it, and mail to a dispatch."""
        return [call for call in self.orca_calls() if call[:2] in (["terminal", "send"], ["terminal", "wait"], ["orchestration", "send"])]

    def ledger(self):
        path = self.work / ".orchestration" / "dispatch-ledger.jsonl"
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []

    def test_allowed_dispatch_starts_one_worker_and_verifies_its_model(self):
        code, output = self.dispatch()
        self.assertEqual(code, 0, output)
        starts = self.worker_starts()
        self.assertEqual(len(starts), 1)
        self.assertEqual(starts[0][starts[0].index("--from") + 1], "term_test")
        # Without a title Orca names the Task and its Run after the spec's first line, the front matter's "---".
        self.assertEqual(starts[0][starts[0].index("--task-title") + 1], "Say READY")
        self.assertIn("watch --run run_test", json.loads(output)["next"])
        rows = self.ledger()
        self.assertEqual([row["decision"] for row in rows], ["ALLOW", "ALLOW"])
        self.assertEqual(rows[1]["job_id"], "ctx_test")
        self.assertEqual(self.started_model(), versioned(EFFICIENT))  # Orca gets the version the CLI resolves the family to
        self.assertEqual(rows[1]["model_measured"], EFFICIENT)  # the worker's versioned id, reduced to its family
        self.assertTrue(rows[1]["model_verified"])

    def test_a_full_model_id_pins_its_version_and_is_gated_as_its_family(self):
        pinned = f"claude-{EFFICIENT}-1-1"
        code, output = self.dispatch(model=pinned)
        self.assertEqual(code, 0, output)
        self.assertEqual(self.started_model(), pinned)  # a full id pins the version Orca launches
        self.assertEqual(self.ledger()[0]["model"], EFFICIENT)
        os.environ["CLAUDE_CLI_COMMAND"] = "no-such-claude"
        self.assertEqual(orchestration.launch_model("claude", EFFICIENT), EFFICIENT)  # no answer from the CLI: as typed

    def test_codex_takes_its_model_from_the_codex_config(self):
        self.assertEqual(self.dispatch(model=None, agent="codex")[0], 2)  # no Codex config yet
        self.assertEqual(self.orca_calls(), [])
        config = Path(os.environ["USERPROFILE"]) / ".codex" / "config.toml"
        config.parent.mkdir()
        config.write_text(f'model = "{EFFICIENT}-preview"\n', encoding="utf-8")
        code, output = self.dispatch(model=None, agent="codex")
        self.assertEqual(code, 0, output)
        self.assertEqual(self.started_model(), f"{EFFICIENT}-preview")
        # Measured from the rollout of the session in the worker's folder, not from the newer one elsewhere.
        self.assertEqual(self.ledger()[1]["model_measured"], EFFICIENT)
        self.assertTrue(self.ledger()[1]["model_verified"])

    def test_a_contract_that_still_runs_is_not_dispatched_again(self):
        self.assertEqual(self.dispatch()[0], 0)
        code, output = self.dispatch()  # after an Orca restart the report is missing, not the work
        self.assertEqual(code, 2)
        self.assertIn("CONTRACT_ALREADY_RUNNING", output)
        self.assertIn("dispatch:ctx_test", output)
        self.assertEqual(len(self.worker_starts()), 1)
        self.assertEqual(len(self.ledger()), 2)  # refused before the gate, whose check counts towards a tier's cap
        for unknown in ("paused", "no answer"):  # an outcome this code does not know, and none at all: neither clears it
            os.environ["FAKE_ORCA_OUTCOME"] = unknown
            code, output = self.dispatch()
            self.assertEqual(code, 2)
            self.assertIn("Orca does not show that dispatch as settled", output)
        self.assertEqual(len(self.worker_starts()), 1)
        os.environ["FAKE_ORCA_OUTCOME"] = "failed"  # a settled dispatch does not hold its contract
        code, output = self.dispatch()
        self.assertEqual(code, 0, output)
        self.assertEqual(len(self.worker_starts()), 2)

    def test_a_retry_or_a_fix_is_one_more_attempt_at_the_task_it_follows(self):
        self.assertEqual(self.dispatch()[0], 0)
        fix = self.work / "contract-2.md"  # a new file, which the gate counts as a contract of its own
        fix.write_text(Path(self.contract()).read_text(encoding="utf-8") + "\nFix what the first attempt left.\n", encoding="utf-8")

        def follow(earlier, name):
            os.environ["FAKE_ORCA_DISPATCH"] = name
            return self.cli("dispatch", "--contract", str(fix), "--agent", "claude", "--model", EFFICIENT, "--effort", "low", "--follows", earlier)

        code, output = follow("ctx_unknown", "fix")
        self.assertEqual(code, 2)
        self.assertIn("does not hold as a registered dispatch", output)
        code, output = follow("ctx_test", "fix")  # the first attempt has not reported: two workers would be on one task
        self.assertEqual(code, 2)
        self.assertIn("TASK_ALREADY_RUNNING", output)
        self.assertEqual((len(self.worker_starts()), len(self.ledger())), (1, 2))  # refused before the gate
        os.environ["FAKE_ORCA_OUTCOME"] = "succeeded"  # it reported, and its result was rejected
        code, output = follow("ctx_test", "fix")
        self.assertEqual(code, 0, output)
        line = json.loads(output)
        self.assertEqual((line["dispatch"], line["origin"], line["task_attempt"]), ("ctx_fix", "task_test", 2))
        # The ledger holds both under the first dispatch's task. The gate's own `attempt` counts lines of one file.
        registered = [(row["job_id"], row["card"]) for row in self.ledger() if row.get("job_id")]
        self.assertEqual(registered, [("ctx_test", "task_test"), ("ctx_fix", "task_test")])
        self.assertEqual(orchestration.task_of({"orchestration_task": "task_old"}), "task_old")  # a row of an earlier version
        code, output = follow("ctx_fix", "third")  # any attempt of the task names it
        self.assertEqual((code, json.loads(output)["task_attempt"]), (0, 3), output)
        code, output = follow("ctx_third", "fourth")
        self.assertEqual(code, 2)
        self.assertIn("TASK_ATTEMPTS_EXHAUSTED", output)
        self.assertIn("tell the owner", output)
        self.assertEqual(len(self.worker_starts()), 3)
        report = self.cli("report")[1]
        self.assertIn(f"{EFFICIENT}: dispatches=", report)  # the gate's own summary, passed on
        self.assertIn("task_test: ctx_test, ctx_fix, ctx_third", report)

    def transcripts(self):
        """The folder the fake Orca's Claude worker writes its session files to."""
        return Path(os.environ["USERPROFILE"]) / ".claude" / "projects" / re.sub(r"[^A-Za-z0-9]", "-", self.work.as_posix())

    def token_lines(self, output):
        return [line for line in output.splitlines() if line.startswith("  ctx_") and "budget" in line]

    def test_a_declared_token_budget_reaches_register_and_a_contract_without_one_registers_null(self):
        code, output = self.dispatch(front="budget: [tokens=2000]\ntokens_basis: uncached\n")
        self.assertEqual(code, 0, output)
        line = json.loads(output)
        self.assertEqual((line["token_budget"], line["tokens_basis"]), (2000, "uncached"))
        row = self.ledger()[1]
        self.assertEqual((row["token_budget"], row["tokens_basis"], row["token_budget_source"]), (2000, "uncached", "declared"))
        # The gate validates the front matter; the wrapper passes on what the gate says.
        self.assertEqual(self.dispatch(front="budget: [tokens=0]\n")[0], 2)
        self.assertEqual(len(self.worker_starts()), 1)

    def test_a_contract_without_a_budget_registers_null(self):
        code, output = self.dispatch()
        self.assertEqual(code, 0, output)
        line = json.loads(output)
        self.assertEqual((line["token_budget"], line["tokens_basis"]), (None, "total"))
        self.assertIsNone(self.ledger()[1]["token_budget"])

    def test_a_settled_dispatch_is_measured_from_its_session_and_over_its_budget_is_marked(self):
        self.assertEqual(self.dispatch(front="budget: [tokens=2000]\n")[0], 0)
        # A later session in the folder that quotes the brief deep inside a long prompt (a digest) is not the worker's.
        digest = {"type": "user", "message": {"role": "user", "content": "x" * 20000 + " ctx_test"}}
        (self.transcripts() / "digest.jsonl").write_text(json.dumps(digest) + "\n", encoding="utf-8")
        os.environ["FAKE_ORCA_OUTCOME"] = "succeeded"
        output = self.cli("report")[1]
        self.assertEqual(self.token_lines(output), ["  ctx_test: budget 2000 total, usage 2200 total over"])
        self.assertEqual(self.ledger()[1]["token_usage"]["total"], 2200)
        shown = len([call for call in self.orca_calls() if call[:2] == ["orchestration", "worker-show"]])
        ledger = (self.work / ".orchestration" / "dispatch-ledger.jsonl").read_bytes()
        self.assertEqual(self.token_lines(self.cli("report")[1]), self.token_lines(output))  # nothing is settled again
        self.assertEqual((self.work / ".orchestration" / "dispatch-ledger.jsonl").read_bytes(), ledger)
        self.assertEqual(len([call for call in self.orca_calls() if call[:2] == ["orchestration", "worker-show"]]), shown)

    def test_usage_is_counted_on_the_budgets_basis_and_a_dispatch_without_a_budget_still_shows_it(self):
        self.assertEqual(self.dispatch(front="budget: [tokens=2000]\ntokens_basis: uncached\n")[0], 0)
        os.environ["FAKE_ORCA_OUTCOME"] = "failed"  # a failed dispatch is settled too
        self.assertEqual(self.token_lines(self.cli("report")[1]), ["  ctx_test: budget 2000 uncached, usage 1500 uncached"])
        os.environ["FAKE_ORCA_DISPATCH"] = "plain"
        self.assertEqual(self.dispatch()[0], 0)
        self.assertIn("  ctx_plain: budget unset, usage 2200 total", self.cli("report")[1])

    def test_a_dispatch_that_orca_shows_open_is_not_settled(self):
        self.assertEqual(self.dispatch()[0], 0)
        self.assertEqual(self.token_lines(self.cli("report")[1]), ["  ctx_test: budget unset, usage open"])  # listed, with no budget
        self.assertEqual(orchestration.token_line({"job_id": "ctx_a", "token_budget": 2000}, "open"), "  ctx_a: budget 2000 total, usage open")
        self.assertNotIn("token_usage", self.ledger()[1])
        os.environ["FAKE_ORCA_OUTCOME"] = "succeeded"
        self.assertIn("usage 2200 total", self.cli("report")[1])

    def test_a_dispatch_without_exactly_one_session_file_is_recorded_unmeasured(self):
        for count, name in ((0, "none"), (2, "twice")):
            os.environ["FAKE_ORCA_DISPATCH"] = name
            self.assertEqual(self.dispatch(front=f"budget: [tokens={2000 + count}]\n")[0], 0)  # a contract of its own
            mine = [path for path in self.transcripts().glob("*.jsonl") if f"ctx_{name}" in path.read_text(encoding="utf-8")]
            if count == 0:
                mine[0].unlink()
            else:
                shutil.copy(mine[0], mine[0].with_name("copy.jsonl"))
            os.environ["FAKE_ORCA_OUTCOME"] = "succeeded"
            output = self.cli("report")[1]
            self.assertIn(f"ctx_{name}: budget {2000 + count} total, usage unmeasured (claude: ", output)
            self.assertIn(f"{count} session files", output)

    def test_an_orca_answer_without_the_fields_settles_nothing(self):
        self.assertEqual(self.dispatch(front="budget: [tokens=2000]\n")[0], 0)
        answers = (
            ({"FAKE_ORCA_SHOW": "lacks", "FAKE_ORCA_OUTCOME": "succeeded"}, "lacks worker.worktreeId"),
            ({"FAKE_ORCA_OUTCOME": "no answer"}, "is missing or refused"),
        )
        for answer, said in answers:
            with mock.patch.dict(os.environ, answer):
                output = self.cli("report")[1]
            self.assertIn(f"ctx_test: not settled, Orca's answer {said}", output)
            self.assertIn("usage unsettled", output)
            self.assertNotIn("token_usage", self.ledger()[1])

    def test_report_says_when_the_orca_cli_cannot_be_started(self):
        self.assertEqual(self.dispatch()[0], 0)
        os.environ["ORCA_CLI_COMMAND"] = str(self.work / "no-such-orca.cmd")
        output = self.cli("report")[1]
        self.assertIn("ctx_test: not settled, the Orca CLI", output)
        self.assertIn("cannot be run", output)
        self.assertNotIn("is missing or refused", output)

    def test_report_skips_a_session_file_it_cannot_read_and_names_it(self):
        self.assertEqual(self.dispatch(front="budget: [tokens=2000]\n")[0], 0)
        for name in ("locked.jsonl", "gone.jsonl"):
            (self.transcripts() / name).write_text("{}\n", encoding="utf-8")
        names, stat = orchestration.names_dispatch, Path.stat

        def locked(session, dispatch_id):
            if session.name == "locked.jsonl":
                raise PermissionError(13, "Permission denied")
            return names(session, dispatch_id)

        def vanishing(path, **options):  # a file that is listed and then gone
            if path.name == "gone.jsonl":
                raise FileNotFoundError(2, "No such file or directory")
            return stat(path, **options)

        os.environ["FAKE_ORCA_OUTCOME"] = "succeeded"
        with mock.patch.object(orchestration, "names_dispatch", locked), mock.patch.object(Path, "stat", vanishing):
            output = self.cli("report")[1]
        self.assertIn("ctx_test: session file locked.jsonl: Permission denied skipped, it could not be read", output)
        self.assertIn("ctx_test: session file gone.jsonl: No such file or directory skipped, it could not be read", output)
        self.assertEqual(self.token_lines(output), ["  ctx_test: budget 2000 total, usage 2200 total over"])  # the worker's own file is measured

    def test_a_check_answer_without_front_matter_refuses_before_a_worker_starts(self):
        gate = orchestration.gate

        def without(*arguments):
            code, decision = gate(*arguments)
            if arguments[0] == "check":
                decision.pop("front_matter", None)
            return code, decision

        with mock.patch.object(orchestration, "gate", without):
            code, output = self.dispatch()
        self.assertEqual(code, 2, output)
        self.assertIn("FRONT_MATTER_MISSING", output)
        self.assertEqual(self.worker_starts(), [])
        self.assertFalse(any(row.get("job_id") for row in self.ledger()))  # nothing was registered

    def test_a_dispatch_that_orca_does_not_know_is_settled_once_as_unmeasured(self):
        self.assertEqual(self.dispatch(front="budget: [tokens=2000]\n")[0], 0)
        os.environ["FAKE_ORCA_OUTCOME"] = "unknown dispatch"  # a ledger older than an `orca orchestration reset`
        output = self.cli("report")[1]
        self.assertIn("ctx_test: Orca does not know this dispatch (dispatch_not_found)", output)
        self.assertEqual(self.ledger()[1]["token_usage"]["source"], "unmeasured")
        self.assertEqual(self.ledger()[1]["token_usage"]["why"].split(":")[0], "claude")  # the runtime the ledger holds
        self.assertIn("usage unmeasured", output)
        shown = len([call for call in self.orca_calls() if call[:2] == ["orchestration", "worker-show"]])
        self.assertNotIn("does not know", self.cli("report")[1])  # not printed for ever
        self.assertEqual(len([call for call in self.orca_calls() if call[:2] == ["orchestration", "worker-show"]]), shown)

    def test_a_new_worktree_in_the_masters_own_repository_starts_from_its_local_branch(self):
        self.git(self.work, "init", "-q")
        self.git(self.work, "commit", "-q", "--allow-empty", "-m", "a commit the remote does not have")
        code, output = self.dispatch("--worktree", "new-child")
        self.assertEqual(code, 0, output)
        start = self.worker_starts()[-1]
        # Orca bases a new worktree on the remote's branch unless it is told, also in the master's own repository.
        branch = self.git(self.work, "branch", "--show-current")
        self.assertEqual(start[start.index("--worktree"):start.index("--worktree") + 4], ["--worktree", "new-child", "--base-branch", branch])
        self.assertNotIn("--repo", start)  # the repository is the one of the terminal that asks
        os.environ["FAKE_ORCA_OUTCOME"] = "succeeded"  # the first dispatch of this contract has settled
        self.assertEqual(self.dispatch("--worktree", f"path:{self.work}")[0], 0)
        self.assertNotIn("--base-branch", self.worker_starts()[-1])  # Orca refuses a base for a worktree that exists

    def test_only_the_master_is_reminded_after_a_compaction(self):
        hook = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))["hooks"]["SessionStart"][0]
        self.assertEqual(hook["matcher"], "compact")
        self.assertIn('orchestration.py" compacted', hook["hooks"][0]["command"])
        self.assertEqual(self.cli("compacted"), (0, ""))  # a project that has never had a master
        self.assertFalse((self.work / ".orchestration").exists())
        self.dispatch()
        self.assertIn("Hand over to a successor", self.cli("compacted")[1])
        os.environ["ORCA_TERMINAL_HANDLE"] = "term_worker"  # a worker in the same folder is compacted
        self.assertEqual(self.cli("compacted"), (0, ""))

    def test_refuses_before_orca_is_asked(self):
        for refused, says in (
            ({"model": "unlisted-model"}, "not listed"),
            ({"model": None}, "pass --model"),  # only codex has a default model
            ({"effort": "max"}, "--effort"),
            ({"model": TOP, "effort": "high", "tier": "top"}, "medium"),  # the top tier is for re-runs at medium
        ):
            code, output = self.dispatch(**refused)
            self.assertEqual(code, 2, output)
            self.assertIn(says, output)
        del os.environ["ORCA_TERMINAL_HANDLE"]  # outside an Orca terminal the worker would start in whichever pane is active
        code, output = self.dispatch()
        self.assertEqual(code, 2)
        self.assertIn("ORCA_TERMINAL_HANDLE", output)
        self.assertEqual(self.orca_calls(), [])

    def test_gate_denial_starts_no_worker(self):
        code, output = self.cli("report")  # from a folder that is no workspace: an error, not an empty summary
        self.assertEqual(code, 2)
        self.assertIn("no dispatch ledger", output)
        self.assertFalse((self.work / ".orchestration").exists())
        code, output = self.dispatch(tier="top")  # the contract claims a tier the model is not in
        self.assertEqual(code, 2)
        self.assertIn("CONTRACT_TIER_MISMATCH", output)
        self.assertEqual(self.worker_starts(), [])
        self.assertEqual([row["decision"] for row in self.ledger()], ["DENY"])

    def test_worker_running_on_a_higher_tier_is_denied_at_register(self):
        os.environ["FAKE_ORCA_WORKER_MODEL"] = versioned(TOP)
        code, output = self.dispatch()
        self.assertEqual(code, 2)
        self.assertIn("MODEL_TIER_ESCALATION", output)
        self.assertIn("ctx_test", output)

    def watch(self, frames, *options, ends=0):
        """Run the watch against an Orca that shows one frame per look."""
        scripted = self.work / "frames.json"
        scripted.write_text(json.dumps(frames), encoding="utf-8")
        os.environ["FAKE_ORCA_FRAMES"] = str(scripted)
        code, output = self.cli("watch", "--run", "run_test", "--poll", "0", *options)
        self.assertEqual(code, ends, output)
        # `check` takes the mail, so a watch that ran it would hide the report from the master.
        self.assertNotIn("check", [call[1] for call in self.orca_calls() if call[0] == "orchestration"])
        return output

    def test_watch_is_silent_while_orca_delivers_and_ends_once_all_is_settled(self):
        output = self.watch([
            {"runtime": "one", "workers": {"ctx_test": "working"}, "unread": 0},
            {"runtime": "one", "workers": {}, "unread": 1},  # the report arrived and Orca is waking the master
            {"runtime": "one", "workers": {}, "unread": 0},  # the master read it
        ])
        self.assertIn("has settled", output)
        self.assertNotIn("unread and", output)
        self.assertEqual(list((self.work / ".orchestration").glob("watch-*")), [])

    def test_watch_wakes_the_master_after_a_restart_that_orca_does_not_wake_it_from(self):
        output = self.watch([
            {"runtime": "one", "workers": {"ctx_busy": "working", "ctx_test": "working"}, "unread": 0},
            {"runtime": None},  # Orca is down
            # Orca has lost sight of both workers. One is in the middle of a command and one is back at its prompt.
            {"runtime": "two", "workers": {"ctx_busy": "unknown", "ctx_test": "unknown"}, "printing": ["ctx_busy"], "unread": 1},
        ], "--grace", "0", "--stalled-after", "0")
        self.assertIn("1 message(s) for run_test are unread and Orca has not woken you. Orca was restarted", output)
        self.assertIn("orca orchestration check --run run_test", output)
        self.assertIn("dispatch ctx_test is open and its worker's screen in term_ctx_test has not changed for 0 s", output)
        self.assertIn("(activity unknown, liveness unverifiable). Orca was restarted", output)
        self.assertNotIn("ctx_busy", output)
        self.assertNotIn("ctx_settled", output)

    def test_watch_names_a_worker_that_stopped_without_settling(self):
        output = self.watch([{"runtime": "one", "workers": {"ctx_test": "done"}, "unread": 0}], "--stalled-after", "0")
        self.assertIn("screen in term_ctx_test has not changed for 0 s (activity done, liveness live)", output)
        self.assertIn("Look at it: orca orchestration worker-read --dispatch ctx_test", output)
        self.assertNotIn("restarted", output)
        self.assertNotIn("parked", output)
        frames = [{"runtime": "one", "workers": {"ctx_test": "done"}, "parked": ["ctx_test"], "unread": 0}]
        self.assertIn('parked on a prompt that only a human can answer: {"evidence": "prompt-text"}', self.watch(frames, "--stalled-after", "0"))
        # An outcome Orca did not have when this was written is never taken for "settled".
        frames = [{"runtime": "one", "workers": {"ctx_test": "unknown"}, "outcomes": {"ctx_test": "cancelled"}, "unread": 0}]
        output = self.watch(frames, "--stalled-after", "0")
        self.assertIn("the outcome 'cancelled', which this watch does not know and reads as open", output)
        self.assertNotIn("has settled", output)

    def test_watch_reads_every_page_and_passes_on_the_step_orca_names(self):
        output = self.watch([
            {"runtime": "one", "workers": {}, "older": {"ctx_old": "working"}, "unread": 0},  # the open worker is on page two
            {"runtime": "one", "workers": {}, "release": ["ctx_done"], "retained": ["ctx_own"], "unread": 0},
        ])
        self.assertEqual(sum(call[:2] == ["orchestration", "worker-list"] for call in self.orca_calls()), 3)
        self.assertIn("has settled and its mail is read.\n", output)
        self.assertIn("Orca names a next step for dispatch ctx_done: orca orchestration worker-release --dispatch ctx_done", output)
        # A worker in a worktree of its own is not closed by Orca, and Orca names no step for it either.
        self.assertIn("dispatch ctx_own has settled and Orca keeps its terminal open (user_requested)", output)
        self.assertIn("orca orchestration worker-release --dispatch ctx_own", output)
        self.assertNotIn("ctx_settled has settled and Orca keeps", output)
        self.assertNotIn("Nothing to do", output)

    def test_watch_names_a_dispatch_that_did_not_pass_the_gate(self):
        frames = [{"runtime": "one", "workers": {}, "release": ["ctx_gated"], "unread": 0}]
        self.assertNotIn("did not pass the gate", self.watch(frames))  # no ledger here, so nothing to compare with
        ledger = self.work / ".orchestration" / "dispatch-ledger.jsonl"
        ledger.write_text(json.dumps({"job_id": "ctx_gated"}) + "\nnot json\n", encoding="utf-8")
        output = self.watch(frames)
        # What a `send` to a settled dispatch starts: a worker in the Run that the ledger has never seen.
        self.assertIn("dispatch ctx_settled of run_test is not in this workspace's ledger, so it did not pass the gate", output)
        self.assertNotIn("dispatch ctx_gated of run_test is not in", output)

    def test_watch_gives_up_on_what_waiting_cannot_mend(self):
        output = self.watch([
            {"runtime": None},  # Orca being down is waited out, however long
            {"runtime": "one", "slow": True},  # and so is an Orca that is up and gives no answer about the Run
            {"runtime": "one", "refuses": True, "unread": 0},
        ], ends=2)
        self.assertIn('Orca has given no usable answer about run_test 5 times in a row, so nothing watches it. The last one: {"code": "invalid_argument"}', output)
        self.assertEqual(sum(call == ["status", "--json"] for call in self.orca_calls()), 7)
        os.environ["ORCA_CLI_COMMAND"] = str(self.work / "no-such-orca.exe")
        self.assertIn("cannot be run", self.watch([], ends=2))

    def test_watch_never_reads_an_answer_it_does_not_know_as_settled(self):
        # Each of these used to end the watch with "every dispatch has settled", and the master stopped waiting.
        for unusable, says in (
            ({"renamed": True}, "`workers` is not in the shape this wrapper reads"),
            ({"not_json": True}, "unknown option '--full'"),
            ({"unknown_run": True}, "Orca lists no dispatch in run_test"),
        ):
            output = self.watch([{"runtime": "one", "unread": 0, **unusable}], ends=2)
            self.assertIn(says, output)
            self.assertNotIn("has settled", output)

    def resume(self, dispatch_id="ctx_test"):
        return self.cli("resume", "--dispatch", dispatch_id)

    def session_id(self):
        return Path(str(self.log) + ".session").read_text(encoding="utf-8")

    def test_resume_brings_an_ended_session_back_in_its_own_terminal_and_tells_it_to_go_on(self):
        self.assertEqual(self.dispatch()[0], 0)
        before = self.ledger()
        os.environ["FAKE_ORCA_LIVENESS"] = "unverifiable"  # an agent that ended: Orca has no status of it
        code, output = self.resume()
        self.assertEqual(code, 0, output)
        line = json.loads(output)
        session = self.session_id()
        self.assertEqual(
            {name: line[name] for name in ("stage", "allow", "dispatch", "terminal", "session", "run")},
            {"stage": "resume", "allow": True, "dispatch": "ctx_test", "terminal": "term_worker_ctx_test", "session": session, "run": "run_test"},
        )
        self.assertIn("watch --run run_test", line["next"])
        self.assertIn("end your turn", line["next"])
        typed, waited, mail, notice = sends = self.sends()
        self.assertEqual([call[:2] for call in sends], [["terminal", "send"], ["terminal", "wait"], ["orchestration", "send"], ["terminal", "send"]])
        # Claude's own line, as printed, into the worker's own terminal: its handle, flags and folder are the worker's.
        self.assertEqual(typed[typed.index("--terminal") + 1], "term_worker_ctx_test")
        self.assertEqual(typed[typed.index("--text") + 1], f"claude --dangerously-skip-permissions --model claude-haiku-5-5 --effort low --resume {session}")
        self.assertIn("--enter", typed)
        self.assertEqual(waited[waited.index("--for") + 1], "tui-idle")  # the message and the notice wait for the session
        self.assertEqual(mail[mail.index("--to") + 1], "dispatch:ctx_test")
        self.assertEqual(mail[mail.index("--from") + 1], "term_test")
        body = mail[mail.index("--body") + 1]
        for said in ("ended and was started again", "without redoing what is done", "Finish command of your preamble"):
            self.assertIn(said, body)
        self.assertEqual(notice[notice.index("--text") + 1], "You have 1 orchestration message. Run orca orchestration check.")
        self.assertEqual(notice[notice.index("--terminal") + 1], "term_worker_ctx_test")
        self.assertEqual(self.ledger(), before)  # no dispatch, nothing written
        self.assertEqual(len(self.worker_starts()), 1)

    def test_resume_refuses_what_is_not_proven_ended_and_starts_nothing(self):
        self.assertEqual(self.dispatch()[0], 0)
        before, calls = self.ledger(), len(self.orca_calls())
        line = "claude --dangerously-skip-permissions --model claude-haiku-5-5 --effort low --resume"
        screen = ["❯ Cooked for 8s", "──────────", "  ⏵⏵ bypass permissions on (shift+tab to cycle)"]  # an agent that runs
        other = "00000000-0000-4000-8000-000000000000"
        reflector = {"type": "user", "message": {"role": "user", "content": "# Episode window (redacted) " + "x" * 20000 + " ctx_test"}}
        (self.transcripts() / f"{other}.jsonl").write_text(json.dumps(reflector) + "\n", encoding="utf-8")
        cases = (
            ("AGENT_LIVE", {"FAKE_ORCA_LIVENESS": "live"}, "orca orchestration send --to dispatch:ctx_test"),
            ("SUCCEEDED", {"FAKE_ORCA_OUTCOME": "succeeded"}, "nothing to resume"),
            ("FAILED", {"FAKE_ORCA_OUTCOME": "failed"}, "--follows ctx_test"),
            ("OUTCOME_UNKNOWN", {"FAKE_ORCA_OUTCOME": "paused"}, "which this command does not know"),
            ("ORCA_SILENT", {"FAKE_ORCA_OUTCOME": "no answer"}, "orca status"),
            ("ORCA_REFUSED", {"FAKE_ORCA_OUTCOME": "unknown dispatch"}, "dispatch line"),
            ("ORCA_SHAPE", {"FAKE_ORCA_WITHOUT": "worker.agentTerminalHandle"}, "lacks `worker.agentTerminalHandle`"),
            ("ORCA_SHAPE", {"FAKE_ORCA_WITHOUT": "projection.liveness"}, "lacks `projection.liveness.verdict`"),
            ("ORCA_SHAPE", {"FAKE_ORCA_WITHOUT": "dispatch.runId"}, "lacks `dispatch.runId`"),
            ("NOT_CLAUDE", {"FAKE_ORCA_AGENT": "codex"}, "Claude sessions only"),
            ("AGENT_NOT_PROVEN_ENDED", {"FAKE_ORCA_TAIL": json.dumps(screen)}, "orca terminal read --terminal term_worker_ctx_test --screen"),
            ("AGENT_NOT_PROVEN_ENDED", {"FAKE_ORCA_TAIL": json.dumps(["Resume this session with:", f"{line} {{session}}"])}, "may still run"),  # no prompt yet
            ("AGENT_NOT_PROVEN_ENDED", {"FAKE_ORCA_TAIL": json.dumps(["Resume this session with:", f"{line} {{session}}", "PS C:/work>", "PS C:/work> dir"])}, "may still run"),
            ("AGENT_NOT_PROVEN_ENDED", {"FAKE_ORCA_TAIL": json.dumps(["Resume this session with:", f"{line} {{session}} & calc", "PS C:/work>"])}, "may still run"),  # shell syntax
            ("AGENT_NOT_PROVEN_ENDED", {"FAKE_ORCA_TAIL": json.dumps([])}, "A terminal that is gone"),  # a released terminal reads as empty
            ("SESSION_MISMATCH", {"FAKE_ORCA_TAIL": json.dumps(["Resume this session with:", f"{line} {other}", "PS C:/work>"])}, "not the session file of ctx_test"),  # the reflector's
            ("SESSION_MISMATCH", {"FAKE_ORCA_TAIL": json.dumps(["Resume this session with:", f"{line} 11111111-1111-4111-8111-111111111111", "PS C:/work>"])}, "not the session file of ctx_test"),
        )
        for reason, environment, says in cases:
            with self.subTest(reason=reason, environment=environment), mock.patch.dict(os.environ, {"FAKE_ORCA_LIVENESS": "unverifiable", **environment}):
                code, output = self.resume()
                self.assertEqual(code, 2, output)
                self.assertEqual(json.loads(output.splitlines()[0]), {"stage": "resume", "allow": False, "dispatch": "ctx_test", "reason": reason})
                self.assertIn(says, output)
        self.assertEqual(self.sends(), [])  # not a character reached a terminal, and no mail was queued
        self.assertEqual(self.ledger(), before)
        self.assertEqual(len(self.worker_starts()), 1)
        self.assertGreater(len(self.orca_calls()), calls)  # Orca was asked, and the cases above did not need more than reads
        # The session file of the dispatch is gone: the line names a session that nothing here can show to be the worker's.
        for path in self.transcripts().glob("*.jsonl"):
            if path.stem != other:
                path.unlink()
        with mock.patch.dict(os.environ, {"FAKE_ORCA_LIVENESS": "unverifiable"}):
            self.assertEqual(self.resume()[0], 2)
        self.assertEqual(self.sends(), [])

    def test_resume_needs_a_dispatch_that_this_workspaces_ledger_holds(self):
        code, output = self.resume()  # a folder that dispatched nothing
        self.assertEqual(code, 2)
        self.assertIn("NOT_REGISTERED", output)
        self.assertIn("workspace root", output)
        self.assertEqual(self.orca_calls(), [])
        self.assertFalse((self.work / ".orchestration").exists())
        self.assertEqual(self.dispatch()[0], 0)
        calls = len(self.orca_calls())
        code, output = self.resume("ctx_elsewhere")
        self.assertEqual(code, 2)
        self.assertIn("NOT_REGISTERED", output)
        self.assertEqual(len(self.orca_calls()), calls)  # Orca was not asked about it
        del os.environ["ORCA_TERMINAL_HANDLE"]
        self.assertEqual(self.resume()[0], 2)
        self.assertEqual(len(self.orca_calls()), calls)

    def test_a_resume_that_cannot_confirm_a_step_says_not_to_run_it_again(self):
        self.assertEqual(self.dispatch()[0], 0)
        for environment, said in (({"FAKE_ORCA_SEND": "refused"}, "typed the resume command"), ({"FAKE_ORCA_IDLE": "never"}, "idle within 60 s")):
            with self.subTest(environment=environment), mock.patch.dict(os.environ, {"FAKE_ORCA_LIVENESS": "unverifiable", **environment}):
                code, output = self.resume()
                self.assertEqual(code, 2, output)
                self.assertIn(said, output)
                self.assertIn("Do not run resume again", output)
        self.assertNotIn(["orchestration", "send"], [call[:2] for call in self.orca_calls()])  # the session was not confirmed back

    def test_succeed_needs_every_handoff_section_before_launching(self):
        handoff = self.work / "handoff.md"
        launch = ["succeed", "--handoff", str(handoff), "--model", EFFICIENT, "--effort", "high"]
        template = orchestration.HANDOFF_TEMPLATE.read_text(encoding="utf-8")
        filled = "".join(f"## {heading}\n\nwhat this session knows\n\n" for heading in orchestration.HANDOFF_HEADINGS[1:])
        state = "## Role State\n\nCurrent Role: Maintenance\nRole Lock: DISABLED\nFrozen: none\nUnlock: none\n\n"
        for unusable, says in (
            ("## Role State\n", "`## Observed Baseline` is missing"),
            (template, "`## Current Objective` still holds the template's text"),  # saved without being filled in
            (state + filled.replace("what this session knows", "", 1), "`## Current Objective` is empty"),
            (state.replace("Maintenance", "Boss") + filled, "`Current Role` must be one of"),
            ("### Role State\n\n" + filled, "`## Role State` is missing"),  # a deeper heading is not the section
        ):
            handoff.write_text(unusable, encoding="utf-8")
            code, output = self.cli(*launch)
            self.assertEqual(code, 2, output)
            self.assertIn(says, output)
        self.assertEqual(self.orca_calls(), [])
        handoff.write_text(state + filled, encoding="utf-8")
        del os.environ["ORCA_TERMINAL_HANDLE"]  # a master outside Orca cannot dispatch, and this is its way in
        os.environ["FAKE_ORCA_CURRENT"] = "none"  # and its folder is none of Orca's workspaces yet
        code, output = self.cli(*launch, "--run", "run_test")
        self.assertEqual(code, 0, output)
        add, call = self.repo_adds()[0], self.orca_calls()[-1]
        self.assertEqual((add[add.index("--path") + 1], add[add.index("--kind") + 1]), (str(self.work), "folder"))
        self.assertFalse((self.work / ".agent").exists())  # a master's workspace gets no worker's rules
        self.assertEqual(call[:2], ["agent", "launch"])
        self.assertEqual(call[call.index("--surface") + 1], "terminal")  # a master in a chat has no handle to dispatch from
        self.assertEqual(call[call.index("--effort") + 1], "high")
        self.assertEqual(call[call.index("--model") + 1], versioned(EFFICIENT))
        self.assertIn("ran in no Orca terminal", call[call.index("--prompt") + 1])
        del os.environ["FAKE_ORCA_CURRENT"]  # from a workspace that Orca holds, nothing is added
        os.environ["ORCA_TERMINAL_HANDLE"] = "term_test"  # and a master in Orca names its terminal for `inherit`
        self.assertEqual(self.cli(*launch)[0], 0)
        prompt = self.orca_calls()[-1]
        self.assertIn("--parent-session is term_test,", prompt[prompt.index("--prompt") + 1])
        self.assertEqual(len(self.repo_adds()), 1)
        launches = sum(call[:2] == ["agent", "launch"] for call in self.orca_calls())
        os.environ.update(FAKE_ORCA_CURRENT="none", FAKE_ORCA_REPOS="no answer")  # a folder that cannot be added
        code, output = self.cli(*launch)
        self.assertEqual(code, 2, output)
        self.assertIn("ORCA_UNREADABLE", output)
        self.assertEqual(sum(call[:2] == ["agent", "launch"] for call in self.orca_calls()), launches)  # and no launch

    def test_start_carries_a_conversation_from_outside_orca_into_a_fresh_master(self):
        frames = self.work.parent / "frames.json"
        os.environ.update(FAKE_ORCA_FRAMES=str(frames), FAKE_ORCA_CURRENT="none")  # a folder Orca has never seen
        launches = lambda: [call for call in self.orca_calls() if call[:2] == ["agent", "launch"]]
        self.assertEqual(self.cli("start")[0], 2)  # in an Orca terminal the session is the master itself
        self.assertEqual(self.orca_calls(), [])
        del os.environ["ORCA_TERMINAL_HANDLE"]
        frames.write_text(json.dumps([{"runtime": None, "stale": True}]), encoding="utf-8")  # Orca is closed and stays so
        code, output = self.cli("start", "--said", "fix the login page")
        self.assertEqual(code, 2, output)
        self.assertIn("runtime_unavailable", output)
        self.assertIn("1. Ask the owner to open the Orca app", output)
        self.assertEqual([call[0] for call in self.orca_calls()], ["status", "open", "status", "status"])
        self.assertEqual(tree(self.work), {})  # and nothing was written
        frames.write_text(json.dumps([{"runtime": None}] * 4 + [{"runtime": "one"}]), encoding="utf-8")  # `open` brings it up
        code, output = self.cli("start", "--said", "fix the\nlogin page")
        self.assertEqual(code, 0, output)
        line = json.loads(output.splitlines()[-1])
        self.assertEqual((line["terminal"], line["workspace"]), ("term_launched", str(self.work)))
        add, call = self.repo_adds()[0], launches()[0]
        self.assertEqual((add[add.index("--path") + 1], add[add.index("--kind") + 1]), (str(self.work), "folder"))
        self.assertEqual(call[call.index("--surface") + 1], "terminal")  # a master in a chat has no handle to dispatch from
        self.assertNotIn("--model", call)  # the owner's own default, as in the session that is carried over
        self.assertIn("the owner's words: fix the login page (end", call[call.index("--prompt") + 1])  # on one line
        self.assertEqual(tree(self.work), {})  # no harness and no state: the master makes its own
        self.assertEqual(self.cli("start")[0], 0)  # Orca is up and holds the folder by now
        self.assertIn("The owner gave no task there", launches()[1][launches()[1].index("--prompt") + 1])
        self.assertEqual((len(self.repo_adds()), sum(call == ["open"] for call in self.orca_calls())), (1, 2))
        os.environ["FAKE_ORCA_REPOS"] = "no answer"  # a folder that cannot be added gets no master
        self.assertEqual(self.cli("start")[0], 2)
        self.assertEqual(len(launches()), 2)

    def test_inherit_appends_one_generation_per_succession(self):
        handoff = self.work / "handoff.md"
        handoff.write_text("handoff", encoding="utf-8")
        for open_terminals in ("term_new", "term_test", "term_old,term_test"):
            os.environ["FAKE_ORCA_TERMINALS"] = open_terminals
            code, output = self.cli("inherit", "--handoff", str(handoff), *LINEAGE_ARGUMENTS)
            self.assertEqual(code, 0, output)
        named = ["a session outside Orca" if argument == "term_old" else argument for argument in LINEAGE_ARGUMENTS]
        self.assertEqual(self.cli("inherit", "--handoff", str(handoff), *named)[0], 0)
        lineage = (self.work / ".orchestration" / "lineage.md").read_text(encoding="utf-8")
        self.assertIn("- **Verification**: **PASS**", lineage)
        generations = lineage.split("## Gen ")
        self.assertEqual([generation[:2] for generation in generations[1:]], ["1 ", "2 ", "3 ", "4 "])
        self.assertIn("is a session's name, not a terminal handle", generations[4])  # no terminal was looked for
        # Whether the predecessor is gone is measured, because two masters on one Run both take its mail. A list
        # without this session's own terminal proves nothing by what it lacks.
        self.assertIn("not verified (Orca's list of terminals does not show this terminal either)", generations[1])
        self.assertIn("terminal term_old is not among Orca's live terminals", generations[2])
        self.assertIn("not verified (terminal term_old is still open)", generations[3])
        self.assertEqual((self.work / ".orchestration" / "master").read_text(encoding="utf-8"), "term_test")

    def git(self, folder, *command, ends=0):
        identity = ["-c", "user.name=tester", "-c", "user.email=tester@example.invalid", "-c", "commit.gpgsign=false"]
        done = subprocess.run(["git", "-C", str(folder), *identity, *command], capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(done.returncode, ends, done.stdout + done.stderr)
        return done.stdout.strip()

    def prepare(self, project, ends=0):
        code, output = self.cli("prepare", str(project))
        self.assertEqual(code, ends, output)
        return json.loads(output)

    def repo_adds(self):
        return [call for call in self.orca_calls() if call[:2] == ["repo", "add"]]

    @unittest.skipUnless(harness.git_bash(), "needs the bash of Git for Windows")
    def test_prepare_makes_a_new_project_ready_and_a_second_run_changes_nothing(self):
        project = self.work.parent / "새 project"  # it does not exist yet
        os.environ["GIT_CONFIG_GLOBAL"] = str(self.work / "no-gitconfig")  # an account that never told git who it is
        line = self.prepare(project)
        self.assertEqual((line["orca"], line["kind"], line["harness"], line["setup"]), ("added", "git", "installed", "ran"), line)
        self.assertTrue(any("no author name or email" in entry for entry in line["attention"]), line)  # no commit would pass
        self.assertTrue(any(harness.STAGE_EXECUTABLE in entry for entry in line["attention"]), line)  # how to commit it
        for key in ("user.name", "user.email"):
            self.git(project, "config", key, "tester@example.invalid")
        self.assertEqual(line["worktree"], f"path:{project}")
        self.assertTrue((project / ".agent" / "Instructions.md").is_file())
        self.assertTrue((project / ".setup-done").is_file())
        self.assertEqual(self.git(project, "config", "core.hooksPath"), ".githooks")
        # No placeholder is left for a master to fill by hand, or for a worker's router to stop at.
        self.assertTrue(any("placeholders are now filled in" in entry and "CLAUDE.md" in entry for entry in line["attention"]), line)
        self.assertFalse(any("PROMPTS.md" in entry or "Double braces" in entry for entry in line["attention"]), line)
        self.assertEqual([name for name, content in tree(project).items() if b"{{" in content], ["PROMPTS.md"])
        self.assertIn("# Project Instructions: 새 project\n", (project / "CLAUDE.md").read_text(encoding="utf-8"))
        self.assertEqual(len({(project / router).read_bytes() for router in ("CLAUDE.md", "AGENTS.md", ".cursorrules", ".windsurfrules")}), 1)
        add = self.repo_adds()[0]
        self.assertEqual((add[add.index("--path") + 1], add[add.index("--kind") + 1]), (str(project), "git"))
        before = tree(project)
        line = self.prepare(str(project).swapcase() + "\\")  # the same folder, typed another way
        self.assertEqual((line["orca"], line["harness"], line["setup"]), ("registered", "present", "done"), line)
        self.assertFalse(any("no author name or email" in entry or "filled" in entry for entry in line["attention"]), line)
        self.assertEqual(line["project"], str(project))
        self.assertEqual(tree(project), before)
        self.assertEqual(len(self.repo_adds()), 1)

    def test_prepare_knows_a_project_that_orca_holds_under_another_path(self):
        project = self.ready("ready")
        junction = self.work.parent / "link"
        made = subprocess.run(["cmd", "/c", "mklink", "/J", str(junction), str(project)], capture_output=True)
        self.assertEqual(made.returncode, 0, made.stdout + made.stderr)
        # Orca was given the junction, in capitals and with forward slashes. A second project for one folder
        # would be the result of comparing the two as text.
        known = [{"id": "repo_test", "path": junction.as_posix().upper(), "kind": "folder"}]
        Path(str(self.log) + ".repos").write_text(json.dumps(known), encoding="utf-8")
        for spelling in (project, junction):
            line = self.prepare(spelling)
            self.assertEqual((line["orca"], line["kind"], line["project"]), ("registered", "folder", str(project)), line)
        self.assertEqual(self.repo_adds(), [])

    def test_prepare_leaves_what_an_existing_project_has(self):
        project = self.work.parent / "existing"
        project.mkdir()
        self.git(project, "init", "-q")
        (project / "CLAUDE.md").write_text("our own rules for {{mustache}}\n", encoding="utf-8")
        ignores = b"node_modules\r\n*.log"  # CRLF, and no line ending after the last line
        (project / ".gitignore").write_bytes(ignores)
        (project / ".git" / "hooks" / "pre-push").write_bytes(b"#!/bin/sh\n")  # as git-lfs installs one
        line = self.prepare(project)
        self.assertEqual((line["orca"], line["kind"], line["harness"], line["setup"]), ("added", "git", "installed", "waits"), line)
        self.assertEqual((project / "CLAUDE.md").read_text(encoding="utf-8"), "our own rules for {{mustache}}\n")
        self.assertIn("CLAUDE.md.starter", " ".join(line["attention"]))
        self.assertNotIn(b"{{", (project / "CLAUDE.md.starter").read_bytes())  # taken as it is, it needs no filling
        # Nearly every project has a .gitignore: it gets the lines it lacks, and no starter that the setup waits for.
        merged = (project / ".gitignore").read_bytes()
        self.assertTrue(merged.startswith(ignores + b"\r\n# Repository harness\r\n.pnpm-store/\r\n"), merged)
        self.assertNotIn(b"node_modules/", merged)  # the project's own pattern covers it
        self.assertNotIn(b"\n\n", merged.replace(b"\r\n", b"\n"))
        self.assertFalse((project / ".gitignore.starter").exists())
        self.assertTrue(any(".gitignore lacked" in entry and ".setup-done" in entry for entry in line["attention"]), line)
        before = tree(project)
        line = self.prepare(project)  # the starter still waits: nothing is staged a second time
        self.assertEqual((line["orca"], line["harness"], line["setup"]), ("registered", "present", "waits"), line)
        self.assertEqual(tree(project), before)
        # The owner takes the harness's router. The project's own hook still stands in the way of the setup.
        shutil.move(project / "CLAUDE.md.starter", project / "CLAUDE.md")
        line = self.prepare(project)
        self.assertEqual(line["setup"], "waits", line)
        self.assertIn("git hooks of its own (pre-push)", " ".join(line["attention"]))
        # So does a hook folder the project names, even one that holds no hook yet.
        self.git(project, "config", "core.hooksPath", ".hooks")
        line = self.prepare(project)
        self.assertEqual(line["setup"], "waits", line)
        self.assertIn("git hooks of its own (core.hooksPath = .hooks)", " ".join(line["attention"]))
        self.assertEqual(self.git(project, "config", "core.hooksPath"), ".hooks")
        self.git(project, "config", "--unset", "core.hooksPath")
        # A project that has changed a script of its harness has its own setup: it is not run from here.
        with open(project / "scripts" / "setup.sh", "ab") as script:
            script.write(b"touch ran-by-prepare\n")
        (project / ".git" / "hooks" / "pre-push").unlink()
        line = self.prepare(project)
        self.assertEqual(line["setup"], "left to the first session there", line)
        self.assertFalse((project / "ran-by-prepare").exists())
        self.assertFalse((project / ".setup-done").exists())
        self.assertEqual(self.git(project, "config", "--get", "core.hooksPath", ends=1), "")

    def test_prepare_installs_no_harness_into_the_plugin_itself(self):
        plugin = self.work.parent / "plugin"  # a checkout of this plugin holds the harness as a template
        (plugin / "harness").mkdir(parents=True)
        with mock.patch.object(harness, "SOURCE", plugin / "harness"):
            line = self.prepare(plugin)
        self.assertEqual((line["orca"], line["kind"], line["harness"]), ("added", "folder", "skipped"), line)
        self.assertNotIn("setup", line)
        self.assertEqual(tree(plugin), {})

    def test_prepare_refuses_what_is_not_a_project_root_and_writes_nothing(self):
        base = self.work.parent
        repo = base / "repo"
        (repo / "sub").mkdir(parents=True)
        self.git(repo, "init", "-q")
        self.git(repo, "commit", "-q", "--allow-empty", "-m", "first")
        self.git(repo, "worktree", "add", "-q", str(base / "linked"))
        self.git(base, "init", "-q", "--bare", "bare.git")
        (base / "plain").mkdir()
        for folder, reason in (
            (repo / "sub", "NOT_A_PROJECT_ROOT"),  # a folder inside a repository
            (repo / "new", "NOT_A_PROJECT_ROOT"),  # and one that would be made inside it
            (base / "linked", "NOT_A_PROJECT_ROOT"),  # a worker's worktree, which Orca would take for a second project
            (base / "bare.git", "GIT_UNREADABLE"),
            (base / "missing" / "project", "NO_SUCH_DIRECTORY"),  # a mistyped path makes no folders
            (Path(self.contract()), "NO_SUCH_DIRECTORY"),
            (Path(self.contract()) / "project", "NO_SUCH_DIRECTORY"),
            (base / "home", "NOT_A_PROJECT"),  # a repository there would hold every project below it
        ):
            self.assertEqual(self.prepare(folder, ends=2)["reason"], reason, folder)
        # Git refuses a repository that another account owns with the exit code it has for "no repository". Read
        # as that, the folder inside it got a harness and, from the setup, a repository of its own.
        empty = base / "empty.gitconfig"
        empty.write_bytes(b"")
        other_owner = {"GIT_TEST_ASSUME_DIFFERENT_OWNER": "1", "GIT_CONFIG_GLOBAL": str(empty), "GIT_CONFIG_NOSYSTEM": "1"}
        with mock.patch.dict(os.environ, other_owner):
            line = self.prepare(repo / "sub", ends=2)
        self.assertEqual(line["reason"], "GIT_UNREADABLE", line)
        self.assertIn("dubious ownership", line["message"])
        self.assertEqual(self.orca_calls(), [])
        os.environ["FAKE_ORCA_REPOS"] = "no answer"  # with Orca away nothing is changed either
        for folder in (base / "fresh", base / "plain"):
            self.assertEqual(self.prepare(folder, ends=2)["reason"], "ORCA_UNREADABLE")
        self.assertEqual(tree(base / "plain"), {})
        for unwritten in (repo / "new", repo / ".agent", repo / "sub" / ".agent", base / "linked" / ".agent", base / "missing", base / "fresh"):
            self.assertFalse(unwritten.exists(), unwritten)

    def test_dispatch_into_a_project_prepares_it_before_the_gate(self):
        base = self.work.parent
        (base / "repo" / "sub").mkdir(parents=True)
        self.git(base / "repo", "init", "-q")
        project = self.ready("ready")
        code, output = self.dispatch("--project", str(base / "repo" / "sub"))
        self.assertEqual(code, 2, output)
        self.assertIn("NOT_A_PROJECT_ROOT", output)
        self.assertEqual((self.worker_starts(), self.ledger()), ([], []))  # refused before the gate counts a check
        self.assertIn("takes only new-top-level", self.dispatch("--project", str(project), "--worktree", "current")[1])
        code, output = self.cli("dispatch", "--contract", "no-such.md", "--agent", "claude", "--model", EFFICIENT, "--effort", "low", "--project", str(project))
        self.assertEqual(code, 2, output)
        self.assertIn("no contract at", output)
        self.assertEqual((self.orca_calls(), self.ledger()), ([], []))  # a mistyped contract registers no project
        code, output = self.dispatch("--project", str(project))
        self.assertEqual(code, 0, output)
        prepared, registered = (json.loads(line) for line in output.splitlines())
        self.assertEqual((prepared["stage"], prepared["orca"], prepared["kind"], prepared["harness"]), ("prepare", "added", "folder", "present"))
        self.assertEqual((registered["stage"], registered["workspace"]), ("register", self.work.as_posix()))
        start = self.worker_starts()[0]
        self.assertEqual(start[start.index("--worktree") + 1], f"path:{project}")

    def test_a_worktree_of_the_workers_own_starts_from_the_projects_branch(self):
        project = self.ready("cloned")
        own = ("--project", str(project), "--worktree", "new-top-level")
        for _ in range(2):  # first a plain folder, then a repository without a commit
            code, output = self.dispatch(*own)
            self.assertEqual(code, 2, output)
            self.assertIn("no branch checked out with a commit", output)
            self.assertIn("starts without its rules", output)
            self.git(project, "init", "-q")
        self.assertEqual((self.worker_starts(), self.ledger()), ([], []))
        self.git(project, "add", "-A")
        self.git(project, "commit", "-q", "-m", "the harness")
        code, output = self.dispatch(*own)
        self.assertEqual(code, 0, output)
        self.assertNotIn("starts without its rules", output)
        start = self.worker_starts()[0]
        # Orca bases a new worktree on the remote's branch unless it is told: local commits would be missing.
        place = start[start.index("--worktree"):start.index("--worktree") + 6]
        branch = self.git(project, "branch", "--show-current")
        self.assertEqual(place, ["--worktree", "new-top-level", "--repo", f"path:{project}", "--base-branch", branch])


if __name__ == "__main__":
    unittest.main()
