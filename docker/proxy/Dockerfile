# poc-foundry EGRESS PROXY image — a dual-homed allowlisting CONNECT proxy that is the SOLE exit for
# a per-build internal:true network (design §5.2). The orchestrator's OWN egress also goes through it
# (Finding-0). CONNECT logging is the detective control; the PyPI/Fastly domain-fronting residual is
# conceded (DEV_NOTES). All security framing is defense-in-depth.
#
# squid via apt (Debian mirrors are reachable on the build server — verified by Stage 2's images).
FROM debian:bookworm-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        squid ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY squid.conf /etc/squid/squid.conf
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

EXPOSE 3128

# The entrypoint injects the single vLLM private-host exception from PF_VLLM_ALLOW_HOST (env, never
# tracked) then runs squid in the foreground logging CONNECT lines to stdout.
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
