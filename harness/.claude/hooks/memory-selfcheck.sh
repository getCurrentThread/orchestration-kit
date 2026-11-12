#!/bin/bash
# memory-selfcheck.sh: UserPromptSubmit hook.
# At the start of every user turn, inject a self-check reminder for the .agent/Memory.md § feedback rules into the system context.
# A hook cannot verify response content; the envelope reminder only increases the frequency of self-review.
# Why: remind every turn whether feedback rules that were not dogfooded are actually reflected in responses.
#
# To repeat a rule every turn, add it as a "- <rule>" line between the two lines inside the EOF below.
# Keep to this project's few load-bearing feedback rules: branch policy, DB push policy, context load.

cat <<'EOF'
[Memory self-check] Before responding, check the .agent/Memory.md § feedback rules.
If in doubt, Read .agent/Memory.md first.
EOF
