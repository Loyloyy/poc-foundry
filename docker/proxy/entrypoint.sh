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

# Validate config, then run in the FOREGROUND. No `squid -z`: we have no cache_dir (cache is denied,
# memory-only), and `squid -z` would spawn an instance that writes /run/squid.pid → the real
# `squid -N` below then aborts with "Squid is already running". pid_filename=none in squid.conf keeps
# it PID-file-free.
squid -k parse -f /etc/squid/squid.conf
exec squid -N -d1 -f /etc/squid/squid.conf
