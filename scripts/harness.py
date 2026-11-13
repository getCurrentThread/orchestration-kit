"""orchestration-kit: install the repository harness (the tree in harness/) into a repository.

Run as `python -X utf8 -s -B harness.py <target> [--dry-run]`.

Nothing that already exists in the target is overwritten: a file that differs from the harness's stays as it
is, and the harness's version is written beside it as <name>.starter for a merge by hand. The one exception
is .gitignore, which nearly every repository has: the lines it lacks are appended to it. The target's git
state is never touched.
"""

from __future__ import annotations

import argparse
import contextlib
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "harness"
# Windows records no executable bit, and without it another system skips the git hook and cannot run the scripts.
STAGE_EXECUTABLE = "git add --chmod=+x .claude/hooks/*.sh .codex/hooks/*.sh .githooks/* scripts/*.sh"
PLACEHOLDER = re.compile(rb"\{\{([^{}\n]+)\}\}")  # what the harness leaves for a project to fill in
INTEGRATION_BRANCH = "integration branch: `dev`, or the default branch"
NOT_RECORDED = "not recorded yet"
# What `fill` writes for a placeholder that the repository itself has no value for. Each one holds in any
# project: a worker's router no longer stops it to ask, and it is told nothing untrue. The owner corrects them.
NEUTRAL = {
    "e.g. run the apply command after adding a migration": "Run the project's own tests before you report a change as done.",
    "e.g. never print or commit secrets/credentials": "Never print or commit secrets or credentials.",
    "stack-specific silent failure pattern": (
        "Do not hide a failure: an error is handled where it happens or passed on to the caller, never only logged or swallowed."
    ),
    "release rule, e.g. promote `dev` to `main` only at release time": (
        "Push, open a pull request or release only when the owner asks for it in the task at hand. Until then the work stays local."
    ),
    "team: who does what": "the repository's owner decides and accepts the work; agents do the tasks they are given",
    "primary user, one line": NOT_RECORDED,
    "e.g. short and direct": "short and direct",
    "stack": "code in the languages this repository already uses",
    "domain": "this product",
    "add project-specific rules": "**Project rules**: none recorded yet. Follow what the repository's own files show.",
    "e.g. lowercase GitHub handle, same for postmortem filenames": "none, the git name is used as-is",
    "what the product does": NOT_RECORDED,
    "e.g. in MVP development": NOT_RECORDED,
    "e.g. Vercel": NOT_RECORDED,
    "one paragraph": NOT_RECORDED,
    "name": "The repository's owner",
    "role": "decides what is built and accepts the work",
    "framework / build": NOT_RECORDED,
    "DB · Auth": NOT_RECORDED,
    "first decision": "none recorded yet",
    "meaning": "-",
    "out-of-scope item": "none recorded yet",
    "D-number": "-",
}


def fail(message: str) -> int:
    print(f"harness: {message}", file=sys.stderr)
    return 2


def sources() -> dict[str, Path]:
    """Map every path the harness installs, relative to the target root, to the file that supplies it."""
    return {path.relative_to(SOURCE).as_posix(): path for path in sorted(SOURCE.rglob("*")) if path.is_file()}


def same_content(source: Path, existing: Path) -> bool:
    """Compare ignoring CRLF: with git's core.autocrlf a checkout of an installed harness is CRLF throughout,
    and a second run must not stage every one of its files as a conflict. The source tree is LF."""
    return existing.is_file() and existing.read_bytes().replace(b"\r\n", b"\n") == source.read_bytes()


def lacking_ignores(source: Path, existing: Path) -> list[str]:
    """Return the harness's ignore lines that a project's .gitignore does not hold.

    A pattern without its closing slash covers the same folder (`node_modules` covers `node_modules/`).
    """
    held = {line.strip().rstrip(b"/") for line in existing.read_bytes().splitlines()}
    lines = (line.strip() for line in source.read_bytes().splitlines())
    return [line.decode("utf-8") for line in lines if line and not line.startswith(b"#") and line.rstrip(b"/") not in held]


