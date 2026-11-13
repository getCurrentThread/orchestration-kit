"""Checks of scripts/harness.py and of the harness tree it installs.

Run from the repository root: python -X utf8 -s -B -m unittest discover -s tests -v
"""

import contextlib
import fnmatch
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
        # The command that stages the executable bit names every script and hook, and nothing that is not there:
        # git refuses a pattern that matches no file.
        patterns = harness.STAGE_EXECUTABLE.split()[3:]
        code = [name for name in FILES if name.endswith(".sh") or name.startswith(".githooks/")]
        self.assertEqual([name for name in code if not any(fnmatch.fnmatchcase(name, pattern) for pattern in patterns)], [])
        self.assertEqual([pattern for pattern in patterns if not fnmatch.filter(code, pattern)], [])

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

    def test_a_gitignore_of_the_projects_own_gets_the_lines_it_lacks(self):
        self.target.mkdir()
        mine = b"dist\n/build/\n.env\n"
        (self.target / ".gitignore").write_bytes(mine)
        _, planned = self.run_harness("--dry-run")
        self.assertEqual((self.target / ".gitignore").read_bytes(), mine)
        code, done = self.run_harness()
        self.assertEqual(code, 0, done)
        merged = (self.target / ".gitignore").read_bytes()
        self.assertTrue(merged.startswith(mine + b"# Repository harness\nnode_modules/\n"), merged)
        added = merged[len(mine):].splitlines()
        self.assertNotIn(b"dist/", added)  # `dist` covers it
        self.assertNotIn(b".env", added)
        self.assertIn(b"!.env.example", added)
        self.assertFalse((self.target / ".gitignore.starter").exists())
        report = [line for line in done.splitlines() if line.startswith("  > .gitignore (node_modules/, ")]
        self.assertEqual(len(report), 1, done)
        self.assertIn(report[0], planned)  # a dry run names the same lines
        code, again = self.run_harness()
        self.assertEqual((code, (self.target / ".gitignore").read_bytes()), (0, merged))  # nothing is appended twice
        self.assertNotIn("  > ", again)

    def test_every_placeholder_gets_a_value_and_nothing_else_is_changed(self):
        # A placeholder without a value stops every worker: its router tells it to ask. PROMPTS.md describes them.
        left = {name for path in (FILES[name] for name in FILES if name != "PROMPTS.md") for name in harness.PLACEHOLDER.findall(path.read_bytes())}
        known = {name.encode("utf-8") for name in (*harness.NEUTRAL, "product-name", "repo", harness.INTEGRATION_BRANCH)}
        self.assertEqual(left - known, set())
        self.assertEqual(known - left, set())  # and no value outlives its placeholder
        self.run_harness()
        (self.target / "CLAUDE.md.starter").write_bytes(FILES["CLAUDE.md"].read_bytes())  # as beside a router of the project's own
        mine = b"a page of the project's own: {{product-name}} {{mustache}}\r\n"
        (self.target / "README.md").write_bytes(mine)
        (self.target / ".agent/Memory.md").write_bytes(mine)  # at a path of the harness that has no placeholder
        with open(self.target / ".agent/Context.md", "ab") as page:
            page.write(b"ours: {{mustache}}\r\n")
        for command in (["init", "-q", "-b", "trunk"], ["remote", "add", "origin", "https://someone:a-token@example.invalid/team/product.git"]):
            self.git(*command)
        before = tree(self.target)
        changed = harness.fill(self.target)
        self.assertEqual(changed, [".agent/Context.md", ".agent/Instructions.md", ".cursorrules", ".windsurfrules", "AGENTS.md", "CLAUDE.md", "CLAUDE.md.starter"])
        after = tree(self.target)
        self.assertEqual({name for name in after if after[name] != before[name]}, set(changed))
        self.assertEqual([name for name in changed if b"{{" in after[name]], [".agent/Context.md"])
        self.assertTrue(after[".agent/Context.md"].endswith(b"ours: {{mustache}}\r\n"))  # double braces of the project's own
        self.assertEqual(len({after[name] for name in (*ROUTERS, "CLAUDE.md.starter")}), 1)
        self.assertIn(b"# Project Instructions: repo\n", after["CLAUDE.md"])
        self.assertIn(b"open a PR into `trunk`.", after["CLAUDE.md"])
        self.assertIn(b"| **Repo** | https://example.invalid/team/product.git |", after[".agent/Context.md"])
        self.assertNotIn(b"a-token", b"".join(after[name] for name in changed))  # these pages are committed
        self.assertEqual(harness.fill(self.target), [])  # a second run finds nothing to fill

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

    def test_an_install_that_fails_half_way_takes_its_files_back(self):
        # Half a harness would pass for a whole one, and nothing would ever complete it.
        self.target.mkdir()
        (self.target / "CLAUDE.md").write_bytes(b"ours\n")
        copy, copied = shutil.copyfile, []

        def disk_fills_up(source, destination):
            copied.append(destination)
            if len(copied) == 5:
                Path(destination).write_bytes(b"cut short")
                raise OSError("no space left on device")
            return copy(source, destination)

        with mock.patch.object(harness.shutil, "copyfile", disk_fills_up):
            code, output = self.run_harness()
        self.assertEqual(code, 2, output)
        self.assertIn("no space left on device", output)
        self.assertEqual(tree(self.target), {"CLAUDE.md": b"ours\n"})
        self.assertEqual(self.run_harness()[0], 0)  # and the next run installs all of it
        self.assertEqual(len(tree(self.target)), len(FILES) + 1)  # every path, and the harness's router beside the project's

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
        # `python -m` looks in the repository's root first. The setup runs the harness's own code and no one else's.
        (self.target / "json.py").write_text("open('ran-by-setup', 'w')\n", encoding="utf-8")
        (self.target / "lefthook.yml").write_text("pre-commit: {}\n", encoding="utf-8")
        self.assertEqual(setup()[0], "")
        self.assertFalse((self.target / "ran-by-setup").exists())
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

    def git(self, *command, **environment):
        done = subprocess.run(
            ["git", *command], cwd=self.target, capture_output=True, env={**os.environ, "SKIP_GITLEAKS": "1", **environment}
        )
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        return done.stdout + done.stderr  # git passes on what a hook prints through either

    @unittest.skipUnless(harness.git_bash(), "needs the bash of Git for Windows")
    def test_the_commit_hook_keeps_line_endings_and_reads_every_file_name(self):
        self.run_harness()
        for command in (
            ["init", "-q"], ["config", "commit.gpgsign", "false"], ["config", "core.autocrlf", "false"],
            ["config", "core.hooksPath", ".githooks"],
        ):
            self.git(*command)
        page = b"---\ntitle: page\nupdated_at: 2020-01-01\n---\n\nbody\n"
        pages = {
            "docs/plain.md": page, "docs/한글.md": page, "docs/crlf.md": page.replace(b"\n", b"\r\n"),
            "docs/mixed.md": page + b"a pasted line\r\nand one more\n",  # each line keeps its own ending
        }
        for name, content in pages.items():
            (self.target / name).write_bytes(content)
        unreadable = page + b"caf\xe9\n"  # not UTF-8, so the updater fails on it
        (self.target / "docs/latin1.md").write_bytes(unreadable)
        self.git("add", *pages, "docs/latin1.md")
        # The identity comes from the environment alone, as on a build server: `git config user.name` fails there.
        empty = self.target.parent / "empty.gitconfig"
        empty.write_bytes(b"")
        identity = {
            "GIT_CONFIG_GLOBAL": str(empty), "GIT_CONFIG_NOSYSTEM": "1", "GIT_AUTHOR_NAME": "tester",
            "GIT_AUTHOR_EMAIL": "tester@example.invalid", "GIT_COMMITTER_NAME": "tester", "GIT_COMMITTER_EMAIL": "tester@example.invalid",
        }
        said = self.git("commit", "-q", "-m", "Add pages", **identity)
        # A failed updater exits 1, which used to be read as "bumped": the page is named as left alone instead.
        self.assertIn(b"docs/latin1.md (front matter left as it is", said)
        self.assertNotIn(b"  - docs/latin1.md\n", said)
        self.assertEqual(self.git("show", "HEAD:docs/latin1.md"), unreadable)
        for name, content in pages.items():
            committed = self.git("show", f"HEAD:{name}")
            self.assertRegex(committed, rb"updated_at: 20\d\d-\d\d-\d\d\r?\n", name)
            self.assertNotIn(b"2020-01-01", committed, name)  # a name that is not ASCII used to be skipped
            # The page keeps the line endings it had: a text-mode write on Windows turned LF into CRLF.
            self.assertEqual(re.sub(rb"updated_at: [0-9-]+", b"updated_at: 2020-01-01", committed), content, name)

    @unittest.skipUnless(harness.git_bash(), "needs the bash of Git for Windows")
    def test_a_linked_worktree_is_covered_by_the_setup_of_its_main_checkout(self):
        def notice(folder):
            done = subprocess.run([str(harness.git_bash()), ".claude/hooks/setup-check.sh"], cwd=folder, capture_output=True, text=True)
            self.assertEqual(done.returncode, 0, done.stderr)
            return done.stdout

        self.run_harness()
        for command in (
            ["init", "-q"], ["config", "user.name", "tester"], ["config", "user.email", "tester@example.invalid"],
            ["config", "commit.gpgsign", "false"], ["add", "-A"], ["commit", "-q", "-m", "Install the harness"],
            ["worktree", "add", "-q", "../linked"],
        ):
            self.git(*command)
        linked = self.target.parent / "linked"
        self.assertIn("[AGENT-BOOTSTRAP]", notice(self.target))
        self.assertIn("[AGENT-BOOTSTRAP]", notice(linked))
        # The marker is not tracked, so a worker's new worktree never has one of its own.
        (self.target / ".setup-done").touch()
        self.assertEqual(notice(self.target), "")
        self.assertEqual(notice(linked), "")

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
