"""End-to-end checks of scripts/orchestration.py: the real vendored gate against a fake Orca CLI.

Run from the repository root: python -X utf8 -s -B -m unittest discover -s tests -v
"""

import contextlib
import io
import json
import msvcrt
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import orchestration  # noqa: E402

# Families come from the policy, so the tests name no model and survive a change of the families listed there.
POLICY_TIERS = json.loads(orchestration.POLICY.read_text(encoding="utf-8"))["tiers"]
EFFICIENT, TOP = POLICY_TIERS["efficient"][0], POLICY_TIERS["top"][0]


def versioned(family):
    """A full model id of a family, as a worker transcript reports it. The version is made up on purpose."""
    return f"claude-{family}-9-9"


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

    def contract(self, tier="efficient", runtime="claude"):
        path = self.work / "contract.md"
        text = f"---\ntier: {tier}\nruntime: {runtime}\nrepo: demo\n---\n\n# Say READY\n\nReply READY.\n"
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

    def dispatch(self, model=EFFICIENT, effort="low", tier="efficient", agent="claude"):
        launch = ["--model", model] if model else []
        return self.cli("dispatch", "--contract", self.contract(tier, agent), "--agent", agent, *launch, "--effort", effort)

    def started_model(self):
        start = self.worker_starts()[-1]
        return start[start.index("--model") + 1]

    def orca_calls(self):
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines()]

    def worker_starts(self):
        return [call for call in self.orca_calls() if call[:2] == ["orchestration", "worker-start"]]

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

    def test_a_versioned_id_falls_under_its_family(self):
        self.assertEqual(orchestration.family(versioned(TOP)), TOP)
        self.assertEqual(orchestration.family(TOP.upper()), TOP)
        self.assertIsNone(orchestration.family("unlisted-model"))
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

    def test_refuses_outside_an_orca_terminal(self):
        del os.environ["ORCA_TERMINAL_HANDLE"]
        code, output = self.dispatch()
        self.assertEqual(code, 2)
        self.assertIn("ORCA_TERMINAL_HANDLE", output)
        self.assertEqual(self.orca_calls(), [])

    def test_refuses_a_model_missing_from_the_policy(self):
        code, output = self.dispatch(model="unlisted-model")
        self.assertEqual(code, 2)
        self.assertIn("not listed", output)
        self.assertEqual(self.dispatch(model=None)[0], 2)  # only codex has a default model
        self.assertEqual(self.orca_calls(), [])

    def test_refuses_max_effort_and_top_tier_above_medium(self):
        self.assertEqual(self.dispatch(effort="max")[0], 2)
        code, output = self.dispatch(model=TOP, effort="high", tier="top")
        self.assertEqual(code, 2)
        self.assertIn("medium", output)
        self.assertEqual(self.orca_calls(), [])

    def test_gate_denial_starts_no_worker(self):
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
        self.assertNotIn("restarted", output)

    def test_watch_gives_up_on_what_waiting_cannot_mend(self):
        output = self.watch([
            {"runtime": None},  # Orca being down is waited out, however long
            {"runtime": "one", "slow": True},  # and so is an Orca that is up and gives no answer about the Run
            {"runtime": "one", "refuses": True, "unread": 0},
        ], ends=2)
        self.assertIn('Orca has refused to show run_test 5 times in a row, so nothing watches it. Its answer: {"code": "invalid_argument"}', output)
        self.assertEqual(sum(call == ["status", "--json"] for call in self.orca_calls()), 7)
        os.environ["ORCA_CLI_COMMAND"] = str(self.work / "no-such-orca.exe")
        self.assertIn("cannot be run", self.watch([], ends=2))

    def test_a_second_watch_of_the_same_run_ends_at_once(self):
        state = self.work / ".orchestration"
        state.mkdir()
        with open(state / "watch-term_test-run_test.lock", "a+b") as held:
            msvcrt.locking(held.fileno(), msvcrt.LK_NBLCK, 1)
            self.assertIn("already have a watch", self.watch([]))
        self.assertEqual(self.orca_calls(), [])

    def test_succeed_needs_every_handoff_section_before_launching(self):
        handoff = self.work / "handoff.md"
        handoff.write_text("## Role State\n", encoding="utf-8")
        launch = ["succeed", "--handoff", str(handoff), "--model", EFFICIENT, "--effort", "high"]
        code, output = self.cli(*launch)
        self.assertEqual(code, 2)
        self.assertIn("Observed Baseline", output)
        self.assertEqual(self.orca_calls(), [])
        handoff.write_text("".join(f"## {heading}\n\n" for heading in orchestration.HANDOFF_HEADINGS), encoding="utf-8")
        del os.environ["ORCA_TERMINAL_HANDLE"]  # a master outside Orca cannot dispatch, and this is its way in
        code, output = self.cli(*launch, "--run", "run_test")
        self.assertEqual(code, 0, output)
        call = self.orca_calls()[0]
        self.assertEqual(call[:2], ["agent", "launch"])
        self.assertEqual(call[call.index("--effort") + 1], "high")
        self.assertEqual(call[call.index("--model") + 1], versioned(EFFICIENT))

    def test_inherit_appends_one_generation_per_succession(self):
        handoff = self.work / "handoff.md"
        handoff.write_text("handoff", encoding="utf-8")
        for _ in range(2):
            code, output = self.cli("inherit", "--handoff", str(handoff), *LINEAGE_ARGUMENTS)
            self.assertEqual(code, 0, output)
        lineage = (self.work / ".orchestration" / "lineage.md").read_text(encoding="utf-8")
        self.assertIn("## Gen 1 ", lineage)
        self.assertIn("## Gen 2 ", lineage)
        self.assertIn("- **Verification**: **PASS**", lineage)


if __name__ == "__main__":
    unittest.main()
