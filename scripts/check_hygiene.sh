#!/usr/bin/env bash
# Data-hygiene guard (AGENTS rule #1): fail if a TRACKED file leaks anything that must stay in the
# gitignored .env — server IP/host, served-model id, /nfs model paths, or a credential. Two layers:
#   • STATIC patterns (always wrong in tracked files), run anywhere.
#   • DYNAMIC: if a local .env exists (server), grep tracked files for its REAL values (catches prose
#     mentions of the host/model, not just KEY=VALUE leaks) — this is the authoritative gate.
# Run before any push:  bash scripts/check_hygiene.sh
set -u
cd "$(dirname "$0")/.." || exit 2
fail=0
hit() { echo "  LEAK: $1"; fail=1; }

# This script literally CONTAINS the leak patterns it greps for, so exclude it from its own scan
# (a scanner matching its own rule set is a false positive, not a leak).
tracked() { git ls-files 2>/dev/null | grep -vx 'scripts/check_hygiene.sh'; }

echo "== static patterns =="
# real *_MODEL/_API_BASE/_API_KEY values in tracked files (placeholders use <...>; blanks end in =)
while IFS= read -r line; do
  [ -n "$line" ] && hit "config value in tracked file → $line"
done < <(tracked | xargs grep -nE '_(MODEL|API_BASE|API_KEY)=[^<#[:space:]]' 2>/dev/null \
         | grep -v '<' | grep -vE '=\s*$' | grep -v '.env.example' || true)
# model paths + obvious credentials
for pat in '/nfs/llm' 'BEGIN [A-Z ]*PRIVATE KEY' 'ghp_[A-Za-z0-9]{20}' 'github_pat_[A-Za-z0-9_]{20}' 'AKIA[0-9A-Z]{12}' 'xox[baprs]-[A-Za-z0-9-]{10}'; do
  while IFS= read -r line; do
    [ -n "$line" ] && hit "pattern '$pat' → $line"
  done < <(tracked | xargs grep -nE "$pat" 2>/dev/null | grep -v '.env.example' || true)
done

echo "== dynamic (.env real values) =="
if [ -f .env ]; then
  vals=$(
    grep -E '^[A-Z_]+=' .env 2>/dev/null | grep -E '_(MODEL|API_BASE)=|VLLM_ALLOW_HOST=' | \
    sed -E 's/^[^=]+=//; s#https?://##; s#/v1.*##; s/"//g' | \
    while IFS= read -r v; do echo "$v"; echo "${v##*/}"; echo "${v%%:*}"; done | \
    sed -E '/^$/d; /^<.*>$/d; /^not-needed$/d; /^localhost$/d' | sort -u
  )
  while IFS= read -r v; do
    [ -z "$v" ] && continue
    [ "${#v}" -lt 4 ] && continue                     # skip trivially-short tokens
    while IFS= read -r line; do
      [ -n "$line" ] && hit "real .env value '$v' appears in a tracked file → $line"
    done < <(tracked | xargs grep -nF "$v" 2>/dev/null || true)
  done <<< "$vals"
else
  echo "  (no local .env — static-only; run on the server for the authoritative dynamic check)"
fi

if [ "$fail" -eq 0 ]; then echo "HYGIENE: clean ✓"; else echo "HYGIENE: LEAKS FOUND ✗"; fi
exit "$fail"
