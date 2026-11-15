---
tier: efficient
runtime: claude
repo: REPOSITORY-OR-FOLDER-NAME
budget: [rounds=3]
writes: [PATH-THE-WORKER-MAY-EDIT]
---

# TASK TITLE

<!--
Front matter rules (checked by the dispatch gate; a malformed block is refused):
- Only flat `key: value` lines; lists are one line, `[a, b]`. No comments inside the block.
- Required: tier (the model's tier in config/model-tier-policy.json), runtime (claude or codex), repo
  (the repository's or the folder's name, as you would call it: the ledger records it and nothing is
  compared with it).
- Optional: budget (rounds=N, hours=N, cost_usd=N.N) and writes. Do not add other keys.
Delete this comment before dispatching.
-->

## Goal

What must be true when the worker is done.

## Scope

- May edit:
- Must not touch:

## Done when

The evidence that proves it: commands to run and the output expected from each.

## Report

What to send back with `worker_done`: changed files, the output of each check, and anything left open.
State where the work is: the branch and commit that hold it, or that it is uncommitted in the working
tree. In a workspace of your own, commit it on your branch before you report: nothing else carries it out
of there. Never push.

In research, review or design work, give every verdict (adopt, defer, drop or any other) four things: what the
item does, in one sentence; the source of each fact and the date it was read; how it relates to what the owner
asked for, quoted from the owner's words or marked `estimate`; and the reason for the verdict. A verdict whose
only reason is a limit of the tooling is marked `owner check needed`.
