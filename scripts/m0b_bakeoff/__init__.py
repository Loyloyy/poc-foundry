"""M0(b) coder bake-off — bespoke bounded loop vs scripted OpenCode (one-time diagnostic).

NOT pipeline code: a spike that decides which engine takes the `CoderEngine` seat at M1 (design §6
M0(b); ledger). Same on-prem model, same tasks (3 planted breaks + 1 red-first feature), bespoke
edit-format A/B (whole-file vs unified diff). Outcome → attribution matrix (both-fail ⇒ model-weak,
frontier reroute; bespoke-fails/OpenCode-passes ⇒ loop-weak, fix our loop).

Stdlib-only on the host (urllib + subprocess; no host pip, rule #3) — verification runs in the
poc-foundry-sandbox container. Reuses patterns validated in `../poc-builder-prework`.
"""
