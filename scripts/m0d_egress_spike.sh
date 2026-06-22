#!/usr/bin/env bash
# M0(d) — Egress proxy spike (SERVER). Proves the per-build egress design (design §5.2, §6):
#   • a Docker `internal: true` network has NO route out (the only exit is the proxy);
#   • the dual-homed allowlisting CONNECT proxy ALLOWS the build substrate (PyPI/GitHub) + the single
#     vLLM private-host exception, and DENIES everything else;
#   • denials + allows are visible in the proxy's CONNECT log (the detective control).
#
# Runs the client under runc (this spike is about NETWORKING, not VM isolation — M0(a) reruns the key
# path under `--runtime kata`). Self-asserting: prints PASS/FAIL per check + a summary, then tears
# down. Does NOT print the real vLLM host (masked) so output is safe to paste back.
#
# Prereqs: Slice-2 images built (`docker compose -f docker/compose.yaml build sandbox proxy`).
# Reads PF_VLLM_ALLOW_HOST / PF_SANDBOX_VLLM_KEY / PF_VLLM_URL from ../.env (the vLLM check is
# optional — skipped if unset).
set -u

# ── Config ───────────────────────────────────────────────────────────────────
HERE="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="${PF_ENV_FILE:-$HERE/../.env}"
SANDBOX_IMAGE="${PF_SANDBOX_IMAGE:-poc-foundry-sandbox}"
PROXY_IMAGE="${PF_PROXY_IMAGE:-poc-foundry-proxy}"
RUNTIME="${PF_SPIKE_RUNTIME:-runc}"           # runc for M0(d); M0(a) overrides to kata
NET_INT="pf-spike-internal"
NET_EGR="pf-spike-egress"
PROXY="pf-spike-proxy"
PROXY_URL="http://${PROXY}:3128"

[ -f "$ENV_FILE" ] && { set -a; . "$ENV_FILE"; set +a; }
VLLM_KEY="${PF_SANDBOX_VLLM_KEY:-}"
VLLM_URL="${PF_VLLM_URL:-}"
[ -z "$VLLM_URL" ] && [ -n "${PF_VLLM_ALLOW_HOST:-}" ] && VLLM_URL="http://${PF_VLLM_ALLOW_HOST}/v1"
mask() { if [ -n "${PF_VLLM_ALLOW_HOST:-}" ]; then sed "s#${PF_VLLM_ALLOW_HOST}#<vllm-host>#g"; else cat; fi; }

PASS=0; FAIL=0
ok()   { echo "  PASS: $1"; PASS=$((PASS+1)); }
bad()  { echo "  FAIL: $1"; FAIL=$((FAIL+1)); }

# Client runners (one ephemeral container per check).
c_proxy()   { docker run --rm --runtime "$RUNTIME" --network "$NET_INT" \
                -e HTTPS_PROXY="$PROXY_URL" -e HTTP_PROXY="$PROXY_URL" \
                -e https_proxy="$PROXY_URL" -e http_proxy="$PROXY_URL" \
                "$SANDBOX_IMAGE" sh -c "$1"; }
c_noproxy() { docker run --rm --runtime "$RUNTIME" --network "$NET_INT" "$SANDBOX_IMAGE" sh -c "$1"; }

cleanup() {
  echo "── teardown ──"
  docker rm -f "$PROXY" >/dev/null 2>&1 || true
  docker network rm "$NET_INT" "$NET_EGR" >/dev/null 2>&1 || true
}
trap cleanup EXIT

# ── Pre-flight ───────────────────────────────────────────────────────────────
for img in "$SANDBOX_IMAGE" "$PROXY_IMAGE"; do
  docker image inspect "$img" >/dev/null 2>&1 || { echo "ERROR: image '$img' missing — build Slice 2 first"; exit 2; }
done

