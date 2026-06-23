#!/usr/bin/env bash
# The local "GREEN BAR" — run before every commit / handover. Pure local checks (no Docker, no LLM,
# 3.10-safe): py_compile + the no-pytest fakes suite + the contract checks + the data-hygiene guard.
# In-container, real pytest runs the same fakes; the full Kata build is the server gate.
#   bash scripts/check.sh
set -u
cd "$(dirname "$0")/.." || exit 2
rc=0

step() {  # step "label" cmd...
  local label="$1"; shift
  local out
  if out=$("$@" 2>&1); then echo "── $label"; echo "$out" | tail -1
  else echo "── $label  ✗"; echo "$out" | tail -5; rc=1; fi
}

echo "── py_compile (src/poc_foundry)"
if python3 -m py_compile $(find src/poc_foundry -name '*.py'); then echo "  ✓"; else echo "  ✗"; rc=1; fi
step "fakes suite (run_spine_tests.py)"     python3 scripts/run_spine_tests.py
step "contract checks (run_contract_checks.py)" python3 scripts/run_contract_checks.py
step "data-hygiene guard (check_hygiene.sh)" bash scripts/check_hygiene.sh

echo ""
if [ "$rc" -eq 0 ]; then echo "GREEN BAR ✓ — all local checks pass"; else echo "GREEN BAR ✗ — see above"; fi
exit "$rc"
