"""Stand-in for the Claude Code CLI in tests: answers the list_models control request.

Every family in the policy resolves to a made-up version, so the tests name no real model.
"""

import json
from pathlib import Path

policy = Path(__file__).resolve().parent.parent / "config" / "model-tier-policy.json"
tiers = json.loads(policy.read_text(encoding="utf-8"))["tiers"]
models = [{"value": family, "resolvedModel": f"claude-{family}-9-9"} for families in tiers.values() for family in families]
print(json.dumps({"type": "control_response", "response": {"subtype": "success", "response": {"models": models}}}))