echo "── topology: internal:true network (no route out) + dual-homed allowlisting proxy ──"
docker network create --internal "$NET_INT" >/dev/null
docker network create "$NET_EGR" >/dev/null
# Proxy: starts on the EGRESS leg, then also joined to the INTERNAL leg (dual-homed).
docker run -d --name "$PROXY" --network "$NET_EGR" \
  -e PF_VLLM_ALLOW_HOST="${PF_VLLM_ALLOW_HOST:-}" "$PROXY_IMAGE" >/dev/null
docker network connect "$NET_INT" "$PROXY"
sleep 3   # let squid come up
# Fail FAST + LOUD if the proxy didn't stay up: a squid config error aborts startup, the container
# Exits, its name drops out of Docker DNS, and every check below would fail with a cryptic
# "could not resolve proxy". Surface the real squid error instead.
if [ "$(docker inspect -f '{{.State.Running}}' "$PROXY" 2>/dev/null)" != "true" ]; then
  echo "ERROR: proxy container is not running — squid failed to start. Last logs:"
  docker logs "$PROXY" 2>&1 | tail -25 | sed 's/^/    /'
  exit 3
fi
docker logs "$PROXY" 2>&1 | grep -qi 'ready to serve\|Accepting\|listening' || true

echo "── ALLOW checks (should succeed THROUGH the proxy) ──"
c_proxy 'curl -fsS -o /dev/null --max-time 40 https://pypi.org/simple/' \
  && ok "PyPI reachable via proxy" || bad "PyPI via proxy"
c_proxy 'git clone --depth 1 -q https://github.com/octocat/Hello-World /tmp/hw && test -d /tmp/hw/.git' \
  && ok "git clone (GitHub) via proxy" || bad "git clone via proxy"
c_proxy 'uv pip install --target /tmp/site --no-cache -q requests && python -c "import sys;sys.path.insert(0,\"/tmp/site\");import requests"' \
  && ok "uv install (PyPI resolve) via proxy" || bad "uv install via proxy"
if [ -n "$VLLM_URL" ] && [ -n "$VLLM_KEY" ]; then
  c_proxy "curl -fsS -o /dev/null --max-time 30 -H 'Authorization: Bearer $VLLM_KEY' '$VLLM_URL/models'" \
    && ok "vLLM endpoint reachable via proxy (single private-host exception)" || bad "vLLM via proxy"
else
  echo "  SKIP: vLLM check (set PF_VLLM_ALLOW_HOST + PF_SANDBOX_VLLM_KEY [+ PF_VLLM_URL] in .env)"
fi

echo "── DENY checks (should FAIL — proxy denies / no route) ──"
c_proxy 'curl -fsS -o /dev/null --max-time 25 https://www.google.com' \
  && bad "non-allowlisted host was REACHABLE via proxy" || ok "non-allowlisted host denied by proxy"
c_proxy 'curl -fsS -o /dev/null --max-time 20 https://10.255.255.1' \
  && bad "non-vLLM RFC1918 was REACHABLE via proxy" || ok "non-vLLM RFC1918 denied by proxy"
c_noproxy 'curl -fsS -o /dev/null --max-time 12 https://github.com' \
  && bad "DIRECT egress worked (internal net has a route out!)" || ok "direct egress blocked (no route; proxy is the only exit)"
c_noproxy 'curl -fsS -o /dev/null --max-time 10 http://10.255.255.1' \
  && bad "DIRECT RFC1918 reachable from internal net" || ok "direct RFC1918 unreachable from internal net"

echo "── CONNECT log evidence (proxy; vLLM host masked) ──"
docker logs "$PROXY" 2>&1 | grep -Ei 'pypi|github|google|10\.255|action=' | tail -25 | mask || echo "  (no matching log lines)"

echo "── domain-fronting residual (conceded): the allowlist matches the requested host; TLS payload"
echo "   is opaque, so a CDN-fronted host (PyPI/Fastly) is a known residual — the CONNECT log above"
echo "   is the detective control, not a guarantee (see DEV_NOTES)."

echo "── SUMMARY: ${PASS} passed, ${FAIL} failed ──"
[ "$FAIL" -eq 0 ] && echo "M0(d): GREEN" || echo "M0(d): investigate the FAIL lines above"
exit "$FAIL"
