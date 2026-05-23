-- Kip Action Audit — append-only log of every daemon-initiated action
-- Created: 2026-05-23 (Phase 2 of Kip Autonomy Safety Charter)
-- Sits alongside kip_memories + kip_memory + kip_audit_log (Lobi's memory-mutations log)
-- This is the ACTIONS log — fed from the Approval Queue + Budget Guard + tool layer

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- =============================================================================
-- kip_action_audit — every action Kip's daemon attempts, append-only
-- =============================================================================
CREATE TABLE IF NOT EXISTS kip_action_audit (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  requested_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  resolved_at     TIMESTAMPTZ,

  -- Action identity
  tool_name       TEXT NOT NULL,
  tier            TEXT NOT NULL CHECK (tier IN ('free', 'gated', 'forbidden')),
  human_label     TEXT,
  reasoning       TEXT,                  -- Kip's own words for WHY
  args            JSONB DEFAULT '{}',
  pattern_signature TEXT,                -- for "always allow" pattern matching

  -- Cost estimate + actual
  estimated_cost_usd     NUMERIC(10, 6) DEFAULT 0,
  estimated_duration_sec INTEGER DEFAULT 0,
  actual_cost_usd        NUMERIC(10, 6) DEFAULT 0,
  actual_duration_sec    NUMERIC(10, 3) DEFAULT 0,

  -- Approval lifecycle
  approval_status TEXT NOT NULL CHECK (approval_status IN (
    'auto',           -- Tier 1: auto-approved, no human gate
    'pending',        -- Tier 2: awaiting approval in queue
    'approved',       -- Tier 2: Papai clicked approve
    'always_allowed', -- Tier 2: pattern was previously approved with "always allow"
    'denied',         -- Tier 2: Papai clicked deny
    'expired',        -- pending too long, auto-denied
    'stop_button'     -- Cmd+. hit while pending — auto-cancelled
  )),
  approved_by     TEXT,                  -- 'papai' usually; could be 'auto' / 'pattern_match' / 'tiger'
  approved_at     TIMESTAMPTZ,

  -- Execution outcome
  outcome         TEXT CHECK (outcome IN (
    'succeeded', 'failed', 'cancelled', 'timeout', NULL
  )),
  error_message   TEXT,
  result_summary  TEXT,                  -- brief outcome description

  -- Provider + model
  provider        TEXT,                  -- 'deepseek', 'anthropic', etc.
  model           TEXT,                  -- 'deepseek-chat', 'deepseek-reasoner', etc.
  tokens_in       INTEGER DEFAULT 0,
  tokens_out      INTEGER DEFAULT 0,

  -- Containment
  workspace       TEXT,                  -- 'workspace-kip' / worktree path / project_id
  session_id      TEXT,                  -- Kip's tick session or chat session id
  parent_action_id UUID REFERENCES kip_action_audit(id) ON DELETE SET NULL  -- for sub-actions
);

-- Hot indexes — these are the queries the UI + budget guard run constantly
CREATE INDEX IF NOT EXISTS idx_kip_audit_requested_at ON kip_action_audit(requested_at DESC);
CREATE INDEX IF NOT EXISTS idx_kip_audit_pending     ON kip_action_audit(approval_status, requested_at DESC) WHERE approval_status = 'pending';
CREATE INDEX IF NOT EXISTS idx_kip_audit_tool        ON kip_action_audit(tool_name, requested_at DESC);
CREATE INDEX IF NOT EXISTS idx_kip_audit_pattern     ON kip_action_audit(pattern_signature) WHERE pattern_signature IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_kip_audit_today_cost  ON kip_action_audit(requested_at) WHERE outcome = 'succeeded';
CREATE INDEX IF NOT EXISTS idx_kip_audit_session     ON kip_action_audit(session_id) WHERE session_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_kip_audit_outcome     ON kip_action_audit(outcome, requested_at DESC) WHERE outcome IS NOT NULL;

-- =============================================================================
-- kip_always_allow_patterns — learned "always allow" patterns
-- =============================================================================
CREATE TABLE IF NOT EXISTS kip_always_allow_patterns (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  pattern_signature TEXT NOT NULL UNIQUE,
  tool_name       TEXT NOT NULL,
  scope_args      JSONB DEFAULT '{}',     -- additional match constraints
  approved_by     TEXT NOT NULL DEFAULT 'papai',
  approved_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at      TIMESTAMPTZ,            -- nullable = permanent
  use_count       INTEGER NOT NULL DEFAULT 0,
  last_used_at    TIMESTAMPTZ,
  revoked_at      TIMESTAMPTZ,            -- soft delete (audit trail preserved)
  notes           TEXT
);

CREATE INDEX IF NOT EXISTS idx_kip_always_allow_active ON kip_always_allow_patterns(pattern_signature) WHERE revoked_at IS NULL;

-- =============================================================================
-- Today's spend rollup view — feeds BudgetGuard + TopBar usage breakdown
-- =============================================================================
CREATE OR REPLACE VIEW kip_today_spend AS
SELECT
  DATE(requested_at AT TIME ZONE 'Asia/Tokyo') AS day_jst,
  COUNT(*)                                      AS action_count,
  COUNT(*) FILTER (WHERE outcome = 'succeeded') AS succeeded,
  COUNT(*) FILTER (WHERE outcome = 'failed')    AS failed,
  COUNT(*) FILTER (WHERE approval_status = 'pending')  AS pending,
  SUM(COALESCE(actual_cost_usd, 0))             AS total_cost_usd,
  SUM(COALESCE(tokens_in, 0))                   AS total_tokens_in,
  SUM(COALESCE(tokens_out, 0))                  AS total_tokens_out
FROM kip_action_audit
WHERE requested_at >= DATE_TRUNC('day', (now() AT TIME ZONE 'Asia/Tokyo'))
GROUP BY 1;

-- =============================================================================
-- Pending approvals queue view — feeds the ApprovalQueue UI directly
-- =============================================================================
CREATE OR REPLACE VIEW kip_pending_approvals AS
SELECT
  id,
  requested_at,
  tool_name,
  tier,
  human_label,
  reasoning,
  args,
  pattern_signature,
  estimated_cost_usd,
  estimated_duration_sec,
  provider,
  model,
  session_id,
  EXTRACT(EPOCH FROM (now() - requested_at)) AS pending_age_sec
FROM kip_action_audit
WHERE approval_status = 'pending'
ORDER BY requested_at ASC;  -- oldest first (FIFO)

-- =============================================================================
-- RLS — same posture as memory tables (daemon uses service_role)
-- =============================================================================
ALTER TABLE kip_action_audit          ENABLE ROW LEVEL SECURITY;
ALTER TABLE kip_always_allow_patterns ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Allow full access to kip_action_audit" ON kip_action_audit
  FOR ALL USING (true) WITH CHECK (true);

CREATE POLICY "Allow full access to kip_always_allow_patterns" ON kip_always_allow_patterns
  FOR ALL USING (true) WITH CHECK (true);

-- =============================================================================
-- Explicit service_role grants (auto-expose is OFF per security posture)
-- =============================================================================
GRANT USAGE ON SCHEMA public TO service_role;
GRANT ALL ON public.kip_action_audit          TO service_role;
GRANT ALL ON public.kip_always_allow_patterns TO service_role;
GRANT SELECT ON public.kip_today_spend        TO service_role;
GRANT SELECT ON public.kip_pending_approvals  TO service_role;
