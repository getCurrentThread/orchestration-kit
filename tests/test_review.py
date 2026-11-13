"""Runs the review workflow's own script under Node, with scripted agents in place of the Workflow runtime."""

import json
import shutil
import subprocess
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "review" / "orchestration-review.workflow.js"

# The Workflow runtime gives a script `args`, `agent`, `parallel` and `log`, and lets it `return` at the top level.
# A reviewer that is not scripted reads the whole diff and finds nothing.
RUNNER = """
const fs = require('fs');
const [script, args, answers] = [process.argv[1], JSON.parse(process.argv[2]), JSON.parse(process.argv[3])];
const prompts = [];
const agent = async (prompt, options) => {
  prompts.push({ label: options.label, prompt });
  const scripted = Object.keys(answers).find(prefix => options.label.startsWith(prefix));
  return scripted ? answers[scripted] : { verdict: 'APPROVE', linesRead: args.diffLines, findings: [] };
};
const parallel = thunks => Promise.all(thunks.map(thunk => thunk().catch(() => null)));
const body = fs.readFileSync(script, 'utf8').replace('export const meta', 'const meta');
new Function('args', 'agent', 'parallel', 'log', `return (async () => { ${body} })()`)(args, agent, parallel, () => {}).then(
  result => console.log(JSON.stringify({ result, prompts })),
  error => console.log(JSON.stringify({ error: error.message }))
);
"""

DIFF = {"diffFile": "C:/work/review.diff", "diffLines": 4321}
FOUND = {
    "verdict": "CHANGES_REQUESTED",
    "linesRead": 4321,
    "findings": [{"title": "t", "severity": "HIGH", "file": "a.py", "line": 1, "evidence": "x = 1", "proof": "p"}],
}


@unittest.skipUnless(shutil.which("node"), "needs Node to run the workflow script")
class ReviewWorkflowTest(unittest.TestCase):
    def review(self, args: dict, answers: dict | None = None) -> dict:
        command = ["node", "-e", RUNNER, str(SCRIPT), json.dumps(args), json.dumps(answers or {})]
        done = subprocess.run(command, capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(done.returncode, 0, done.stderr)
        return json.loads(done.stdout)

    def test_a_clean_diff_is_approved_by_reviewers_that_read_the_file(self):
        out = self.review({**DIFF, "language": "python"})
        self.assertEqual(out["result"]["verdict"], "APPROVE")
        # An approval without a finding shows that the diff was read, with no look into the journal.
        self.assertEqual(out["result"]["read"], {"diffLines": 4321, "quality": 4321, "lang:python": 4321, "silent-failures": 4321})
        self.assertEqual([sent["label"] for sent in out["prompts"]], ["review:quality", "review:lang:python", "review:silent-failures"])
        for sent in out["prompts"]:
            self.assertIn("The diff is the file C:/work/review.diff", sent["prompt"])
            self.assertNotIn("4321", sent["prompt"])  # a reviewer that is told the count can repeat it unread

    def test_a_blocking_finding_goes_to_a_verifier_that_reads_the_same_file(self):
        out = self.review(DIFF, {"review:quality": FOUND, "verify:": {"isReal": True, "confidence": 0.9, "reasoning": "r"}})
        self.assertEqual((out["result"]["verdict"], len(out["result"]["blocking"])), ("CHANGES_REQUESTED", 1))
        verifier = out["prompts"][-1]
        self.assertTrue(verifier["label"].startswith("verify:a.py"))
        self.assertIn("the diff in the file C:/work/review.diff", verifier["prompt"])

    def test_a_change_is_checked_against_the_contract_it_was_made_for(self):
        def judged(*statuses):
            rows = [{"requirement": f"requirement {number}", "status": status, "evidence": "e"} for number, status in enumerate(statuses)]
            return {"verdict": "APPROVE", "linesRead": 4321, "findings": [], "requirements": rows}

        contract = {**DIFF, "contractFile": "C:/work/contract.md"}
        out = self.review(contract, {"review:contract": judged("met", "unjudged")})
        self.assertEqual(out["result"]["verdict"], "APPROVE")
        self.assertEqual([row["status"] for row in out["result"]["contract"]], ["met", "unjudged"])  # what was checked, for the caller
        reviewer = next(sent for sent in out["prompts"] if sent["label"] == "review:contract")
        for named in ("The contract is the file C:/work/contract.md", "the unified diff in the file C:/work/review.diff"):
            self.assertIn(named, reviewer["prompt"])
        self.assertIsNone(self.review(DIFF)["result"]["contract"])  # no contract was named, so nothing was checked against one
        # A clean diff that does not do what was asked: the requirement blocks, whatever verdict the reviewer gave.
        real = {"isReal": True, "confidence": 0.9, "reasoning": "r"}
        out = self.review(contract, {"review:contract": judged("met", "unmet"), "verify:": real})
        blocking = out["result"]["blocking"]
        self.assertEqual((out["result"]["verdict"], [found["evidence"] for found in blocking]), ("CHANGES_REQUESTED", ["requirement 1"]))
        self.assertIn("The contract is the file C:/work/contract.md: read it as well", out["prompts"][-1]["prompt"])
        # A reviewer that names no requirement has not read the contract, and one that died has checked nothing.
        for lost, error in ((judged(), "named no requirement of the contract"), (None, "agent returned null (terminal failure or skip)")):
            result = self.review(contract, {"review:contract": lost})["result"]
            self.assertEqual((result["verdict"], result["failedDimensions"]), ("CHANGES_REQUESTED", [{"dimension": "contract", "error": error}]))
        self.assertIn("contractFile", self.review({**DIFF, "contractFile": ""})["error"])

    def test_the_gate_fails_closed(self):
        self.assertIn("diffFile", self.review({"diff": "+ a line", "diffLines": 1})["error"])  # a diff pasted as text is not taken
        self.assertIn("diffLines", self.review({"diffFile": "d", "diffLines": 0})["error"])  # nor an empty or uncounted file
        lost = self.review(DIFF, {"review:silent-failures": None})["result"]  # a reviewer that died
        self.assertEqual((lost["verdict"], lost["incomplete"]), ("CHANGES_REQUESTED", True))
        self.assertEqual((lost["read"]["quality"], lost["read"]["silent-failures"]), (4321, None))
        # A reviewer that approved after the first part of the file, or without the file, did not review the diff.
        for read in (4320, 0):
            short = self.review(DIFF, {"review:quality": {"verdict": "APPROVE", "linesRead": read, "findings": []}})["result"]
            self.assertEqual((short["verdict"], short["stats"]["failed"]), ("CHANGES_REQUESTED", 1))
            self.assertEqual(short["failedDimensions"], [{"dimension": "quality", "error": f"read {read} of the diff's 4321 lines"}])
        unsure = {"isReal": False, "confidence": 0.5, "reasoning": "r"}
        kept = self.review(DIFF, {"review:quality": FOUND, "verify:": unsure})["result"]
        self.assertEqual((kept["verdict"], len(kept["blocking"])), ("CHANGES_REQUESTED", 1))
        cleared = self.review(DIFF, {"review:quality": FOUND, "verify:": {**unsure, "confidence": 0.9}})["result"]
        self.assertEqual((cleared["verdict"], len(cleared["advisory"])), ("APPROVE", 1))


if __name__ == "__main__":
    unittest.main()
