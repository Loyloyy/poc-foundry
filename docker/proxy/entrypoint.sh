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

# squid runs as the non-root 'proxy' user (it FATALs as root) and so can't write the container's
# root-owned /dev/stdout. So it logs the CONNECT line to a file it owns, and we stream that file to
# the container's stdout with a background `tail` (this script runs as root) → `docker logs` evidence.
mkdir -p /var/log/squid
chown -R proxy:proxy /var/log/squid
: > /var/log/squid/access.log
chown proxy:proxy /var/log/squid/access.log
tail -n +1 -F /var/log/squid/access.log &

# Validate config, then run in the FOREGROUND. No `squid -z`: we have no cache_dir (cache is denied,
# memory-only), and `squid -z` would spawn an instance that writes /run/squid.pid → the real
# `squid -N` below then aborts with "Squid is already running". pid_filename=none keeps it PID-free.
# -d1 sends squid's own diagnostics to stderr (captured by `docker logs` even before access.log fills).
squid -k parse -f /etc/squid/squid.conf
exec squid -N -d1 -f /etc/squid/squid.conf
