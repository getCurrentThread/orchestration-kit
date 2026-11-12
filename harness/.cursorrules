# Project Instructions: {{product-name}}

> This file is a **router**. It points to where to read, not the content itself. Keep it under 100 lines.
> Claude Code reads `CLAUDE.md`, Codex reads `AGENTS.md`. Cursor uses `.cursor/rules/agentic-router.mdc` as its project rule adapter.
> `CLAUDE.md`, `AGENTS.md`, `.cursorrules`, and `.windsurfrules` share identical content.

## Always read before answering

- [`.agent/Instructions.md`](./.agent/Instructions.md): Who/What/Rules/Outputs
- [`.agent/Context.md`](./.agent/Context.md): product, team, stack, and decisions SoT
- [`.agent/Memory.md`](./.agent/Memory.md): preferences/corrections learning log

Automatic memory update: "remember / don't forget / from now on / don't do" triggers an immediate write to Memory.md (protocol = Instructions.md § Memory Update Protocol).

## First-entry autonomous bootstrap

If `.setup-done` is missing, or the project's content files still contain unfilled double-brace template placeholders (for example the Code safety bullets below), setup has not been completed. Run the appropriate flow yourself instead of telling the human to type commands:

- Empty/greenfield target: run `bash scripts/setup.sh`, then fill the remaining placeholders by asking the user (see `PROMPTS.md` #1). `setup.sh` writes `.setup-done` when it completes.
- Existing project target: the installer never overwrites a file. Where the project already had one, the harness's version is beside it as `<name>.starter`. Report every `*.starter` file for the human to adjudicate, merge as they decide, and delete it. Then run `bash scripts/setup.sh` to finalize `.setup-done`. Where the project already runs a husky/lefthook chain, `setup.sh` leaves `core.hooksPath` alone: merge the `.githooks/pre-commit` automation into the existing chain by hand (see `PROMPTS.md` #2).

This contract is host-agnostic: Claude Code and Codex must behave identically here, with no Claude-specific tooling assumed. After either flow, still read `.agent/Instructions.md`, `.agent/Context.md`, and `.agent/Memory.md` before answering.

## Required and recommended tools

| Tool | Verify | Install |
|------|--------|---------|
| **code-review-graph** | `code-review-graph status` | `uv tool install code-review-graph` (or `pip install code-review-graph`). `.mcp.json` starts its MCP server. |
| **GSD** | `which gsd-tools` | `npx @opengsd/gsd-core@latest` |
| **gstack** | `command -v gstack` (or set `GSTACK_HOME`) | `git clone --depth 1 https://github.com/garrytan/gstack.git <host-skills-dir>/gstack && (cd <host-skills-dir>/gstack && ./setup)` |
| **Superpowers** | check the host plugin manager | install per host |

Workflow definitions live in [`.agent/workflows/triple-crown.md`](./.agent/workflows/triple-crown.md).

## Code safety (load-bearing, fill in per project)

- {{e.g. run the apply command after adding a migration}}
- {{e.g. never print or commit secrets/credentials}}
- {{stack-specific silent failure pattern}}

## GSD and the LLM wiki

- `.planning/` = GSD execution state. Use `workstreams` for large efforts, `workspace` for isolation, `thread` for open discussions, `quick` for small recording tasks, `fast` for trivial changes, and `capture` for collecting input.
- `docs/wiki/` = long-term knowledge. Decisions go in `decisions/D-*.md`, operational guides in `guides/`, retrospectives in `postmortem/`.
- GSD does not supersede the LLM wiki. GSD holds execution state; the wiki holds long-term knowledge.
- `code-review-graph` MCP = a map of the code structure. Use it first when exploring code or checking the impact of a change. Do not treat it as a document search engine.

## Directory conventions

- `.agent/` (singular) = Layer 2 context (Instructions/Context/Memory/workflows). Change it freely.
- `.agents/` (plural) = external skill library. Do not rename it.
- `docs/wiki/` = Layer 3 wiki (decisions/postmortem/guides).

## Documentation operations

- Decisions are `D-YYYYMMDD-<author>-<slug>.md` (copy `D-template.md`). After writing one, run `bash scripts/decisions-index.sh` to regenerate the index.
- Keep open questions in `.planning/threads/` instead of locking them into an ADR.
- After writing an important guide/ADR/postmortem, run a Review Gate Quiz of up to 3 questions before commit/push/PR.
- After a long workflow, leave a handoff snapshot in `docs/wiki/postmortem/temp/`.

## Branch policy (fill in per project)

- Work on a `feat|fix|docs|chore/<topic>` branch, then open a PR into {{integration branch: `dev`, or the default branch}}.
- Do not commit directly to that branch.
- {{release rule, e.g. promote `dev` to `main` only at release time}}

## Optional: Korean-language projects

These rules apply only when the project writes in Korean. Skip them for English-only projects.

- Match the Korean voice and tone defined in `.agent/korean-persona.md`.
- No emojis in committed files (a hook blocks them).

---

*Keep the router under 100 lines. Move the body to `.agent/` if it grows.*
