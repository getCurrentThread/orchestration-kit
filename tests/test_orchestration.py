"""End-to-end checks of scripts/orchestration.py: the real vendored gate against a fake Orca CLI.

Run from the repository root: python -X utf8 -s -B -m unittest discover -s tests -v
"""

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import orchestration  # noqa: E402

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
            "ORCA_TERMINAL_HANDLE": "term_test",
            "FAKE_ORCA_LOG": str(self.log),
            "FAKE_ORCA_WORKTREE": self.work.as_posix(),
            "FAKE_ORCA_WORKER_MODEL": "claude-haiku-5-5",
        }
        patcher = mock.patch.dict(os.environ, environment)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(os.chdir, Path.cwd())
        os.chdir(self.work)

    def contract(self, tier="efficient"):
        path = self.work / "contract.md"
        path.write_text(f"---\ntier: {tier}\nruntime: claude\nrepo: demo\n---\nReply READY.\n", encoding="utf-8")
        return str(path)

    def cli(self, *arguments):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            try:
                code = orchestration.main(list(arguments))
            except SystemExit as stop:  # argparse rejects bad options this way
                code = stop.code
        return code, output.getvalue()

    def dispatch(self, model="claude-haiku-5-5", effort="low", tier="efficient"):
        return self.cli("dispatch", "--contract", self.contract(tier), "--agent", "claude", "--model", model, "--effort", effort)

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
        rows = self.ledger()
        self.assertEqual([row["decision"] for row in rows], ["ALLOW", "ALLOW"])
        self.assertEqual(rows[1]["job_id"], "ctx_test")
        self.assertEqual(rows[1]["model_measured"], "claude-haiku-5-5")
        self.assertTrue(rows[1]["model_verified"])

    def test_refuses_outside_an_orca_terminal(self):
        del os.environ["ORCA_TERMINAL_HANDLE"]
        code, output = self.dispatch()
        self.assertEqual(code, 2)
        self.assertIn("ORCA_TERMINAL_HANDLE", output)
        self.assertEqual(self.orca_calls(), [])

    def test_refuses_a_model_missing_from_the_policy(self):
        code, output = self.dispatch(model="claude-fable-5-2")
        self.assertEqual(code, 2)
        self.assertIn("not listed", output)
        self.assertEqual(self.orca_calls(), [])

    def test_refuses_max_effort_and_top_tier_above_medium(self):
        self.assertEqual(self.dispatch(effort="max")[0], 2)
        code, output = self.dispatch(model="claude-opus-5-5", effort="high", tier="top")
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
        os.environ["FAKE_ORCA_WORKER_MODEL"] = "claude-opus-5-5"
        code, output = self.dispatch()
        self.assertEqual(code, 2)
        self.assertIn("MODEL_TIER_ESCALATION", output)
        self.assertIn("ctx_test", output)

    def test_succeed_needs_every_handoff_section_before_launching(self):
        handoff = self.work / "handoff.md"
        handoff.write_text("## Role State\n", encoding="utf-8")
        launch = ["succeed", "--handoff", str(handoff), "--model", "claude-sonnet-5-5", "--effort", "high"]
        code, output = self.cli(*launch)
        self.assertEqual(code, 2)
        self.assertIn("Observed Baseline", output)
        self.assertEqual(self.orca_calls(), [])
        handoff.write_text("".join(f"## {heading}\n\n" for heading in orchestration.HANDOFF_HEADINGS), encoding="utf-8")
        code, output = self.cli(*launch, "--run", "run_test")
        self.assertEqual(code, 0, output)
        call = self.orca_calls()[0]
        self.assertEqual(call[:2], ["agent", "launch"])
        self.assertEqual(call[call.index("--effort") + 1], "high")

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
