---
description: Run the orchestration-review Workflow over a diff (uncommitted changes, a branch against its base, or a GitHub PR) and report blocking vs advisory findings.
argument-hint: "[--in <directory>] [--contract <file>] [base-ref [path...] | pr-number | pr-url | blank for uncommitted changes]"
---

# /orchestration-kit:orchestration-review

Surface for `review/orchestration-review.workflow.js`, a Workflow port of the
upstream review phase. This command writes a diff to a file, hands the file to
the workflow, and presents the result. The workflow owns the fan-out (one
reviewer per dimension, dedup, adversarial verify); this command owns input and
output.

**Input**: $ARGUMENTS

---

## Mode Selection

| Input | Mode |
|---|---|
| Blank | **Local Mode** — review uncommitted changes, new files included |
| A branch, tag or commit (e.g. `master`), optionally followed by paths | **Branch Mode** — review what the current branch adds to that base |
| Number (e.g. `42`) or PR URL | **PR Mode** — review a GitHub PR |

A leading `--in <directory>` names the checkout to review when it is not the
current folder: a worker's own worktree, or a project a master was given a task
for. Accept it only if it is an existing directory and
`git -C "<DIR>" rev-parse --show-toplevel` succeeds; stop with an error
otherwise. With it, every `git` command below runs as `git -C "<DIR>" ...`, "the
current branch" is the one checked out there, and paths are relative to that
checkout. The mode is chosen from what follows it. The diff file is written
where it always is.

A leading `--contract <file>` names the task contract the change was made for,
before or after `--in`. Accept it only if it is an existing file; stop with an
error otherwise. With it, one more reviewer reads the contract beside the diff
and says of every requirement whether the change meets it. Without it, nobody
checks the change against what was asked: use it for every worker's result.

Never pass the raw argument to the shell. Derive the values named below from it
and use only those.

---

## Phase 1 — GATHER

Write the unified diff to a new file, `.orchestration/review-<UTC timestamp>.diff`
in your own workspace root (`<DIFF>` below; an absolute path, also with `--in`).
Create the folder if it is missing; it is
state and is never committed. Do not print the diff into the conversation and do
not paste it anywhere: the reviewers read the file. Run these commands in Bash,
because PowerShell re-encodes redirected output.

**Local Mode:**

```bash
git diff HEAD > <DIFF>
git ls-files --others --exclude-standard -- . ':!.orchestration'   # new files: `git diff` leaves them out
git diff --no-index -- /dev/null <NEW FILE> >> <DIFF>              # once per new file; exit code 1 is normal
```

**Branch Mode:**

Accept the base only if it matches `^[A-Za-z0-9][A-Za-z0-9._/-]*$` and
`git rev-parse --verify --quiet "<BASE>^{commit}"` succeeds. Accept a path only
if it exists in the repository. Reject anything else and stop with an error.

```bash
git diff <BASE>...HEAD [-- <PATH>...] > <DIFF>
```

The three dots compare against the commit where the branch left the base, so
work that reached the base since then is not read as removed by the branch.
Uncommitted changes are not in this diff: commit them first, or use Local Mode.

**PR Mode:**

Normalize URL input by stripping query strings, hash fragments, and trailing
slashes first. Then:

- If input is a GitHub PR URL, extract `<OWNER>`, `<REPO>`, and `<NUMBER>`.
- If input is a bare integer, treat it as `<NUMBER>` in the **current checkout
  repository only**, and derive `<OWNER>/<REPO>` from `git remote get-url origin`
  (normalize SSH/HTTPS form to `owner/repo` and reject if derivation fails).
- Reject anything else (extra text, shell metacharacters, a non-PR URL) and
  stop with an error.

Use the parsed target only:

```bash
gh pr diff <NUMBER> --repo <OWNER>/<REPO> > <DIFF>
```

If the PR is not found, stop with an error.

**Every mode:** count the file's lines. If there are none, stop: "Nothing to review."

```bash
wc -l < <DIFF>     # <LINES>
```

