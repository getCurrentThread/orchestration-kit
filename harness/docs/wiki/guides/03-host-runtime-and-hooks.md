---
id: guide-host-runtime-and-hooks
title: "Host Runtime and Hooks"
type: guide
created_at: YYYY-MM-DD
created_by: @git_author
updated_at: YYYY-MM-DD
updated_by: @git_author
last_verified_at: YYYY-MM-DD
last_verified_by: @git_author
audit_log:
  - action: created
    at: YYYY-MM-DD
    by: @git_author
    note: "Initial host runtime guide from mogui-agent-harness."
status: active
tags: [guide, runtime, hooks, claude-code, codex]
relations:
  - id: guide-agentic-architecture
    label: references
code_refs:
  - file: .claude/settings.json
    note: "Claude Code hook wiring."
  - file: .mcp.json
    note: "Claude Code MCP server for code-review-graph."
  - file: .codex/hooks.example.json
    note: "Codex hook example wiring."
---

# Host Runtime and Hooks

Host differences are about runtime contracts, not model intelligence. They differ in which files they read automatically, which hooks they run, and at what point they can block.

## Entrypoints

| host | stable entrypoint | caveat |
|---|---|---|
| Claude Code | `CLAUDE.md`, `.claude/settings.json`, `.mcp.json` | hooks can print, block, and post-process |
| Codex | `AGENTS.md`, `.codex/` local config | behavior depends on current tools and host config |

`CLAUDE.md` and `AGENTS.md` are thin routers. Keep them in sync and under 100 lines.

## Claude Code

Claude Code can run `SessionStart`, `PreToolUse`, and `PostToolUse` hooks. In this starter, `load-context.sh` prints `.agent/Context.md` and tells the agent to read `.agent/Instructions.md` and `.agent/Memory.md` directly. `.mcp.json` starts the code-review-graph MCP server, and `.claude/settings.json` approves it, which Claude Code honors once the folder is trusted.

## Codex

Codex reads `AGENTS.md` and uses the tools available in the current session. Do not claim Claude Code hook behavior is guaranteed in Codex. The reliable contract is: read the router, read `.agent`, run verification commands.

## Hook Output

If a hook prints an action-trigger prefix, treat it as an action, not a normal log line. `[AGENT-ASK]`: ask the user, then run the proposed command if approved. `[AGENT-BOOTSTRAP]`: the autonomous case — run the router's First-entry bootstrap flow yourself, no yes/no prompt.

## Verification

```bash
bash scripts/verify-agent-ssot.sh
git diff --check
```