def classify(files: dict[str, Path], target: Path) -> dict[str, list[str]]:
    """Sort every installable path by what a run does with it. Reads only, so a dry run reports the same sets.

    `appended` holds no path: it lists the lines that the one merged file, .gitignore, receives.
    """
    report: dict[str, list[str]] = {"added": [], "staged": [], "identical": [], "kept": [], "merged": [], "appended": []}
    for relative, source in files.items():
        existing = target / relative
        if not os.path.lexists(existing):  # lexists: a dangling link is an existing entry, never written through
            report["added"].append(relative)
        elif same_content(source, existing):
            report["identical"].append(relative)
        elif os.path.lexists(f"{existing}.starter"):
            report["kept"].append(relative)
        elif relative == ".gitignore" and existing.is_file():
            # A starter here made every project wait for a merge by hand that is the same each time.
            report["appended"] = lacking_ignores(source, existing)
            report["merged" if report["appended"] else "identical"].append(relative)
        else:
            report["staged"].append(relative)
    return report


def fill(target: Path) -> list[str]:
    """Write a value for every placeholder the harness has left in target, and return the files that changed.

    The project's name, repository and branch are what the folder and git show there, and the rest is in
    NEUTRAL. Only the harness's own placeholders are replaced, in the files it installs and in their starters:
    double braces of a project's own stay as they are. A second run finds nothing to fill.
    """
    def git(*arguments: str) -> str:
        try:
            said = subprocess.run(["git", "-C", str(target), *arguments], capture_output=True, text=True, encoding="utf-8", errors="replace")
        except OSError:
            return ""
        return said.stdout.strip() if said.returncode == 0 else ""

    # A remote's address can carry a token, and these pages are committed.
    remote = re.sub(r"://[^/@]+@", "://", git("config", "--get", "remote.origin.url"))
    branch = git("symbolic-ref", "--short", "-q", "HEAD")  # also names the branch of a repository without a commit
    values = {
        **NEUTRAL,
        "product-name": target.name,
        "repo": remote or "a local repository without a remote",
        INTEGRATION_BRANCH: f"`{branch}`" if branch else "the default branch",
    }
    known = {name.encode("utf-8"): value.encode("utf-8") for name, value in values.items()}
    changed = []
    for relative, source in sources().items():
        # PROMPTS.md describes the placeholders and keeps them.
        if relative == "PROMPTS.md" or not PLACEHOLDER.search(source.read_bytes()):
            continue
        for name in (relative, f"{relative}.starter"):
            page = target / name
            if not page.is_file():
                continue
            before = page.read_bytes()
            after = PLACEHOLDER.sub(lambda found: known.get(found.group(1), found.group(0)), before)
            if after != before:
                page.write_bytes(after)
                changed.append(name)
    return changed


def blocked_folders(files: dict[str, Path], target: Path) -> list[str]:
    """Name every entry in the target that is not a folder where the harness needs one."""
    folders = {folder for relative in files for folder in (target / relative).parents if folder.is_relative_to(target)}
    return sorted(str(folder) for folder in folders if os.path.lexists(folder) and not folder.is_dir())


def git_bash() -> Path | None:
    """Return the bash of Git for Windows. It is rarely on PATH, and System32's bash.exe is the WSL launcher."""
    git = shutil.which("git")
    if git:
        # git.exe sits in <Git>\cmd, <Git>\bin or <Git>\mingw64\bin.
        for folder in list(Path(git).resolve().parents)[:3]:
            if (folder / "bin" / "bash.exe").is_file():
                return folder / "bin" / "bash.exe"
    return None


def missing_tools() -> list[str]:
    """Name what the harness uses and this machine lacks."""
    missing = []
    if git_bash() is None:
        missing.append("Git for Windows (bash). The harness's hooks and scripts are bash and cannot run.")
    if shutil.which("code-review-graph") is None:
        missing.append(
            "code-review-graph. Its hooks and its MCP server (.mcp.json) do nothing until it is installed:\n"
            "  uv tool install code-review-graph   (or: pip install code-review-graph)"
        )
    if shutil.which("gitleaks") is None:
        missing.append(
            "gitleaks. The pre-commit hook scans no commit for secrets until it is installed:\n"
            "  https://github.com/gitleaks/gitleaks"
        )
    return missing