Then derive `language` from the dominant changed-file extension (for example
`.ts`/`.tsx` to `typescript`, `.py` to `python`, `.go` to `go`). Leave it unset
when the change is mixed or non-code — the workflow simply skips the
language-specific reviewer.

## Phase 2 — INVOKE

Call the Workflow tool. The workflow fails closed: it throws without `diffFile`
or `diffLines`, and a reviewer that did not read the file to its last line counts
as a dimension that failed to run. Pass the count as a number and name it nowhere
else: the reviewers are not told it.

```jsonc
Workflow({
  scriptPath: "${CLAUDE_PLUGIN_ROOT}/review/orchestration-review.workflow.js",
  args: {
    diffFile: "<absolute path of <DIFF>>",   // required
    diffLines: <LINES>,                      // required, from `wc -l`
    language: "typescript",                  // optional
    contractFile: "<absolute path of the contract>"   // only with --contract
  }
})
```

The workflow fans out reviewers in parallel (quality, silent failures, the
language's reviewer when `language` is set, and the contract reviewer when
`contractFile` is set), dedups findings on the normalized evidence snippet, and
runs an adversarial verifier on every unique CRITICAL/HIGH finding. A
requirement the contract reviewer finds unmet is a HIGH finding. It returns:

```jsonc
{
  "verdict": "APPROVE" | "CHANGES_REQUESTED",
  "incomplete": false,                 // true if a review dimension failed to run
  "failedDimensions": [ /* { dimension, error } */ ],
  "blocking": [ /* confirmed CRITICAL/HIGH + unverifiable findings */ ],
  "advisory": [ /* MEDIUM/LOW + adversarially-refuted findings */ ],
  "read": { "diffLines": 244, "quality": 245, "silent-failures": 245 },  // how far each reviewer said it read
  "contract": [ /* { requirement, status: "met" | "unmet" | "unjudged", evidence } */ ],  // null without contractFile
  "stats": { "dimensions": 3, "failed": 0, "raw": 11, "unique": 4, "confirmed": 3, "unverified": 0, "uncertain": 0, "refuted": 1 }
}
```

## Phase 3 — REPORT

Present the result to the user (this is the human review gate; the workflow does
not commit anything). A long result can arrive cut off in the notification that
the workflow has finished: the task's output file, which that notification
names, holds it whole under `result`. Read the `contract` rows from there.

- Lead with `verdict` and the `stats` line (dimensions, raw to unique collapse).
  `stats.failed` of 0 means that every reviewer said it read the diff to its last
  line: one that read less is counted as failed. `read` shows each reviewer's
  count beside the diff's length, so a review with no findings needs no look at
  the workflow's journal.
- With `--contract`, give the count of `contract` rows per status and list
  every row that is `unmet` or `unjudged` with its `evidence`. An `unjudged`
  requirement is one a diff cannot show, such as the output of a command: it is
  yours to check by running what the contract names, and the review has not
  cleared it. Without `--contract`, say that the change was not checked against
  any contract: the reviewers then see the diff only.
- List every `blocking` finding with file, severity, and evidence — these must
  clear before a commit. Findings tagged "could not be verified" stay in
  `blocking` by design; call them out as needing manual confirmation.
- List `advisory` findings briefly (MEDIUM/LOW and verifier-refuted items).
- If `incomplete` is true, state which dimensions in `failedDimensions` did not
  run, with the `error` of each (for example a reviewer that read only part of
  the diff), and that the verdict is therefore not a clean approval. Run the
  review again; for a long diff, review it in parts.

## Fail-Closed Contract

This command must never present a clean APPROVE when the review could not fully
run. If the Workflow tool itself errors, report the failure — do not fall back to
a hand-rolled review and do not imply the diff was approved.

---

## Edge Cases

- **No `gh` CLI (PR Mode)**: stop and tell the user PR Mode needs `gh`; suggest
  Branch Mode against a checked-out branch instead.
- **Large diff**: every reviewer reads the whole file, so a large diff is slow
  and costly. In Branch Mode, pass paths and review it in parts.
- **Binary or generated files**: they add noise without reviewable content. In
  Branch Mode, name the paths that matter.
