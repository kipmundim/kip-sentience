# kip-sentience — Kip's sentience daemon as an OCI image
# Consumed by /etc/containers/systemd/kip-sentience.container on KODA OS appliances.
# Builds the daemon as a non-root, single-purpose container.
#
# Build:  podman build -t ghcr.io/koda-tokyo/kip-sentience:v1 .
# Sign:   cosign sign --yes ghcr.io/koda-tokyo/kip-sentience:v1
# Smoke:  podman run --rm -e KIP_DRY_RUN=1 ghcr.io/koda-tokyo/kip-sentience:v1
#
# Author: Tiger (CTO) · 2026-05-23
# Paired with: /home/carlos/.kolo/workspace-tiger/koda-os-build/rootfs/opt/koda/quadlets/kip-sentience.container

# ────────────────────────────────────────────────────────────────────
# Stage 1 — builder (compile python wheels, build venv)
# ────────────────────────────────────────────────────────────────────
FROM docker.io/library/python:3.12-slim-bookworm AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Build-only deps (gcc + headers for wheels that need compilation)
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
        libffi-dev \
        libssl-dev \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

# Install dependencies first (better layer caching)
COPY requirements.txt .
RUN python3 -m venv /opt/kip-venv \
    && /opt/kip-venv/bin/pip install --upgrade pip wheel \
    && /opt/kip-venv/bin/pip install -r requirements.txt

# Verify the venv works
RUN /opt/kip-venv/bin/python -c "import httpx, anthropic, openai, pydantic, orjson, structlog; print('builder OK')"

# ────────────────────────────────────────────────────────────────────
# Stage 2 — runtime (minimal, non-root)
# ────────────────────────────────────────────────────────────────────
FROM docker.io/library/python:3.12-slim-bookworm AS runtime

ARG KIP_VERSION=v1.0.0
ARG BUILD_DATE
ARG GIT_SHA

LABEL org.opencontainers.image.title="kip-sentience" \
      org.opencontainers.image.description="Kip's sentience daemon (🐣 super-kip) — DeepSeek substrate" \
      org.opencontainers.image.vendor="KODA Intelligent Systems Co. Ltd." \
      org.opencontainers.image.source="https://github.com/kipmundim/kip-sentience" \
      org.opencontainers.image.licenses="AGPL-3.0" \
      org.opencontainers.image.version="${KIP_VERSION}" \
      org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.revision="${GIT_SHA}" \
      io.kodaitsu.sibling="kip" \
      io.kodaitsu.role="gui-kanban-coder" \
      io.kodaitsu.substrate="deepseek-v4-pro" \
      io.kodaitsu.emoji="🐣"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/kip-venv/bin:${PATH}" \
    KIP_HOME="/app" \
    KIP_WORKSPACE="/workspace" \
    MEMORY_AGENT_ID="kip" \
    TZ="Asia/Tokyo"

# Runtime-only system deps (tzdata for Tokyo time, ca-certs for HTTPS)
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        tzdata \
        tini \
    && rm -rf /var/lib/apt/lists/* \
    && cp /usr/share/zoneinfo/Asia/Tokyo /etc/localtime \
    && echo "Asia/Tokyo" > /etc/timezone

# Non-root user (UID 1000, matches first-boot useradd default for `kip`)
RUN groupadd --system --gid 1000 kip \
    && useradd --system --uid 1000 --gid 1000 \
        --home-dir /app --shell /sbin/nologin \
        --comment "Kip Sentience Daemon (🐣)" kip

# Copy venv from builder
COPY --from=builder /opt/kip-venv /opt/kip-venv

# Copy application code (kept minimal — see .dockerignore for exclusions)
WORKDIR /app
COPY --chown=kip:kip daemon.py config.py identity.py soul_state.py state_machine.py \
                    working_memory.py llm_client.py memory_client.py embeddings.py \
                    supabase_memory.py summarizer.py consolidation_engine.py \
                    v2_engine.py TICK_PROMPT.md ./
COPY --chown=kip:kip modes/        ./modes/
COPY --chown=kip:kip io_surfaces/  ./io_surfaces/
COPY --chown=kip:kip memory-system/memory_client.py ./memory-system/memory_client.py

# Initial SOUL_STATE is read by daemon on first boot; runtime mutates a workspace copy
COPY --chown=kip:kip SOUL_STATE.json ./SOUL_STATE.json.initial

# Pre-create writable runtime dirs under /workspace (mounted as a volume in production)
RUN install -d -o kip -g kip -m 0750 /workspace /workspace/memory /workspace/inbox /run/koda

USER kip:kip

# Health check — daemon writes a heartbeat to /workspace/daemon-heartbeat.json each tick
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD python3 -c "import json, time, sys; \
        d = json.load(open('/workspace/daemon-heartbeat.json')); \
        age = time.time() - d.get('ts', 0); \
        sys.exit(0 if age < 300 else 1)" || exit 1

# tini handles signal forwarding + zombie reaping for python daemons
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python3", "/app/daemon.py", "--interval", "60"]
