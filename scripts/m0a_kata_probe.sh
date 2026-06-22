#!/usr/bin/env bash
# M0(a) — Kata probe checklist (SERVER; run AFTER the register+smoke runbook passes). Verifies the
# sandbox semantics the harness depends on (design §5.5/§5.7/§6), under `--runtime kata`:
#   1. virtio-fs uid/gid mapping (workspace writable; ownership sane)
#   2. nested READ-ONLY tests/ mount inside an RW workspace (coder can't write tests/)
#   3. writable junit path (pytest can emit junit.xml into the workspace)
#   4. named-volume uv cache (warm cache survives a fresh VM)
#   5. resource caps + sandbox_cgroup_only on cgroup v2 (best-effort; host-side cgroup authoritative)
#   6. VM→proxy→vLLM reachability on an internal:true network (reuses the egress spike under kata)
# Self-asserting where it can be; items needing human interpretation are printed as INFO.
#
# Workspaces MUST be on LOCAL disk (never NFS) for Kata virtio-fs. Prereqs: kata registered (smoke
# green), Slice-2 images built.
set -u

HERE="$(cd "$(dirname "$0")" && pwd)"
SANDBOX_IMAGE="${PF_SANDBOX_IMAGE:-poc-foundry-sandbox}"
PROBE_ROOT="${PF_WORKSPACE_DIR:-/var/tmp/pf-m0a}"
WS="$PROBE_ROOT/ws"
TESTS="$PROBE_ROOT/tests"
UVVOL="pf-m0a-uvcache"

PASS=0; FAIL=0; WARN=0
ok()   { echo "  PASS: $1"; PASS=$((PASS+1)); }
bad()  { echo "  FAIL: $1"; FAIL=$((FAIL+1)); }
warn() { echo "  WARN: $1"; WARN=$((WARN+1)); }
k() { docker run --rm --runtime kata "$@"; }   # kata runner

cleanup() {
  echo "── teardown ──"
  docker volume rm "$UVVOL" >/dev/null 2>&1 || true
  rm -rf "$PROBE_ROOT" 2>/dev/null || true
}
trap cleanup EXIT

# ── Pre-flight ───────────────────────────────────────────────────────────────
docker image inspect "$SANDBOX_IMAGE" >/dev/null 2>&1 || { echo "ERROR: image '$SANDBOX_IMAGE' missing — build Slice 2"; exit 2; }
docker info --format '{{json .Runtimes}}' | grep -q kata || { echo "ERROR: kata runtime not registered — run scripts/m0a_kata_register_smoke.md first"; exit 2; }
mkdir -p "$WS" "$TESTS"; chmod 777 "$WS" "$TESTS"
echo "probe root (local disk): $PROBE_ROOT"

echo "── 1. virtio-fs uid/gid mapping (workspace RW; ownership) ──"
if k -v "$WS:/work" "$SANDBOX_IMAGE" sh -c 'id; echo hi > /work/probe.txt && cat /work/probe.txt'; then
  if [ -f "$WS/probe.txt" ]; then
    echo "    host sees:"; ls -ln "$WS/probe.txt" | sed 's/^/      /'
    ok "workspace writable from VM; file visible on host (eyeball uid above — expect 1000)"
  else bad "file written in VM not visible on host (virtio-fs propagation)"; fi
else bad "could not write workspace from VM"; fi

echo "── 2. nested RO tests/ inside RW workspace (coder can't write tests/) ──"
out=$(k -v "$WS:/work" -v "$TESTS:/work/tests:ro" "$SANDBOX_IMAGE" sh -c '
  echo ok > /work/out.txt && echo "WORKSPACE_RW_OK";
  if echo bad > /work/tests/x 2>/dev/null; then echo "TESTS_WRITABLE_BAD"; else echo "TESTS_RO_ENFORCED"; fi' 2>&1)
echo "$out" | sed 's/^/    /'
echo "$out" | grep -q WORKSPACE_RW_OK   && ok "workspace RW" || bad "workspace not RW"
echo "$out" | grep -q TESTS_RO_ENFORCED && ok "nested tests/ is read-only" || bad "nested tests/ was WRITABLE (RO mount not enforced)"

echo "── 3. writable junit path (pytest output into workspace) ──"
if k -v "$WS:/work" "$SANDBOX_IMAGE" sh -c 'printf "<testsuite/>" > /work/junit.xml && test -s /work/junit.xml'; then
  ok "junit.xml writable in workspace"
else bad "cannot write junit.xml into workspace"; fi

echo "── 4. named-volume uv cache (warm cache survives a fresh VM) ──"
docker volume create "$UVVOL" >/dev/null
k -v "$UVVOL:/uv-cache" "$SANDBOX_IMAGE" sh -c 'uv pip install --target /tmp/s -q requests >/dev/null 2>&1; ls -A /uv-cache | head' >/tmp/.uvls 2>&1 || true
if k -v "$UVVOL:/uv-cache" "$SANDBOX_IMAGE" sh -c 'test -n "$(ls -A /uv-cache 2>/dev/null)"'; then
  ok "uv cache populated + persists in the named volume across fresh VMs"
else warn "uv cache empty after install (UV_CACHE_DIR=/uv-cache? proxy/egress? check manually)"; fi

echo "── 5. resource caps + sandbox_cgroup_only (cgroup v2) — best-effort ──"
echo "    INFO: kernel 6.8 ⇒ cgroup v2; known issues #2539/#3038. Host-side cgroup is the"
echo "    authoritative cap (design §5.2); never claim hard in-guest isolation (#12203 mmap bypass)."
if k --memory 256m "$SANDBOX_IMAGE" python -c 'bytearray(400*1024*1024); print("ALLOCATED_400M")' >/tmp/.cap 2>&1; then
  warn "256m-capped VM allocated 400M (in-guest mem cap NOT enforced — expected caveat; rely on host-side cgroup)"
else
  ok "256m memory cap had an effect (alloc failed/killed) — eyeball exit below"
fi
tail -2 /tmp/.cap | sed 's/^/      /'
echo "    NEXT (manual): confirm kata config has sandbox_cgroup_only and that host-side"
echo "    'docker inspect --format {{.HostConfig.Memory}} <ctr>' limits are enforced on the host cgroup."

echo "── 6. VM→proxy→vLLM reachability on internal:true (reusing the egress spike under kata) ──"
echo "    delegating to m0d_egress_spike.sh with PF_SPIKE_RUNTIME=kata ..."
if PF_SPIKE_RUNTIME=kata bash "$HERE/m0d_egress_spike.sh"; then   # bash: don't depend on the exec bit
  ok "egress spike GREEN under kata (VM reaches proxy on internal net; no host.docker.internal)"
else
  bad "egress spike had failures under kata — see its output above"
fi

echo "── SUMMARY: ${PASS} passed, ${FAIL} failed, ${WARN} warnings ──"
[ "$FAIL" -eq 0 ] && echo "M0(a) probe: GREEN (review WARNs/INFOs for caveats to document)" \
                  || echo "M0(a) probe: investigate FAIL lines"
exit "$FAIL"
