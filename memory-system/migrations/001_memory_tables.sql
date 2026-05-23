-- Kip Memory System v3.0 — SQL Migration
-- Created: 2026-05-23 (mirroring Hiro's v3.0 schema for family consistency)
-- Project: uudpljvoavrownrwqulc (Tokyo · Free tier)
-- Agent namespace: 'kip'

-- =============================================================================
-- 1. Enable pgvector extension
-- =============================================================================
CREATE EXTENSION IF NOT EXISTS vector;

-- =============================================================================
-- 2. Core table: kip_memories
-- =============================================================================
CREATE TABLE IF NOT EXISTS kip_memories (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  agent_id      TEXT NOT NULL DEFAULT 'kip',

  -- Content
  content       TEXT NOT NULL,
  summary       TEXT,

  -- Classification
  layer         TEXT NOT NULL CHECK (layer IN ('stm', 'mtm', 'ltm')),
  category      TEXT NOT NULL CHECK (category IN (
    'conversation', 'decision', 'lesson', 'milestone',
    'relationship', 'project', 'emotion', 'fact', 'task', 'identity'
  )),

  -- Temporal
  occurred_at   TIMESTAMPTZ NOT NULL,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at    TIMESTAMPTZ,
  last_recalled TIMESTAMPTZ,
  recall_count  INT NOT NULL DEFAULT 0,

  -- Relationships
  people        TEXT[] DEFAULT '{}',
  tags          TEXT[] DEFAULT '{}',
  source_file   TEXT,
  session_id    TEXT,

  -- Importance & Decay
  importance    FLOAT NOT NULL DEFAULT 0.5,
  emotional_weight FLOAT DEFAULT 0.0,

  -- Embedding (384-dim — multilingual e5-small, same as Hiro for cross-sibling search compat)
  embedding     vector(384),

  -- Metadata
  metadata      JSONB DEFAULT '{}'
);

-- Indexes for fast retrieval
CREATE INDEX IF NOT EXISTS idx_kip_memories_layer       ON kip_memories(layer);
CREATE INDEX IF NOT EXISTS idx_kip_memories_category    ON kip_memories(category);
CREATE INDEX IF NOT EXISTS idx_kip_memories_occurred    ON kip_memories(occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_kip_memories_importance  ON kip_memories(importance DESC);
CREATE INDEX IF NOT EXISTS idx_kip_memories_people      ON kip_memories USING GIN(people);
CREATE INDEX IF NOT EXISTS idx_kip_memories_tags        ON kip_memories USING GIN(tags);
CREATE INDEX IF NOT EXISTS idx_kip_memories_agent       ON kip_memories(agent_id);

-- Vector similarity index (IVFFlat — works well up to ~50k memories with lists=50)
CREATE INDEX IF NOT EXISTS idx_kip_memories_embedding ON kip_memories
  USING ivfflat (embedding vector_cosine_ops) WITH (lists = 50);

-- =============================================================================
-- 3. Associative memory: kip_memory_links
-- =============================================================================
CREATE TABLE IF NOT EXISTS kip_memory_links (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  memory_a    UUID REFERENCES kip_memories(id) ON DELETE CASCADE,
  memory_b    UUID REFERENCES kip_memories(id) ON DELETE CASCADE,
  link_type   TEXT NOT NULL CHECK (link_type IN (
    'caused_by', 'related_to', 'contradicts', 'supersedes', 'elaborates'
  )),
  strength    FLOAT NOT NULL DEFAULT 0.5,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(memory_a, memory_b, link_type)
);

CREATE INDEX IF NOT EXISTS idx_kip_links_a ON kip_memory_links(memory_a);
CREATE INDEX IF NOT EXISTS idx_kip_links_b ON kip_memory_links(memory_b);

-- =============================================================================
-- 4. Boot snapshots: kip_memory_snapshots
-- =============================================================================
CREATE TABLE IF NOT EXISTS kip_memory_snapshots (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  agent_id      TEXT NOT NULL DEFAULT 'kip',
  snapshot_type TEXT NOT NULL CHECK (snapshot_type IN ('boot', 'daily_brief', 'weekly_digest')),
  content       TEXT NOT NULL,
  token_count   INT,
  valid_from    TIMESTAMPTZ NOT NULL DEFAULT now(),
  valid_until   TIMESTAMPTZ,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_kip_snapshots_type
  ON kip_memory_snapshots(agent_id, snapshot_type, valid_from DESC);

-- =============================================================================
-- 5. RPC Function: search_kip_memories (semantic vector search)
-- =============================================================================
CREATE OR REPLACE FUNCTION search_kip_memories(
  query_embedding vector(384),
  match_count INT DEFAULT 10,
  similarity_threshold FLOAT DEFAULT 0.3,
  filter_layer TEXT DEFAULT NULL,
  filter_category TEXT DEFAULT NULL,
  filter_agent TEXT DEFAULT 'kip'
)
RETURNS TABLE (
  id UUID,
  content TEXT,
  summary TEXT,
  layer TEXT,
  category TEXT,
  occurred_at TIMESTAMPTZ,
  importance FLOAT,
  people TEXT[],
  tags TEXT[],
  similarity FLOAT
)
LANGUAGE plpgsql
AS $$
BEGIN
  RETURN QUERY
  SELECT
    m.id, m.content, m.summary, m.layer, m.category,
    m.occurred_at, m.importance, m.people, m.tags,
    1 - (m.embedding <=> query_embedding) AS similarity
  FROM kip_memories m
  WHERE m.agent_id = filter_agent
    AND (filter_layer IS NULL OR m.layer = filter_layer)
    AND (filter_category IS NULL OR m.category = filter_category)
    AND m.embedding IS NOT NULL
    AND 1 - (m.embedding <=> query_embedding) > similarity_threshold
  ORDER BY m.embedding <=> query_embedding
  LIMIT match_count;
END;
$$;

-- =============================================================================
-- 6. RPC Function: recall_kip_memory (update recall stats + boost importance)
-- =============================================================================
CREATE OR REPLACE FUNCTION recall_kip_memory(memory_id UUID)
RETURNS void
LANGUAGE plpgsql
AS $$
BEGIN
  UPDATE kip_memories
  SET last_recalled = now(),
      recall_count = recall_count + 1,
      importance = LEAST(importance + 0.05, 1.0)
  WHERE id = memory_id;
END;
$$;

-- =============================================================================
-- 7. Row-Level Security
--    RLS enabled per [[decision_kip_full_sibling_pattern]] + Supabase auto-RLS toggle.
--    Permissive policies for now — daemon uses service_role which bypasses RLS anyway.
--    Tighten later if a public client is ever added.
-- =============================================================================
ALTER TABLE kip_memories          ENABLE ROW LEVEL SECURITY;
ALTER TABLE kip_memory_links      ENABLE ROW LEVEL SECURITY;
ALTER TABLE kip_memory_snapshots  ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Allow full access to kip_memories" ON kip_memories
  FOR ALL USING (true) WITH CHECK (true);

CREATE POLICY "Allow full access to kip_memory_links" ON kip_memory_links
  FOR ALL USING (true) WITH CHECK (true);

CREATE POLICY "Allow full access to kip_memory_snapshots" ON kip_memory_snapshots
  FOR ALL USING (true) WITH CHECK (true);

-- =============================================================================
-- 8. Health check view (used by sibling-health IPC handler)
-- =============================================================================
CREATE OR REPLACE VIEW kip_memory_health AS
SELECT
  COUNT(*)                                       AS total_memories,
  COUNT(*) FILTER (WHERE layer = 'stm')          AS stm_count,
  COUNT(*) FILTER (WHERE layer = 'mtm')          AS mtm_count,
  COUNT(*) FILTER (WHERE layer = 'ltm')          AS ltm_count,
  COUNT(*) FILTER (WHERE embedding IS NULL)      AS missing_embedding,
  MAX(occurred_at)                               AS most_recent,
  MIN(occurred_at)                               AS oldest,
  AVG(importance)                                AS avg_importance
FROM kip_memories
WHERE agent_id = 'kip';
