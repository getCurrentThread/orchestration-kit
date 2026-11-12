"""Checks of scripts/harness.py and of the harness tree it installs.

Run from the repository root: python -X utf8 -s -B -m unittest discover -s tests -v
"""

import contextlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import harness  # noqa: E402

FILES = harness.sources()
ROUTERS = ("CLAUDE.md", "AGENTS.md", ".cursorrules", ".windsurfrules")


def tree(folder):
    """Every file under a folder with its bytes, to prove that a run left it alone."""
    return {path.relative_to(folder).as_posix(): path.read_bytes() for path in folder.rglob("*") if path.is_file()}


class HarnessTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(temporary.cleanup)
        self.target = Path(temporary.name).resolve() / "repo"
        # The tool check reports this machine, not the code under test.
        patcher = mock.patch.object(harness, "missing_tools", return_value=[])
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_harness(self, *arguments):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            code = harness.main([str(self.target), *arguments])
        return code, output.getvalue()

    def test_harness_tree_is_consistent(self):
        # LF throughout, or git's line ending setting on this machine would decide what a target receives.
        self.assertEqual([name for name, path in FILES.items() if b"\r\n" in path.read_bytes()], [])
        self.assertEqual(len({FILES[name].read_bytes() for name in ROUTERS}), 1)
        # Codex runs the same hooks as Claude Code, from its own copies.
        for name in [name for name in FILES if name.startswith(".claude/hooks/")]:
            self.assertEqual(FILES[name.replace(".claude/", ".codex/", 1)].read_bytes(), FILES[name].read_bytes(), name)
        for wiring in (".claude/settings.json", ".codex/hooks.example.json"):
            for name in re.findall(r"\.(?:claude|codex)/hooks/[\w.-]+", FILES[wiring].read_text(encoding="utf-8")):
                self.assertIn(name, FILES, f"{wiring} names a hook that is not in the tree")
        # A hook runs as shipped: no bootstrap step names one, so a placeholder in it is printed into every session.
        self.assertEqual([name for name in FILES if "hooks/" in name and b"{{" in FILES[name].read_bytes()], [])
        # Claude Code asks before it starts a project's MCP server unless the project settings approve it.
        settings = json.loads(FILES[".claude/settings.json"].read_text(encoding="utf-8"))
        servers = json.loads(FILES[".mcp.json"].read_text(encoding="utf-8"))["mcpServers"]
        self.assertEqual(sorted(settings["enabledMcpjsonServers"]), sorted(servers))
        # These would land at the root of someone else's project.
        self.assertFalse({"LICENSE", "README.md"} & set(FILES))

    @unittest.skipUnless(harness.git_bash(), "needs the bash of Git for Windows")
    def test_emoji_guard_decides_without_jq(self):
        hook = FILES[".claude/hooks/no-emoji-check.sh"]
        self.assertNotRegex(hook.read_text(encoding="utf-8"), r"(?m)(^|\|)\s*jq\s")

        def run_hook(payload):
            return subprocess.run([str(harness.git_bash()), hook.as_posix()], input=payload.encode("utf-8"), capture_output=True)

        def decision(content, path="notes.md"):
            done = run_hook(json.dumps({"tool_input": {"file_path": path, "content": content}}))
            self.assertEqual(done.returncode, 0, done.stderr)
            return json.loads(done.stdout)["hookSpecificOutput"]["permissionDecision"] if done.stdout.strip() else None

        self.assertEqual(decision("launch \U0001F680"), "deny")
        # Arrows and the multiplication sign are allowed on purpose.
        self.assertIsNone(decision("café → 3 × 4"))
        # Half of a surrogate pair, as a cut-off emoji leaves it, is no emoji and must not stop the hook.
        self.assertIsNone(decision("cut off \ud83d"))
        # Both copies of the hook hold the emoji ranges, and both must stay editable.
        self.assertIsNone(decision("\U0001F680", path="repo/.codex/hooks/no-emoji-check.sh"))
        # Exit 2 is the code Claude Code blocks a tool call on: a hook that cannot decide must not let the write pass.
        self.assertEqual(run_hook("not json").returncode, 2)

    def test_empty_target_gets_the_whole_harness_and_a_second_run_changes_nothing(self):
        code, output = self.run_harness()
        self.assertEqual(code, 0, output)
        self.assertIn(f"Added ({len(FILES)} file(s))", output)
        installed = tree(self.target)
        self.assertEqual(installed, {name: path.read_bytes() for name, path in FILES.items()})

        code, output = self.run_harness()
        self.assertEqual(code, 0, output)
        self.assertIn("Added (0 file(s))", output)
        self.assertIn(f"identical ({len(FILES)} file(s))", output)
        self.assertEqual(tree(self.target), installed)

    def test_existing_files_are_never_overwritten(self):
        (self.target / ".agent").mkdir(parents=True)
        mine = {
            "CLAUDE.md": b"my router\n",
            ".agent/Context.md": b"my context\n",
            ".gitignore": b"my ignores\n",
            ".gitignore.starter": b"an earlier staged copy\n",
            # The same content as the harness's, as git checks it out with core.autocrlf.
            "AGENTS.md": FILES["AGENTS.md"].read_bytes().replace(b"\n", b"\r\n"),
        }
        for name, content in mine.items():
            (self.target / name).write_bytes(content)

        code, output = self.run_harness()
        self.assertEqual(code, 0, output)
        after = tree(self.target)
        for name, content in mine.items():
            self.assertEqual(after[name], content, name)
        self.assertEqual(after["CLAUDE.md.starter"], FILES["CLAUDE.md"].read_bytes())
        self.assertEqual(after[".agent/Context.md.starter"], FILES[".agent/Context.md"].read_bytes())
        self.assertNotIn("AGENTS.md.starter", after)
        self.assertIn("Staged as *.starter, merge by hand (2 file(s))", output)
        self.assertIn("! .gitignore.starter", output)

    def test_dry_run_writes_nothing_and_reports_what_a_run_does(self):
        code, output = self.run_harness("--dry-run")
        self.assertEqual(code, 0, output)
        self.assertFalse(self.target.exists())

        self.target.mkdir()
        (self.target / "CLAUDE.md").write_bytes(b"my router\n")
        before = tree(self.target)
        _, planned = self.run_harness("--dry-run")
        self.assertEqual(tree(self.target), before)
        _, done = self.run_harness()
        groups = [line for line in done.splitlines() if line.startswith("  ") and line[2] in "+~=!"]
        self.assertEqual([line for line in planned.splitlines() if line.startswith("  ") and line[2] in "+~=!"], groups)
        self.assertIn("  ~ CLAUDE.md.starter", groups)

    def test_a_file_where_a_folder_belongs_stops_the_run_before_any_write(self):
        self.target.mkdir()
        (self.target / "docs").write_bytes(b"a file, not a folder\n")
        for arguments in (["--dry-run"], []):
            code, output = self.run_harness(*arguments)
            self.assertEqual(code, 2, output)
            self.assertIn("docs", output)
            self.assertEqual(tree(self.target), {"docs": b"a file, not a folder\n"})

    def test_the_harness_source_is_refused_as_a_target(self):
        before = tree(harness.SOURCE)
        for self.target in (harness.SOURCE, harness.SOURCE / "docs"):
            code, output = self.run_harness()
            self.assertEqual(code, 2, output)
        self.assertEqual(tree(harness.SOURCE), before)

    @unittest.skipUnless(harness.git_bash(), "needs the bash of Git for Windows")
    def test_setup_keeps_an_existing_hook_chain(self):
        def setup():
            done = subprocess.run(
                [str(harness.git_bash()), "scripts/setup.sh"], cwd=self.target, capture_output=True, text=True, encoding="utf-8"
            )
            self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
            self.assertTrue((self.target / ".setup-done").is_file())
            hooks_path = subprocess.run(
                ["git", "config", "--local", "--get", "core.hooksPath"], cwd=self.target, capture_output=True, text=True
            ).stdout.strip()
            return hooks_path, done.stdout

        self.run_harness()
        (self.target / "lefthook.yml").write_text("pre-commit: {}\n", encoding="utf-8")
        self.assertEqual(setup()[0], "")
        (self.target / "lefthook.yml").unlink()
        # Git stops running a hook in .git/hooks once the path moves, so setup has to name it.
        (self.target / ".git/hooks/pre-push").write_bytes(b"#!/bin/sh\n")
        hooks_path, output = setup()
        self.assertEqual(hooks_path, ".githooks")
        self.assertRegex(output, r"no longer run[\s\S]*hooks/pre-push")

    @unittest.skipUnless(harness.git_bash(), "needs the bash of Git for Windows")
    def test_a_commit_takes_everything_its_hooks_change(self):
        # A hook that edits a file after git has built the commit leaves that edit staged behind it.
        self.run_harness()
        for command in (
            ["init", "-q"], ["config", "user.name", "tester"], ["config", "user.email", "tester@example.invalid"],
            ["config", "commit.gpgsign", "false"], ["config", "core.hooksPath", ".githooks"], ["add", "-A"],
            ["commit", "-q", "-m", "Install the harness"],
        ):
            done = subprocess.run(["git", *command], cwd=self.target, capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        status = subprocess.run(["git", "status", "--porcelain"], cwd=self.target, capture_output=True, text=True)
        self.assertEqual(status.stdout, "")

    @unittest.skipUnless(harness.git_bash(), "needs the bash of Git for Windows")
    def test_a_commit_without_docs_still_reaches_the_secret_scan(self):
        self.run_harness()
        tools = self.target.parent / "tools"
        tools.mkdir()
        # Stand-ins: the hook only asks whether gitleaks exists, and the scan reports a finding.
        (tools / "gitleaks").write_bytes(b"#!/usr/bin/env bash\nexit 0\n")
        (self.target / "scripts/redaction-scan.sh").write_bytes(b"#!/usr/bin/env bash\necho scan ran\nexit 1\n")
        (self.target / "app.js").write_bytes(b"let a = 1\n")
        for command in (["init", "-q"], ["config", "user.name", "tester"], ["add", "app.js"]):
            subprocess.run(["git", *command], cwd=self.target, check=True, capture_output=True)
        done = subprocess.run(
            [str(harness.git_bash()), ".githooks/pre-commit"], cwd=self.target, capture_output=True, text=True,
            env={**os.environ, "PATH": f"{tools}{os.pathsep}{os.environ['PATH']}"},
        )
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("scan ran", done.stdout)
        self.assertIn("[redaction-scan blocked]", done.stdout)


if __name__ == "__main__":
    unittest.main()
