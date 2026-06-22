#!/bin/sh
# Generate the single vLLM private-host exception from PF_VLLM_ALLOW_HOST (host[:port]) so the real
# host/IP never lives in a tracked config (rule #1). Detects IP vs hostname and picks the right ACL
# type. Empty exception when unset (then the proxy only allows the public allowlist). Then run squid
# in the foreground, logging CONNECT lines to stdout.
set -eu

VLLM_CONF=/etc/squid/vllm.conf
: > "$VLLM_CONF"

if [ -n "${PF_VLLM_ALLOW_HOST:-}" ]; then
    host="${PF_VLLM_ALLOW_HOST%%:*}"
    port="${PF_VLLM_ALLOW_HOST##*:}"
    [ "$port" = "$host" ] && port=443   # no :port given → default https
    if echo "$host" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$'; then
        echo "acl vllm_dst dst $host" >> "$VLLM_CONF"          # IP literal
    else
        echo "acl vllm_dst dstdomain $host" >> "$VLLM_CONF"    # hostname
    fi
    {
        echo "acl vllm_port port $port"
        echo "http_access allow CONNECT vllm_dst vllm_port"
        echo "http_access allow vllm_dst vllm_port"
    } >> "$VLLM_CONF"
    echo "proxy: vLLM exception enabled for ${host}:${port}" >&2
else
    echo "# PF_VLLM_ALLOW_HOST unset — no private-host exception" >> "$VLLM_CONF"
    echo "proxy: no vLLM exception (PF_VLLM_ALLOW_HOST unset)" >&2
fi

# Validate config, init (cacheless, but squid still wants the call), then run foreground.
squid -k parse -f /etc/squid/squid.conf
squid -z -f /etc/squid/squid.conf 2>/dev/null || true
exec squid -N -d1 -f /etc/squid/squid.conf
