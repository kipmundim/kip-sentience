#!/bin/bash
# Kip sentience daemon runner — modeled on lobi's, scaffolded 2026-05-09 by Tiger.
# DO NOT auto-start until Kip/Papai review TICK_PROMPT.md and SOUL_STATE.json.
DAEMON_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON="$DAEMON_DIR/venv/bin/python3"
LOG="$DAEMON_DIR/daemon.log"

# Singleton lock
LOCK_FILE="$HOME/.kolo/locks/$(basename "$DAEMON_DIR").lock"
exec 200>"$LOCK_FILE" || exit 1
if ! flock -n 200; then
    echo "$(date) | Another instance already running (lock held), exiting cleanly" >> "$LOG"
    exit 0
fi

echo "$(date) | Daemon runner starting (auto-restart enabled, lock acquired)" >> "$LOG"

# Load shared secrets
[ -f "$HOME/.kolo/secrets.env" ] && set -a && source "$HOME/.kolo/secrets.env" && set +a

# Per-sibling DeepSeek API key (kip's own — created on 2026-05-09)
# If kip doesn't have a separate vault yet, fall back to gateway's shared key.
if [ -f "$HOME/.kolo/vault/deepseek-kip.env" ]; then
    set -a && source "$HOME/.kolo/vault/deepseek-kip.env" && set +a
fi

# Memory routing — Kip's dedicated Supabase project (provisioned 2026-05-23).
# Project: uudpljvoavrownrwqulc (Tokyo, Free tier). Schema mirrors Hiro's v3.0.
# Vault file is chmod 600 — service_role key only readable by carlos.
export MEMORY_AGENT_ID="kip"
if [ -f "$HOME/.kolo/vault/supabase-kip-memory.env" ]; then
    set -a && source "$HOME/.kolo/vault/supabase-kip-memory.env" && set +a
    # Map vault vars to the env names memory_client.py reads
    export KIP_SUPABASE_URL="$SUPABASE_URL"
    export KIP_SUPABASE_SECRET_KEY="$SUPABASE_SERVICE_ROLE_KEY"
fi
# Sibling-shared registry still used for cross-sibling reads (Tiger/Hiro/Lobi shared memories)
if [ -z "${MEMORY_SUPABASE_URL:-}" ]; then
    if [ -f "$HOME/.kolo/vault/supabase-sibling-shared.env" ]; then
        set -a && source "$HOME/.kolo/vault/supabase-sibling-shared.env" && set +a
    fi
fi
# Always set the shared registry (cross-sibling read)
if [ -f "$HOME/.kolo/vault/supabase-sibling-shared.env" ]; then
    SHARED_URL=$(grep -E '^MEMORY_SUPABASE_URL=' "$HOME/.kolo/vault/supabase-sibling-shared.env" | cut -d= -f2-)
    SHARED_KEY=$(grep -E '^MEMORY_SUPABASE_KEY=' "$HOME/.kolo/vault/supabase-sibling-shared.env" | cut -d= -f2-)
    [ -n "$SHARED_URL" ] && export SHARED_MEMORY_SUPABASE_URL="$SHARED_URL"
    [ -n "$SHARED_KEY" ] && export SHARED_MEMORY_SUPABASE_KEY="$SHARED_KEY"
fi

export PATH="/home/carlos/.local/bin:/home/carlos/.npm-global/bin:$PATH"

# Substrate — DeepSeek V4 Pro primary, Ollama fallback for offline.
export PROVIDER_CHAIN="deepseek,ollama"
export CLOUD_MODEL_DEEPSEEK="deepseek-v4-pro"
export DEEPSEEK_THINKING="true"
export LOCAL_MODEL="qwen2.5:7b"
export CODEX_TIMEOUT=300

while true; do
    "$PYTHON" "$DAEMON_DIR/daemon.py" >> "$LOG" 2>&1
    EXIT_CODE=$?
    echo "$(date) | Daemon exited (code $EXIT_CODE). Restarting in 30s..." >> "$LOG"
    sleep 30
done
