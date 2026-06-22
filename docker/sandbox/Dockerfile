# poc-foundry SANDBOX image — runs UNDER `--runtime kata` (a fresh VM per iteration). Executes
# installs / tests / demo ONLY; it NEVER holds high-value secrets (only the sacrificial vLLM
# inference key, injected at runtime). The orchestrator writes workspace files; this image executes
# them (the M1 claim: orchestrator-writes / sandbox-executes).
#
# Contents per design §3 skeleton: python3.12 + uv + git + pytest + ruff. Kept lean; add compilers
# only if a pilot's wheels need them.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    # uv cache is a per-build NAMED VOLUME at runtime (design §5.7) — point uv at a stable path.
    UV_CACHE_DIR=/uv-cache \
    # PoCs are gradio-first: kill phone-home + CDN fonts at the image level (design §5.3).
    GRADIO_ANALYTICS_ENABLED=False \
    HF_HUB_DISABLE_TELEMETRY=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        git curl ca-certificates build-essential \
    && rm -rf /var/lib/apt/lists/*

# uv (fast resolver/installer used inside the VM), plus pytest + ruff baked in so a build doesn't pay
# to install them every iteration. `psycopg[binary]` (libpq bundled in the wheel) is baked too as the
# common SIBLING-SERVICE driver, so a service-using PoC (e.g. pgvector) can `import psycopg` in the
# iteration VMs — which don't run `uv pip install` (only the clean-room does). Per-PoC deps beyond
# these are installed by `uv` into the workspace at runtime.
RUN pip install --upgrade pip && pip install uv pytest ruff 'psycopg[binary]'

# A fixed, non-root build user so workspace ownership maps predictably across virtio-fs
# (uid/gid mapping is an M0(a) probe item). The workspace is bind-mounted RW at /work; tests/ is
# mounted READ-ONLY by the broker (design §5.5). uv cache volume must be writable by this uid.
RUN useradd --create-home --uid 1000 builder \
    && mkdir -p /work /uv-cache \
    && chown -R builder:builder /work /uv-cache
USER builder
WORKDIR /work

# Egress (HTTPS_PROXY/HTTP_PROXY) is injected at runtime by the broker — the VM's ONLY exit is the
# dual-homed allowlisting proxy on the internal network (design §5.2). Nothing baked here.

CMD ["sh", "-c", "echo sandbox-image OK; python --version; uv --version; git --version; pytest --version; ruff --version"]