def print_group(title: str, mark: str, paths: list[str], suffix: str = "") -> None:
    print(f"\n{title} ({len(paths)} file(s)):")
    print("\n".join(f"  {mark} {path}{suffix}" for path in paths) or "  (none)")


def install(target: Path, dry_run: bool = False) -> dict[str, list[str]] | str:
    """Put the harness into target and return what was done with every path, or why nothing was written."""
    if target == SOURCE or SOURCE in target.parents:
        return "the target is the harness source itself"
    files = sources()
    blocked = blocked_folders(files, target)
    if blocked:  # found before the first write, so a run never stops half way
        return "not a folder, and the harness needs one there: " + ", ".join(blocked)
    report = classify(files, target)
    if dry_run:
        return report
    copies = [(files[relative], target / relative) for relative in report["added"]]
    copies += [(files[relative], Path(f"{target / relative}.starter")) for relative in report["staged"]]
    written: list[Path] = []
    try:
        for source, destination in copies:
            destination.parent.mkdir(parents=True, exist_ok=True)
            written.append(destination)  # before the copy: a file that was cut short is taken back too
            shutil.copyfile(source, destination)
        if report["merged"]:  # last, so that a run that is taken back has not touched the project's own file
            ignore = target / ".gitignore"
            held = ignore.read_bytes()
            ending = "\r\n" if b"\r\n" in held else "\n"
            lines = ["" if held.endswith(b"\n") or not held else ending, "# Repository harness", *report["appended"]]
            with open(ignore, "ab") as merged:
                merged.write((lines[0] + ending.join(lines[1:]) + ending).encode("utf-8"))
    except OSError as error:
        # All or nothing: half a harness passes for a whole one, and the next run would not complete it.
        # Every path in `written` was missing before this run, so nothing of the target's own is removed.
        for destination in written:
            with contextlib.suppress(OSError):
                destination.unlink()
        return f"a file could not be written, and the files of this run were taken back: {error}"
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="harness", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("target", help="repository root; created when it does not exist")
    parser.add_argument("--dry-run", action="store_true", help="report what a run would do and write nothing")
    args = parser.parse_args(argv)

    target = Path(args.target).resolve()
    report = install(target, args.dry_run)
    if isinstance(report, str):
        return fail(report)

    print(f"harness: installing into {target}" + (" (dry run, nothing is written)" if args.dry_run else ""))
    print_group("Added", "+", report["added"])
    print_group("Staged as *.starter, merge by hand", "~", report["staged"], ".starter")
    if report["merged"]:
        print_group("Merged, the lines it lacked are appended", ">", report["merged"], f" ({', '.join(report['appended'])})")
    print_group("Skipped, already present and identical", "=", report["identical"])
    if report["kept"]:
        print_group("Left untouched, a *.starter already existed", "!", report["kept"], ".starter")

    for tool in missing_tools():
        print(f"\nMissing on this machine: {tool}")

    if args.dry_run:
        print("\nDry run complete.")
        return 0
    steps = [
        "Merge every *.starter file by hand, then delete it.",
        "Run `bash scripts/setup.sh` (git hooks, wiring check, .setup-done). It keeps a husky or lefthook chain.",
        "Commit: a worker in its own worktree only sees a committed harness. Windows records no executable bit,\n"
        f"     which other systems need: {STAGE_EXECUTABLE}",
        "If this repository ships its own files (a release ZIP, a package, an image), keep the files added above\n"
        "     out of what it ships: its packaging does not know them.",
    ]
    print("\nInstall complete. Next, in the target:")
    for number, step in enumerate(steps, start=1):
        print(f"  {number}. {step}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
