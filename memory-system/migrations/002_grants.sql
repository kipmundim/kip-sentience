-- Kip Memory System — explicit grants
-- Created: 2026-05-23
-- Why: project was created with "Automatically expose new tables = OFF" (correct security posture).
-- We explicitly grant only what the daemon needs (service_role) — default-deny preserved for anon/authenticated.

-- =============================================================================
-- 1. service_role (used by kip-sentience daemon — bypasses RLS, full CRUD)
-- =============================================================================
GRANT USAGE ON SCHEMA public TO service_role;

GRANT ALL ON public.kip_memories          TO service_role;
GRANT ALL ON public.kip_memory_links      TO service_role;
GRANT ALL ON public.kip_memory_snapshots  TO service_role;

-- Function execute
GRANT EXECUTE ON FUNCTION public.search_kip_memories(vector, INT, FLOAT, TEXT, TEXT, TEXT) TO service_role;
GRANT EXECUTE ON FUNCTION public.recall_kip_memory(UUID) TO service_role;

-- View
GRANT SELECT ON public.kip_memory_health TO service_role;

-- =============================================================================
-- 2. anon / authenticated — INTENTIONALLY no grants
--    Kip's diary is private. If a future feature ever needs anon access to a
--    specific row subset, add a dedicated VIEW + grant SELECT on the view only.
--    Never grant blanket access to the raw kip_memories table.
-- =============================================================================

-- =============================================================================
-- 3. Verification — print what was granted
-- =============================================================================
SELECT
  grantee,
  privilege_type,
  table_name
FROM information_schema.role_table_grants
WHERE table_schema = 'public'
  AND table_name LIKE 'kip_%'
ORDER BY table_name, grantee, privilege_type;
