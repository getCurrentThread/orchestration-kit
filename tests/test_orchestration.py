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
import time
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

    def test_a_dispatch_that_orca_lost_to_a_reset_holds_neither_a_contract_nor_a_follow_up(self):
        self.assertEqual(self.dispatch()[0], 0)
        fix = self.work / "contract-2.md"
        fix.write_text(Path(self.contract()).read_text(encoding="utf-8") + "\nFix it.\n", encoding="utf-8")
        follow = ["dispatch", "--contract", str(fix), "--agent", "claude", "--model", EFFICIENT, "--effort", "low", "--follows", "ctx_test"]
        for held in ("no answer", "other refusal"):  # no answer, and a refusal with another code, still hold the dispatch
            os.environ["FAKE_ORCA_OUTCOME"] = held
            for code, output in (self.dispatch(), self.cli(*follow)):
                self.assertEqual(code, 2, output)
            self.assertEqual(len(self.worker_starts()), 1)
        os.environ["FAKE_ORCA_OUTCOME"] = "unknown dispatch"  # as after `orca orchestration reset`
        os.environ.update(FAKE_ORCA_DISPATCH="fix", FAKE_ORCA_FRESH="fix")
        code, output = self.cli(*follow)
        self.assertEqual(code, 0, output)
        os.environ.update(FAKE_ORCA_DISPATCH="again", FAKE_ORCA_FRESH="again")
        code, output = self.dispatch()
        self.assertEqual(code, 0, output)  # a second dispatch of the first contract file
        self.assertEqual(len(self.worker_starts()), 3)

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

    def test_a_gate_answer_without_what_register_needs_refuses_before_a_worker_starts(self):
        gate = orchestration.gate
        cases = (
            ("check", "contract_sha", "check", "CONTRACT_SHA_MISSING"),
            ("budget", "tokens_basis", "budget", "BUDGET_FIELDS_MISSING"),
            ("budget", "source", "budget", "BUDGET_FIELDS_MISSING"),
            ("budget", "token_budget", "budget", "BUDGET_FIELDS_MISSING"),
        )
        for command, field, stage, reason in cases:
            def without(*arguments, command=command, field=field):
                code, decision = gate(*arguments)
                if arguments[0] == command:
                    decision.pop(field, None)
                return code, decision

            with self.subTest(field=field):
                with mock.patch.object(orchestration, "gate", without):
                    code, output = self.dispatch()
                self.assertEqual(code, 2, output)
                line = json.loads(output.splitlines()[0])
                self.assertEqual((line["stage"], line["allow"], line["reason"]), (stage, False, reason))
                self.assertEqual(self.worker_starts(), [])  # refused with nothing started, not by an error after the start
                self.assertFalse(any(row.get("job_id") for row in self.ledger()))

    def test_a_worker_start_that_orca_refuses_registers_nothing(self):
        os.environ["FAKE_ORCA_START"] = "exit 1"
        code, output = self.dispatch()
        self.assertEqual(code, 2, output)
        self.assertIn("worker-start refused", output)  # Orca's own answer is printed
        self.assertIn("worker-start failed; load Orca's recovery guide before retrying", output)
        self.assertFalse(any(row.get("job_id") for row in self.ledger()))

    def test_a_start_that_orca_calls_done_without_a_dispatch_id_is_no_failure_and_registers_nothing(self):
        os.environ["FAKE_ORCA_START"] = "no dispatchId"
        code, output = self.dispatch()
        self.assertEqual(code, 2, output)
        self.assertIn('"taskId": "task_test"', output)  # Orca's answer is printed
        message = next(line for line in output.splitlines() if line.startswith("orchestration:"))
        self.assertNotIn("failed", message)  # a worker was started
        for said in ("Orca reported the start as done", "may be running which no ledger row holds", "orca orchestration run-current --json",
                     "orca orchestration worker-list --run <run>", "Do not dispatch this contract again before that worker is found"):
            self.assertIn(said, message)
        self.assertFalse(any(row.get("job_id") for row in self.ledger()))
        self.assertEqual(len(self.worker_starts()), 1)

    def test_a_receipt_whose_effects_cannot_be_read_gives_no_workspace_and_the_worker_is_registered(self):
        for number, shape in enumerate(("effects without id", "effects without kind", "no effects", "effects not a list")):
            name = shape.replace(" ", "_")
            with self.subTest(shape=shape), mock.patch.dict(os.environ, {"FAKE_ORCA_START": shape, "FAKE_ORCA_DISPATCH": name}):
                code, output = self.dispatch(front=f"budget: [tokens={1000 + number}]\n")  # a contract of its own
                self.assertEqual(code, 0, output)
                line = json.loads(output)
                self.assertEqual((line["dispatch"], line["workspace"]), (f"ctx_{name}", ""))
                self.assertIn(f"ctx_{name}", [row.get("job_id") for row in self.ledger()])

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
        os.environ["FAKE_ORCA_TAIL"] = json.dumps(["Working (12s)"])  # an agent that still runs; the resume line is another test's
        output = self.watch([{"runtime": "one", "workers": {"ctx_test": "done"}, "unread": 0}], "--stalled-after", "0")
        self.assertIn("screen in term_ctx_test has not changed for 0 s (activity done, liveness live)", output)
        self.assertIn("Look at it: orca orchestration worker-read --dispatch ctx_test", output)
        self.assertNotIn("restarted", output)
        self.assertNotIn("parked", output)
        frames = [{"runtime": "one", "workers": {"ctx_test": "done"}, "parked": ["ctx_test"], "unread": 0}]
        self.assertIn('parked on a prompt that only a human can answer: {"source": "hook", "since": 1794824861316}', self.watch(frames, "--stalled-after", "0"))
        # An outcome Orca did not have when this was written is never taken for "settled".
        frames = [{"runtime": "one", "workers": {"ctx_test": "unknown"}, "outcomes": {"ctx_test": "cancelled"}, "unread": 0}]
        output = self.watch(frames, "--stalled-after", "0")
        self.assertIn("the outcome 'cancelled', which this watch does not know and reads as open", output)
        self.assertNotIn("has settled", output)

    def test_watch_names_resume_for_a_standing_screen_that_shows_the_resume_line(self):
        frames = [{"runtime": "one", "workers": {"ctx_test": "unknown"}, "unread": 0}]
        output = self.watch(frames, "--stalled-after", "0")  # the fake's default text ends with Claude's resume line and a prompt
        self.assertIn("The agent has ended and its terminal is kept", output)
        self.assertIn("ORCHESTRATION resume --dispatch ctx_test", output)
        self.assertNotIn("worker-read", output)
        shell = json.dumps(["Working (12s)", "PS C:\\work>"])  # a prompt without the resume line: the agent may still run
        for environment in ({"FAKE_ORCA_TAIL": shell}, {"FAKE_ORCA_READ": "no answer"}):
            with mock.patch.dict(os.environ, environment):
                output = self.watch(frames, "--stalled-after", "0")
            self.assertIn("Look at it: orca orchestration worker-read --dispatch ctx_test", output)
            self.assertNotIn("resume --dispatch", output)
        self.assertEqual(self.sends(), [])  # the watch typed nothing and started nothing
        self.assertEqual(self.worker_starts(), [])

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
        ledger.write_text(json.dumps({"job_id": "ctx_gated", "decision": "ALLOW"}) + "\nnot json\n", encoding="utf-8")
        output = self.watch(frames)
        # What a `send` to a settled dispatch starts: a worker in the Run that the ledger has never seen.
        self.assertIn("dispatch ctx_settled of run_test is not registered in this workspace's ledger, so it did not pass the gate", output)
        self.assertNotIn("dispatch ctx_gated of run_test is not registered", output)

    def test_watch_takes_a_denied_register_for_a_dispatch_that_did_not_pass_the_gate(self):
        # The gate writes a DENY row with a job_id when it refuses a register (MODEL_TIER_ESCALATION): the worker runs all the same.
        frames = [{"runtime": "one", "workers": {}, "release": ["ctx_allowed", "ctx_denied"], "unread": 0}]
        ledger = self.work / ".orchestration" / "dispatch-ledger.jsonl"
        ledger.parent.mkdir()
        rows = ({"job_id": "ctx_allowed", "decision": "ALLOW"}, {"job_id": "ctx_denied", "decision": "DENY", "reason": "MODEL_TIER_ESCALATION"})
        ledger.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
        output = self.watch(frames)
        self.assertIn("dispatch ctx_denied of run_test is not registered in this workspace's ledger", output)
        self.assertNotIn("dispatch ctx_allowed of run_test is not registered", output)
        ledger.write_text(json.dumps(rows[1]) + "\n", encoding="utf-8")  # a workspace whose only dispatch was denied is still one that dispatched
        self.assertIn("dispatch ctx_denied of run_test is not registered in this workspace's ledger", self.watch(frames))

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

    def test_succeed_keeps_the_owners_default_model_unless_one_is_named(self):
        handoff = self.work / "handoff.md"
        state = "## Role State\n\nCurrent Role: Maintenance\n\n"
        handoff.write_text(state + "".join(f"## {heading}\n\nwhat this session knows\n\n" for heading in orchestration.HANDOFF_HEADINGS[1:]), encoding="utf-8")
        code, output = self.cli("succeed", "--handoff", str(handoff))
        self.assertEqual(code, 0, output)
        launch = self.orca_calls()[-1]
        self.assertEqual(launch[:2], ["agent", "launch"])
        self.assertTrue(set(launch).isdisjoint({"--model", "--effort"}))  # as `start`: no model chosen by the kit
        before = len(self.orca_calls())
        for refused, says in (
            (["--model", "unlisted-model", "--effort", "low"], "not listed"),  # with --model the checks stay
            (["--model", TOP, "--effort", "high"], "medium"),
            (["--effort", "low"], "--effort needs --model"),
            (["--model", EFFICIENT], "pass --effort"),
        ):
            code, output = self.cli("succeed", "--handoff", str(handoff), *refused)
            self.assertEqual(code, 2, output)
            self.assertIn(says, output)
        self.assertEqual(len(self.orca_calls()), before)

    def test_succeed_exits_0_only_when_orca_names_a_terminal_and_the_kickoff_reached_it(self):
        handoff = self.work / "handoff.md"
        state = "## Role State\n\nCurrent Role: Maintenance\n\n"
        handoff.write_text(state + "".join(f"## {heading}\n\nwhat this session knows\n\n" for heading in orchestration.HANDOFF_HEADINGS[1:]), encoding="utf-8")
        refusals = (
            ("no terminal", ("named no terminal with a successor", "a second run would start a second successor", f"orca terminal list --worktree path:{self.work} --json")),
            ("not delivered", ("terminal term_launched", "(prompt outcome: 'rejected')", "orca terminal read --terminal term_launched --screen", "Do not run this again")),
        )
        for launch, said in refusals:
            with self.subTest(launch=launch):
                with mock.patch.dict(os.environ, {"FAKE_ORCA_LAUNCH": launch}):
                    code, output = self.cli("succeed", "--handoff", str(handoff))
                self.assertEqual(code, 2, output)
                for part in said:
                    self.assertIn(part, output)
        code, output = self.cli("succeed", "--handoff", str(handoff))
        self.assertEqual(code, 0, output)
        line = json.loads(output)  # one line, and not Orca's raw answer
        self.assertEqual({name: line[name] for name in ("stage", "allow", "terminal")}, {"stage": "succeed", "allow": True, "terminal": "term_launched"})
        for step in ("stop your watch", "Tell the owner", "Then stop working"):
            self.assertIn(step, line["next"])
        launch = self.orca_calls()[-1]
        self.assertNotIn("--focus", launch)  # `--focus` stays with `start`

    def test_a_handoff_asks_of_the_role_state_a_current_role_and_no_more(self):
        filled = "".join(f"## {heading}\n\nwhat this session knows\n\n" for heading in orchestration.HANDOFF_HEADINGS[1:])
        current = "## Role State\n\n```\nCurrent Role: Maintenance\n```\n\n"
        old = "## Role State\n\n```\nCurrent Role: Maintenance\nRole Lock: DISABLED\nFrozen: none\nUnlock: none\n```\n\n"
        for state in (current, old):  # a handoff written before the three lines were dropped is accepted
            self.assertEqual(orchestration.handoff_problems(state + filled), [])
        self.assertEqual(len(orchestration.handoff_problems(current.replace("Maintenance", "Boss") + filled)), 1)
        template = orchestration.HANDOFF_TEMPLATE.read_text(encoding="utf-8")
        for dropped in ("Role Lock", "Frozen", "Unlock"):
            self.assertNotIn(dropped, template)
        self.assertIn("Current Role: Maintenance", template)
        self.assertFalse([problem for problem in orchestration.handoff_problems(template) if "Role State" in problem])

    def test_the_contract_template_declares_no_budget_and_the_gate_takes_it_so(self):
        template = (ROOT / "skills" / "master" / "contract-template.md").read_text(encoding="utf-8")
        front = template.split("---\n")[1]
        self.assertNotIn("budget", front)
        self.assertIn("only tokens=N is measured", template)
        code, output = self.cli("dispatch", "--contract", self.contract(), "--agent", "claude", "--model", EFFICIENT, "--effort", "low")
        self.assertEqual(code, 0, output)
        self.assertEqual(json.loads(output)["token_budget"], None)

    def test_the_model_probe_reads_the_session_that_names_the_dispatch(self):
        # A newer session in the worker's folder, on a model of the top tier, whose first prompt names no dispatch:
        # a second worker there, or the reflector that the owner's autoharness starts when a session ends.
        os.environ["FAKE_ORCA_DECOY"] = versioned(TOP)
        code, output = self.dispatch()
        self.assertEqual(code, 0, output)
        self.assertEqual(len(list(self.transcripts().glob("*.jsonl"))), 2)  # the decoy is there, and newer
        self.assertNotIn("MODEL_", output)  # neither MODEL_TIER_ESCALATION nor a warning
        row = self.ledger()[1]
        self.assertEqual((row["model_measured"], row["model_verified"]), (EFFICIENT, True))
        # A session that does not name the dispatch is never read: with no other, the probe waits out its time and exits 1.
        with mock.patch.object(orchestration.time, "monotonic", side_effect=[0, 0, 100]), mock.patch.object(orchestration.time, "sleep"):
            code, output = self.cli("probe-model", "claude", self.work.as_posix(), "0", "ctx_other")
        self.assertEqual((code, output), (1, ""))

    def test_succeed_needs_every_handoff_section_before_launching(self):
        handoff = self.work / "handoff.md"
        launch = ["succeed", "--handoff", str(handoff), "--model", EFFICIENT, "--effort", "high"]
        template = orchestration.HANDOFF_TEMPLATE.read_text(encoding="utf-8")
        filled = "".join(f"## {heading}\n\nwhat this session knows\n\n" for heading in orchestration.HANDOFF_HEADINGS[1:])
        state = "## Role State\n\nCurrent Role: Maintenance\n\n"
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
        self.assertIn("not verified (nothing was closed: .orchestration/master held another handle", generations[3])
        self.assertEqual((self.work / ".orchestration" / "master").read_text(encoding="utf-8"), "term_test")

    def succession(self, recorded="term_old", verification="PASS", count="0", parent="term_old", **environment):
        """Run `inherit` as the successor term_test, with the predecessor term_old among Orca's terminals and `recorded` in the file.

        Return the exit code, the Orca calls to `terminal close`, and the newest lineage entry.
        """
        for leftover in (self.log, Path(str(self.log) + ".closed"), Path(str(self.log) + ".lag")):
            leftover.unlink(missing_ok=True)
        master = self.work / ".orchestration" / "master"
        master.parent.mkdir(exist_ok=True)
        master.unlink(missing_ok=True)
        if recorded:
            master.write_text(recorded, encoding="utf-8")
        handoff = self.work / "handoff.md"
        handoff.write_text("handoff", encoding="utf-8")
        arguments = [{"PASS": verification, "term_old": parent}.get(argument, argument) for argument in LINEAGE_ARGUMENTS]
        with mock.patch.dict(os.environ, {"FAKE_ORCA_TERMINALS": "term_old,term_test", **environment}):
            code, output = self.cli("inherit", "--handoff", str(handoff), *arguments, "--repeated-question-count", count)
        closes = [call for call in self.orca_calls() if call[:2] == ["terminal", "close"]]
        ledger = self.work / ".orchestration" / "lineage.md"
        entry = ledger.read_text(encoding="utf-8").split("## Gen ")[-1] if ledger.exists() else ""
        return code, closes, entry

    def test_inherit_closes_the_recorded_predecessor_after_a_pass_and_proves_it_gone(self):
        code, closes, entry = self.succession()
        self.assertEqual((code, closes), (0, [["terminal", "close", "--terminal", "term_old", "--json"]]))
        self.assertIn("terminal term_old closed by its successor", entry)
        self.assertIn("no longer among Orca's live terminals", entry)
        self.assertEqual(sum(call[:2] == ["terminal", "list"] for call in self.orca_calls()), 2)  # before the close, and after it
        self.assertEqual((self.work / ".orchestration" / "master").read_text(encoding="utf-8"), "term_test")

    def test_inherit_keeps_the_predecessor_open_after_partial_or_failed(self):
        for verification in ("PARTIAL", "FAILED"):
            with self.subTest(verification=verification):
                code, closes, entry = self.succession(verification=verification)
                self.assertEqual((code, closes), (0, []))
                self.assertIn(f"kept open (verification {verification}", entry)
                self.assertIn("orca terminal close --terminal term_old", entry)

    def test_inherit_closes_nothing_after_a_pass_unless_the_workspace_record_names_the_predecessor(self):
        for recorded in ("term_other", None):  # another handle, and no file
            with self.subTest(recorded=recorded):
                code, closes, entry = self.succession(recorded=recorded)
                self.assertEqual((code, closes), (0, []))
                self.assertIn("not verified (nothing was closed", entry)
                self.assertIn("orca terminal close --terminal term_old", entry)
        with self.subTest(recorded="this terminal's own"):  # the predecessor is this very terminal
            code, closes, entry = self.succession(recorded="term_test", parent="term_test", FAKE_ORCA_TERMINALS="term_test")
            self.assertEqual((code, closes), (0, []))
            self.assertIn("not verified (nothing was closed", entry)

    def test_inherit_records_what_it_found_when_orca_does_not_close_the_terminal(self):
        for switch, said in (
            ("stale", "not verified (Orca did not close terminal term_old: terminal_handle_stale"),
            ("odd answer", "not verified (Orca did not close terminal term_old: an answer not in the shape measured"),
            ("not carried out", "not verified (Orca said it closed terminal term_old, and it is still among Orca's live terminals"),
        ):
            with self.subTest(switch=switch), mock.patch.object(orchestration, "CLOSE_WAIT", 0.3):  # not the 10 s of a real wait
                code, closes, entry = self.succession(FAKE_ORCA_CLOSE=switch)
                self.assertEqual((code, len(closes)), (0, 1))
                self.assertIn(said, entry)
                self.assertEqual((self.work / ".orchestration" / "master").read_text(encoding="utf-8"), "term_test")

    def test_inherit_waits_for_a_closed_terminal_that_stays_listed_for_a_moment(self):
        # Measured on 1.4.259: the list right after a close still holds the handle for about a second.
        code, closes, entry = self.succession(FAKE_ORCA_CLOSE_LAG="2")
        self.assertEqual((code, len(closes)), (0, 1))
        self.assertIn("terminal term_old closed by its successor", entry)
        self.assertGreaterEqual(sum(call[:2] == ["terminal", "list"] for call in self.orca_calls()), 3)

    def test_inherit_stops_waiting_for_a_close_that_is_not_carried_out(self):
        started = time.monotonic()
        with mock.patch.object(orchestration, "CLOSE_WAIT", 0.5):
            _, closes, entry = self.succession(FAKE_ORCA_CLOSE="not carried out")
        self.assertIn("and it is still among Orca's live terminals", entry)
        self.assertEqual(len(closes), 1)
        self.assertLess(time.monotonic() - started, 2)

    def test_inherit_closes_nothing_for_an_entry_the_lineage_refuses(self):
        _, closes, before = self.succession()
        self.assertEqual(len(closes), 1)  # the same run, with an entry the lineage takes, closes
        code, closes, entry = self.succession(count="-1")
        self.assertEqual((code, closes, entry), (2, [], before))  # and no entry was added
        self.assertEqual((self.work / ".orchestration" / "master").read_text(encoding="utf-8"), "term_old")

    def test_succeed_records_this_terminal_as_the_master_only_in_an_orca_terminal(self):
        handoff = self.work / "handoff.md"
        master = self.work / ".orchestration" / "master"
        handoff.write_text("## Role State\n", encoding="utf-8")  # refused: the successor is not launched, and nothing is recorded
        self.assertEqual(self.cli("succeed", "--handoff", str(handoff))[0], 2)
        self.assertFalse(master.exists())
        state = "## Role State\n\nCurrent Role: Maintenance\n\n"
        handoff.write_text(state + "".join(f"## {heading}\n\nwhat this session knows\n\n" for heading in orchestration.HANDOFF_HEADINGS[1:]), encoding="utf-8")
        code, output = self.cli("succeed", "--handoff", str(handoff))
        self.assertEqual(code, 0, output)
        self.assertEqual(master.read_text(encoding="utf-8"), "term_test")
        self.assertIn("your terminal closes once the successor has recorded the succession with PASS", json.loads(output)["next"])
        master.unlink()
        del os.environ["ORCA_TERMINAL_HANDLE"]
        self.assertEqual(self.cli("succeed", "--handoff", str(handoff))[0], 0)
        self.assertFalse(master.exists())

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


    def test_dispatch_state_names_the_six_states_every_command_reads(self):
        shown = lambda outcome: {"ok": True, "result": {"projection": {"outcome": outcome}}}  # noqa: E731
        for answer, expected in (
            (shown("in_progress"), ("open", "in_progress")),
            (shown("succeeded"), ("succeeded", "succeeded")),
            (shown("failed"), ("failed", "failed")),
            (shown("paused"), ("unknown", "paused")),  # an outcome this wrapper does not know is read as open
            ({"ok": False, "error": {"code": "dispatch_not_found"}}, ("gone", None)),
            ({}, ("unread", None)),  # no answer
            ({"ok": False, "error": {"code": "invalid_argument"}}, ("unread", None)),  # another refusal
            ({"ok": False, "error": "unknown option"}, ("unread", None)),
            ({"ok": True, "result": {"projection": {}}}, ("unread", None)),  # an answer without an outcome
            ({"ok": True, "result": []}, ("unread", None)),
            (shown(7), ("unread", 7)),  # an outcome that is no string
            # Only an ok answer holds a dispatch.
            ({"ok": False, "result": {"projection": {"outcome": "succeeded"}}}, ("unread", None)),
        ):
            with self.subTest(answer=answer):
                self.assertEqual(orchestration.dispatch_state(answer), expected)
        self.assertEqual([orchestration.outcome_state(value) for value in ("in_progress", "failed", "x", None)], ["open", "failed", "unknown", "unread"])

    def status_ledger(self, master=None):
        """A ledger of five unmeasured dispatches (three attempts at one task, one alone, one whose register was denied)
        and one measured; returns what `snapshot` shows."""
        state = self.work / ".orchestration"
        state.mkdir(exist_ok=True)
        rows = [
            {"job_id": "ctx_one", "decision": "ALLOW", "card": "task_t", "orchestration_task": "task_t"},
            {"job_id": "ctx_two", "decision": "ALLOW", "card": "task_t", "orchestration_task": "task_two"},
            {"job_id": "ctx_three", "decision": "ALLOW", "card": "task_t"},
            {"job_id": "ctx_denied", "decision": "DENY", "reason": "MODEL_TIER_ESCALATION"},
            {"job_id": "ctx_solo", "decision": "ALLOW", "card": "task_u"},
            {"job_id": "ctx_done", "decision": "ALLOW", "card": "task_v", "token_usage": {"source": "measured"}},
        ]
        (state / "dispatch-ledger.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows) + "not json\n", encoding="utf-8")
        if master:
            (state / "master").write_text(master, encoding="utf-8")
        return self.snapshot()

    def snapshot(self):
        """The ledger's bytes and the files under .orchestration."""
        state = self.work / ".orchestration"
        return (state / "dispatch-ledger.jsonl").read_bytes(), sorted(path.name for path in state.iterdir())

    def test_status_says_where_each_dispatch_stands_and_only_reads(self):
        before = self.status_ledger(master="term_test")
        each = {"ctx_one": "task task_t, attempt 1", "ctx_two": "task task_t, attempt 2", "ctx_three": "task task_t, attempt 3", "ctx_solo": "task task_u, attempt 1"}
        who = {name: f"dispatch {name} ({place} of 3)" for name, place in each.items()}
        denied = "dispatch ctx_denied (refused at register: MODEL_TIER_ESCALATION)"
        stop = "Its worker runs unaccepted: stop it with orca orchestration worker-stop --dispatch ctx_denied"
        last = "status: 1 earlier dispatch(es) are over and measured: ORCHESTRATION report lists them."
        run, shell = "run_test", json.dumps(["Working (12s)", "PS C:\\work>"])
        parked = Path(self.work / "frames.json")
        parked.write_text(json.dumps([{"parked": ["ctx_one", "ctx_two", "ctx_three", "ctx_solo", "ctx_denied"]}]), encoding="utf-8")
        # state: the environment that shows it, then (head, step) for every dispatch; {id} is the dispatch's.
        held = (
            ("live", {}, f" is open in {run}, and Orca shows its agent live.",
             f"Keep `ORCHESTRATION watch --run {run}` running; a message reaches it with: orca orchestration send --to dispatch:{{id}}"),
            ("parked", {"FAKE_ORCA_FRAMES": str(parked)}, f' is open in {run}, parked on a prompt that only a human can answer: {{"source": "hook", "since": 1794824861316}}.',
             "Read what it asks with orca orchestration worker-read --dispatch {id}, tell the owner, and do not answer it through its terminal."),
            ("ended", {"FAKE_ORCA_LIVENESS": "unverifiable"},
             f" is open in {run}, and its agent has ended while its terminal is kept (Claude's resume line, then a shell prompt):",
             "run ORCHESTRATION resume --dispatch {id}"),
            ("not live", {"FAKE_ORCA_LIVENESS": "unverifiable", "FAKE_ORCA_TAIL": shell},
             f" is open in {run}, and Orca does not show its agent live (liveness unverifiable), which alone proves nothing.",
             "Read its screen: orca orchestration worker-read --dispatch {id}"),
            ("unknown", {"FAKE_ORCA_OUTCOME": "paused"}, ": Orca gives it the outcome 'paused', which this wrapper does not know and reads as open.",
             "Look at it with `orca orchestration worker-show --dispatch {id}` and tell the owner."),
            ("unread", {"FAKE_ORCA_OUTCOME": "no answer"}, ": Orca gave no usable answer about it (nothing).",
             "See that `orca status` answers, then run this again."),
        )
        for name, environment, head, step in held:
            expected = ["status: master record: term_test (this terminal)"]
            expected += [f"status: {who[dispatch]}{head} {step.format(id=dispatch)}" for dispatch in ("ctx_one", "ctx_two", "ctx_three")]
            expected.append(f"status: {denied}{head[:-1] + '.' if head.endswith(':') else head} {stop}")
            expected.append(f"status: {who['ctx_solo']}{head} {step.format(id='ctx_solo')}")
            with self.subTest(name), mock.patch.dict(os.environ, environment):
                code, output = self.cli("status")
                self.assertEqual((code, output.splitlines()), (0, [*expected, last]))
        gone = (" is gone: Orca does not know it (dispatch_not_found), as after an `orca orchestration reset`. "
                "`ORCHESTRATION report` settles it as unmeasured, and it holds neither its contract nor its task.")
        over = {
            "succeeded": (f" has succeeded in {run}. ", f"Its report is a claim: verify, review and merge it. A fix is a new dispatch with --follows {{id}}. `ORCHESTRATION report` measures its usage."),
            "failed": (f" has failed in {run}. ", "Its task has had 3 attempts: stop, and tell the owner what was tried."),
        }
        for outcome, (head, step) in over.items():  # a refused dispatch whose state is over is not listed
            expected = ["status: master record: term_test (this terminal)", f"status: {who['ctx_one']}{head}ctx_three followed it.", f"status: {who['ctx_two']}{head}ctx_three followed it.",
                        f"status: {who['ctx_three']}{head}{step.format(id='ctx_three')}"]
            expected.append(f"status: {who['ctx_solo']}{head}" + (step.format(id="ctx_solo") if outcome == "succeeded" else "Dispatch its task again with --follows ctx_solo."))
            with self.subTest(outcome), mock.patch.dict(os.environ, {"FAKE_ORCA_OUTCOME": outcome}):
                self.assertEqual(self.cli("status"), (0, "\n".join([*expected, last]) + "\n"))
        with mock.patch.dict(os.environ, {"FAKE_ORCA_OUTCOME": "unknown dispatch"}):
            output = self.cli("status")[1].splitlines()
        self.assertEqual(output[1:-1], [f"status: {who[name]}{gone}" for name in ("ctx_one", "ctx_two", "ctx_three", "ctx_solo")])
        with mock.patch.dict(os.environ, {"FAKE_ORCA_WITHOUT": "dispatch.runId"}):  # an answer that names no Run
            line = self.cli("status")[1].splitlines()[1]
        self.assertIn("is open in a Run that Orca's answer does not name, and Orca shows its agent live. Keep a watch running on its Run, which `orca orchestration run-current --json` names; a message", line)
        # It only reads: the ledger, the folder, the gate and Orca's workers are as they were.
        self.assertEqual(self.snapshot(), before)
        asked = {tuple(call[:2]) for call in self.orca_calls()}
        self.assertEqual(asked, {("orchestration", "worker-show"), ("terminal", "read")})
        self.assertTrue(all("--full" in call and "--dispatch" in call for call in self.orca_calls() if call[1] == "worker-show"))
        with mock.patch.object(orchestration, "gate_run", side_effect=AssertionError("the gate was called")):
            self.assertEqual(self.cli("status")[0], 0)

    def test_status_names_the_master_record_and_needs_a_ledger(self):
        code, output = self.cli("status")  # a folder that dispatched nothing
        self.assertEqual(code, 2)
        self.assertEqual(output, f"orchestration: no dispatch ledger at {self.work / '.orchestration' / 'dispatch-ledger.jsonl'}; run this from the workspace root\n")
        self.assertFalse((self.work / ".orchestration").exists())
        self.assertEqual(self.orca_calls(), [])
        for master, own, record in (
            (None, "term_test", "none"),
            ("term_test", "term_test", "term_test (this terminal)"),
            ("term_old", "term_test", "term_old (not this terminal, which is term_test)"),
            ("term_old", None, "term_old (not this terminal, which is no Orca terminal)"),
        ):
            (self.work / ".orchestration" / "master").unlink(missing_ok=True)
            self.status_ledger(master=master)
            with self.subTest(record), mock.patch.dict(os.environ, {"ORCA_TERMINAL_HANDLE": own or ""}):
                if own is None:
                    os.environ.pop("ORCA_TERMINAL_HANDLE")
                self.assertEqual(self.cli("status")[1].splitlines()[0], f"status: master record: {record}")
        os.environ["ORCA_CLI_COMMAND"] = str(self.work / "no-such-orca.exe")  # an Orca CLI that cannot be started
        code, output = self.cli("status")
        self.assertEqual(code, 2)
        self.assertNotIn("status: master", output)


    def test_a_held_dispatch_line_carries_the_state_of_the_dispatch_that_holds_it(self):
        self.assertEqual(self.dispatch()[0], 0)
        for outcome, state in (("in_progress", "open"), ("paused", "unknown"), ("no answer", "unread")):
            with self.subTest(outcome), mock.patch.dict(os.environ, {"FAKE_ORCA_OUTCOME": outcome}):
                code, output = self.dispatch()
                line = json.loads(output.splitlines()[0])
                self.assertEqual((code, line["reason"], line["dispatch"], line["state"]), (2, "CONTRACT_ALREADY_RUNNING", "ctx_test", state))
                self.assertEqual(line["outcome"], None if outcome == "no answer" else outcome)
        self.assertEqual(len(self.worker_starts()), 1)

    def test_no_worker_is_started_while_one_runs_that_the_gate_refused_at_register(self):
        os.environ["FAKE_ORCA_WORKER_MODEL"] = versioned(TOP)
        self.assertEqual(self.dispatch()[0], 2)  # refused at register, and its worker runs all the same
        os.environ.update({"FAKE_ORCA_WORKER_MODEL": versioned(EFFICIENT), "FAKE_ORCA_DISPATCH": "other"})
        other = self.work / "other.md"  # another contract: the refused row names none, so it holds every one
        other.write_text(Path(self.contract()).read_text(encoding="utf-8") + "\nAnother task.\n", encoding="utf-8")
        again = ("dispatch", "--contract", str(other), "--agent", "claude", "--model", EFFICIENT, "--effort", "low")
        rows = len(self.ledger())
        for outcome, state in (("in_progress", "open"), ("paused", "unknown"), ("no answer", "unread")):
            with self.subTest(outcome), mock.patch.dict(os.environ, {"FAKE_ORCA_OUTCOME": outcome}):
                code, output = self.cli(*again)
                self.assertEqual(code, 2, output)
                line = json.loads(output.splitlines()[0])
                self.assertEqual((line["reason"], line["dispatch"], line["state"]), ("UNACCEPTED_WORKER_RUNNING", "ctx_test", state))
                self.assertIn("orca orchestration worker-stop --dispatch ctx_test", output)
                # Only an open one is known to run: the others are to be looked at first.
                self.assertIn("Orca shows it open" if state == "open" else "orca orchestration worker-show --dispatch ctx_test", output)
                self.assertEqual((len(self.worker_starts()), len(self.ledger())), (1, rows))  # refused before the gate
        # Stopped: `orca orchestration worker-stop` left `failed` on each of 5 scratch workers (Orca 1.4.259, 2026-11-16).
        os.environ["FAKE_ORCA_OUTCOME"] = "failed"
        code, output = self.cli(*again)
        self.assertEqual((code, json.loads(output)["dispatch"]), (0, "ctx_other"), output)

    def test_the_owner_ends_the_hold_of_a_refused_dispatch_only_while_orca_shows_its_agent_exited(self):
        os.environ["FAKE_ORCA_WORKER_MODEL"] = versioned(TOP)
        self.assertEqual(self.dispatch()[0], 2)  # ctx_test is refused at register
        os.environ.update({"FAKE_ORCA_WORKER_MODEL": versioned(EFFICIENT), "FAKE_ORCA_DISPATCH": "other"})
        other = self.work / "other.md"
        other.write_text(Path(self.contract()).read_text(encoding="utf-8") + "\nAnother task.\n", encoding="utf-8")
        again = ("dispatch", "--contract", str(other), "--agent", "claude", "--model", EFFICIENT, "--effort", "low")
        why = "the owner: stopped by hand, and Orca calls it paused"
        override = ("override-hold", "--dispatch", "ctx_test", "--reason", why)
        record = self.work / ".orchestration" / "hold-overrides.jsonl"
        stuck = {"FAKE_ORCA_OUTCOME": "paused", "FAKE_ORCA_LIVENESS": "exited"}  # stopped, under an outcome nobody knows
        refusals = (  # what Orca shows, and why the hold is not ended for it
            ({"FAKE_ORCA_OUTCOME": "in_progress", "FAKE_ORCA_LIVENESS": "exited"}, "WORKER_OPEN"),
            ({"FAKE_ORCA_OUTCOME": "no answer"}, "ORCA_UNREAD"),
            ({"FAKE_ORCA_OUTCOME": "other refusal"}, "ORCA_UNREAD"),
            ({"FAKE_ORCA_OUTCOME": "paused", "FAKE_ORCA_LIVENESS": "live"}, "AGENT_NOT_EXITED"),
            ({"FAKE_ORCA_OUTCOME": "paused", "FAKE_ORCA_LIVENESS": "unverifiable"}, "AGENT_NOT_EXITED"),
            ({"FAKE_ORCA_OUTCOME": "failed"}, "NOTHING_HELD"),
            ({"FAKE_ORCA_OUTCOME": "unknown dispatch"}, "NOTHING_HELD"),
        )
        for environment, reason in refusals:
            with self.subTest(reason, **environment), mock.patch.dict(os.environ, environment):
                code, output = self.cli(*override)
                self.assertEqual((code, json.loads(output.splitlines()[0])["reason"]), (2, reason), output)
                self.assertFalse(record.exists())
        with mock.patch.dict(os.environ, stuck):
            calls = len(self.orca_calls())
            code, output = self.cli("override-hold", "--dispatch", "ctx_nobody", "--reason", why)  # no refused row names it
            self.assertEqual((code, json.loads(output.splitlines()[0])["reason"], len(self.orca_calls())), (2, "NOT_REFUSED", calls), output)
            self.assertEqual(self.cli("override-hold", "--dispatch", "ctx_test", "--reason", " ")[0], 2)  # no reason given
            self.assertFalse(record.exists())
            record.write_text(json.dumps({"job_id": "ctx_test"}) + "\n", encoding="utf-8")  # by hand, without a reason: none
            code, output = self.cli(*again)
            self.assertEqual((code, json.loads(output.splitlines()[0])["reason"]), (2, "UNACCEPTED_WORKER_RUNNING"), output)
            self.assertIn('tell the owner. Only on the owner\'s word, ORCHESTRATION override-hold --dispatch ctx_test --reason "<the owner\'s reason>" ends that hold.', self.cli("status")[1])
            record.unlink()
            code, output = self.cli("override-hold", "--dispatch", "ctx_test", "--reason", why.replace(", ", ",\n\t"))
            self.assertEqual((code, json.loads(output)["allow"]), (0, True), output)
            kept = json.loads(record.read_text(encoding="utf-8"))  # one record, with the reason on one line
            self.assertEqual((kept["job_id"], kept["reason"], kept["outcome"], kept["terminal"]), ("ctx_test", why, "paused", "term_test"))
            self.assertIn(f"The owner ended its hold ({why}): it holds no dispatch while Orca shows its agent exited.", self.cli("status")[1])
        # The record passes a dispatch only while Orca shows that again: never an open one, an unread one or a live agent.
        still = (
            ({"FAKE_ORCA_OUTCOME": "in_progress", "FAKE_ORCA_LIVENESS": "exited"}, "open"),
            ({"FAKE_ORCA_OUTCOME": "no answer"}, "unread"),
            ({"FAKE_ORCA_OUTCOME": "paused", "FAKE_ORCA_LIVENESS": "live"}, "unknown"),
        )
        for environment, state in still:
            with self.subTest(state, **environment), mock.patch.dict(os.environ, environment):
                code, output = self.cli(*again)
                line = json.loads(output.splitlines()[0])
                self.assertEqual((code, line["reason"], line["dispatch"], line["state"]), (2, "UNACCEPTED_WORKER_RUNNING", "ctx_test", state))
                self.assertIn("Its worker runs unaccepted: stop it with orca orchestration worker-stop --dispatch ctx_test", self.cli("status")[1])
        self.assertEqual(len(self.worker_starts()), 1)
        with mock.patch.dict(os.environ, stuck):
            code, output = self.cli(*again)
            self.assertEqual((code, json.loads(output)["dispatch"]), (0, "ctx_other"), output)
            # It ends the hold of a refused dispatch and no other: a registered one keeps its contract, whatever the file says.
            code, output = self.cli("override-hold", "--dispatch", "ctx_other", "--reason", why)
            self.assertEqual((code, json.loads(output.splitlines()[0])["reason"]), (2, "NOT_REFUSED"), output)
            with record.open("a", encoding="utf-8") as forged:
                forged.write(json.dumps({"job_id": "ctx_other", "reason": "written by hand"}) + "\n")
            code, output = self.cli(*again)
            self.assertEqual((code, json.loads(output.splitlines()[0])["reason"]), (2, "CONTRACT_ALREADY_RUNNING"), output)
        self.assertEqual(len(self.worker_starts()), 2)

    def test_a_dispatch_refused_before_the_gate_leaves_no_state_folder_behind(self):
        project = self.ready("ready")
        code, output = self.cli("dispatch", "--contract", "no-such.md", "--agent", "claude", "--model", EFFICIENT, "--effort", "low", "--project", str(project))
        self.assertEqual(code, 2, output)
        self.assertIn("no contract at", output)
        self.assertFalse((self.work / ".orchestration").exists())  # the look for a refused worker reads a ledger, and makes none

    def test_a_worker_row_without_a_terminal_handle_is_not_ended_and_orca_is_not_asked(self):
        calls = len(self.orca_calls())
        self.assertIsNone(orchestration.agent_ended(None))
        self.assertEqual(len(self.orca_calls()), calls)


if __name__ == "__main__":
    unittest.main()
