"""orchestration-kit: install the repository harness (the tree in harness/) into a repository.

Run as `python -X utf8 -s -B harness.py <target> [--dry-run]`.

Nothing that already exists in the target is overwritten: a file that differs from the harness's stays as it
is, and the harness's version is written beside it as <name>.starter for a merge by hand. The target's git
state is never touched.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "harness"


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


def classify(files: dict[str, Path], target: Path) -> dict[str, list[str]]:
    """Sort every installable path by what a run does with it. Reads only, so a dry run reports the same sets."""
    report: dict[str, list[str]] = {"added": [], "staged": [], "identical": [], "kept": []}
    for relative, source in files.items():
        existing = target / relative
        if not os.path.lexists(existing):  # lexists: a dangling link is an existing entry, never written through
            report["added"].append(relative)
        elif same_content(source, existing):
            report["identical"].append(relative)
        elif os.path.lexists(f"{existing}.starter"):
            report["kept"].append(relative)
        else:
            report["staged"].append(relative)
    return report


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
    return missing


def print_group(title: str, mark: str, paths: list[str], suffix: str = "") -> None:
    print(f"\n{title} ({len(paths)} file(s)):")
    print("\n".join(f"  {mark} {path}{suffix}" for path in paths) or "  (none)")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="harness", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("target", help="repository root; created when it does not exist")
    parser.add_argument("--dry-run", action="store_true", help="report what a run would do and write nothing")
    args = parser.parse_args(argv)

    target = Path(args.target).resolve()
    if target == SOURCE or SOURCE in target.parents:
        return fail("the target is the harness source itself")
    files = sources()
    blocked = blocked_folders(files, target)
    if blocked:  # found before the first write, so a run never stops half way
        return fail("not a folder, and the harness needs one there: " + ", ".join(blocked))
    report = classify(files, target)

    print(f"harness: installing into {target}" + (" (dry run, nothing is written)" if args.dry_run else ""))
    if not args.dry_run:
        for relative in report["added"]:
            (target / relative).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(files[relative], target / relative)
        for relative in report["staged"]:
            shutil.copyfile(files[relative], f"{target / relative}.starter")

    print_group("Added", "+", report["added"])
    print_group("Staged as *.starter, merge by hand", "~", report["staged"], ".starter")
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
        "     which other systems need: git add --chmod=+x .claude/hooks/*.sh .codex/hooks/*.sh .githooks/* scripts/*.sh",
        "If this repository ships its own files (a release ZIP, a package, an image), keep the files added above\n"
        "     out of what it ships: its packaging does not know them.",
    ]
    print("\nInstall complete. Next, in the target:")
    for number, step in enumerate(steps, start=1):
        print(f"  {number}. {step}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
