"""The models a chief lane can run on: what `dispatch --model` accepts and the plan records."""

from __future__ import annotations

# Claude tiers (docs/model-tiers.md), posted to the launcher as typed.
CLAUDE_MODELS = ("sonnet", "opus", "fable")

# Short names the chief types, mapped to the launcher's provider-qualified
# model; anything else is sent as typed. Grok Build ships one model, so the
# alias carries no tier.
DISPATCH_MODEL_ALIASES = {"grok": "grok:grok-4.5"}

# Codex families the chief may name, version-free: the launcher's catalog
# label (case-insensitive) picks the slug, so a new Luna release needs no edit
# here. Only Luna: Codex on a ChatGPT account rejects the other families.
CODEX_FAMILIES = ("luna",)

DISPATCH_MODELS = (*CLAUDE_MODELS, *CODEX_FAMILIES, *DISPATCH_MODEL_ALIASES)
