# poc-foundry ORCHESTRATOR (app) image — runs the deterministic LangGraph harness + short-lived
# phase agents. Holds all high-value secrets (secrets live in tool/client code, never in agent
# context — design §5.2 Finding-0). Authored locally; BUILT ON THE SERVER (no host docker, rule #3).
#
# Python 3.12 (stack needs >=3.11; matches the sandbox image). If a runtime dep fails to build on
# 3.12, drop to python:3.11-slim (Stage 2's validated floor).
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# git: workspace commits + clean-room clone (+ the SHA-pinned Stage-2 dep end-state, DECISIONS #2).
# build-essential/curl: some wheels need a compiler at install time. patch: the coder's diff
# edit-format (whole-file is the default, but keep diff usable). docker.io: the M1 in-process broker
# drives the HOST docker socket via the `docker` CLI to spin Kata VMs (risk-accepted M1 residual —
# the socket is mounted in docker-compose.override.yml; the daemon is NOT started in this image).
# Installed from Debian mirrors (build-time apt is reachable on the server — same path the proxy
# image uses for squid).
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential git curl ca-certificates patch docker.io \
    && rm -rf /var/lib/apt/lists/*

# The orchestrator (root) runs git over workspaces chown'd to the sandbox builder (uid 1000); trust
# them globally so git's dubious-ownership guard doesn't abort commits/clone (belt; the code also
# passes `-c safe.directory=*` per call).
RUN git config --global --add safe.directory '*'

# Deps first for layer caching. The base install is schema-light; the [runtime] extra is REQUIRED
# here or the image silently lacks langgraph/langchain/deepagents (mirrors Stage 2's extras lesson).
COPY pyproject.toml ./
COPY src ./src
RUN pip install --upgrade pip && pip install -e ".[runtime,obs,test]"

# Runtime assets. templates/ is stamped into PoC workspaces by P3 scaffold (compose also mounts it
# for live edits). config/scripts as before.
COPY config ./config
COPY scripts ./scripts
COPY templates ./templates
# tests/ carries the committed contract fixtures + suite — needed for the in-container M0(c) check
# (`run_contract_checks.py`) and the pytest suite. compose also mounts it for live edits.
COPY tests ./tests

# Default to a harmless help banner; real runs override the command (CLI lands at M2c).
CMD ["python", "-c", "import poc_foundry; print('poc-foundry', poc_foundry.__version__, '— orchestrator image OK')"]
