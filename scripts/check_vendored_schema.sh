#!/usr/bin/env bash
# Drift check for the vendored Stage-2 schema (DECISIONS #2). Diffs the byte-identical mirror files
# against the local Stage-2 source. Non-zero exit = drift → re-copy + bump the SHA in
# src/poc_foundry/stage2_artifact/_VENDORED.md (or retire the mirror for the git dep).
#
#   bash scripts/check_vendored_schema.sh [path-to-ai-engineer-research]
set -u

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
SRC_REPO="${1:-$REPO/../ai-engineer-research}"
SRC_DIR="$SRC_REPO/src/ai_engineer_research/artifact"
VEND_DIR="$REPO/src/poc_foundry/stage2_artifact"

if [ ! -d "$SRC_DIR" ]; then
  echo "SKIP: Stage-2 source not found at $SRC_DIR"
  echo "      (pass the repo path as arg 1, or this just means the source isn't checked out here.)"
  exit 0
fi

drift=0
for f in schema.py store.py validate.py; do   # __init__.py is intentionally adapted — excluded
  if diff -q "$SRC_DIR/$f" "$VEND_DIR/$f" >/dev/null 2>&1; then
    echo "  OK:    $f in sync"
  else
    echo "  DRIFT: $f differs from source"
    diff -u "$SRC_DIR/$f" "$VEND_DIR/$f" | sed 's/^/      /'
    drift=1
  fi
done

if [ "$drift" -eq 0 ]; then
  echo "vendored schema: IN SYNC with $SRC_DIR"
else
  echo "vendored schema: DRIFT — re-copy the changed file(s) + bump the SHA in artifact/_VENDORED.md"
fi
exit "$drift"
