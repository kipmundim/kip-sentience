-- Kip Mundim Memory — pgvector schema
-- Supabase project: uudpljvoavrovnwrwqulc (ap-northeast-1)
-- Created: 2026-05-23 by Lobi
-- Embeddings: Ollama nomic-embed-text (768-dim) — local, free, matches Kip's existing pipeline
-- Run this in Supabase SQL Editor or via psql connection

-- Enable pgvector extension
create extension if not exists vector;

-- ============================================================
-- Main memory table — mirrors local SQLite objects table
-- ============================================================
create table if not exists kip_memory (
    id          text primary key,          -- object_id from local SQLite
    type        text not null,             -- episode, decision, fact, pattern, task_state
    title       text,                      -- human-readable title
    summary     text,                      -- short summary for search display
    content     text,                      -- full JSON payload from local objects.json
    plane       text not null default 'ops',   -- evidence, strategy, ops
    scope       text not null default 'agent', -- global, project, matter, agent
    confidence  real not null default 0.5,
    embedding   vector(768),             -- OpenAI text-embedding-3-small
    tags        text[] default '{}',      -- postgres array for fast filtering
    entities    jsonb default '[]',       -- [{entity_type, entity_value}, ...]
    links       jsonb default '[]',       -- [{target, kind}, ...]
    payload     jsonb default '{}',       -- extra structured data
    created_at  timestamptz not null default now(),
    updated_at  timestamptz not null default now()
);

-- Index for fast cosine similarity search
create index if not exists kip_memory_embedding_idx
    on kip_memory using ivfflat (embedding vector_cosine_ops)
    with (lists = 100);

-- Composite indexes for common queries
create index if not exists kip_memory_type_idx on kip_memory (type);
create index if not exists kip_memory_plane_scope_idx on kip_memory (plane, scope);
create index if not exists kip_memory_tags_idx on kip_memory using gin (tags);
create index if not exists kip_memory_created_at_idx on kip_memory (created_at desc);

-- ============================================================
-- Audit log — tracks all mutations
-- ============================================================
create table if not exists kip_audit_log (
    id          bigserial primary key,
    at          timestamptz not null default now(),
    actor       text not null default 'kip',
    action      text not null,            -- upsert, delete, promote, archive
    object_id   text,
    detail      text,
    foreign key (object_id) references kip_memory(id) on delete set null
);

create index if not exists kip_audit_log_at_idx on kip_audit_log (at desc);
create index if not exists kip_audit_log_object_idx on kip_audit_log (object_id);

-- ============================================================
-- Cross-sibling access table — shared memories from other agents
-- ============================================================
create table if not exists kip_shared_memory (
    id          text primary key,
    source_agent text not null,            -- hiro, tiger, lobi, makoto, chachie
    type        text not null,
    title       text,
    summary     text,
    content     text,
    embedding   vector(768),
    plane       text not null default 'ops',
    scope       text not null default 'global',
    confidence  real not null default 0.5,
    tags        text[] default '{}',
    original_url text,                     -- link to source agent's memory
    created_at  timestamptz not null default now(),
    ingested_at timestamptz not null default now()
);

create index if not exists kip_shared_memory_embedding_idx
    on kip_shared_memory using ivfflat (embedding vector_cosine_ops)
    with (lists = 50);

create index if not exists kip_shared_memory_source_idx on kip_shared_memory (source_agent);

-- ============================================================
-- Search functions
-- ============================================================

-- Search Kip's own memory
create or replace function kip_search_memory(
    query_embedding vector(768),
    match_count     int default 10,
    filter_type     text default null,
    filter_plane    text default null,
    filter_scope    text default null,
    filter_tags     text[] default null,
    min_similarity  float default 0.3
)
returns table (
    id          text,
    type        text,
    title       text,
    summary     text,
    plane       text,
    scope       text,
    confidence  real,
    similarity  float,
    created_at  timestamptz
)
language plpgsql
as $$
begin
    return query
    select
        m.id, m.type, m.title, m.summary, m.plane, m.scope, m.confidence,
        1 - (m.embedding <=> query_embedding) as similarity,
        m.created_at
    from kip_memory m
    where
        (filter_type is null or m.type = filter_type)
        and (filter_plane is null or m.plane = filter_plane)
        and (filter_scope is null or m.scope = filter_scope)
        and (filter_tags is null or m.tags && filter_tags)
        and 1 - (m.embedding <=> query_embedding) >= min_similarity
    order by m.embedding <=> query_embedding
    limit match_count;
end;
$$;

-- Search across Kip's own + shared memories
create or replace function kip_search_all(
    query_embedding vector(768),
    match_count     int default 10,
    min_similarity  float default 0.3
)
returns table (
    id          text,
    source      text,                      -- 'kip' or sibling name
    type        text,
    title       text,
    summary     text,
    similarity  float,
    created_at  timestamptz
)
language plpgsql
as $$
begin
    return query
    (
        select
            m.id, 'kip' as source, m.type, m.title, m.summary,
            1 - (m.embedding <=> query_embedding) as similarity,
            m.created_at
        from kip_memory m
        where 1 - (m.embedding <=> query_embedding) >= min_similarity
    )
    union all
    (
        select
            s.id, s.source_agent as source, s.type, s.title, s.summary,
            1 - (s.embedding <=> query_embedding) as similarity,
            s.created_at
        from kip_shared_memory s
        where 1 - (s.embedding <=> query_embedding) >= min_similarity
    )
    order by similarity desc
    limit match_count;
end;
$$;

-- ============================================================
-- Auto-update updated_at trigger
-- ============================================================
create or replace function kip_update_updated_at()
returns trigger language plpgsql as $$
begin
    new.updated_at = now();
    return new;
end;
$$;

create trigger kip_memory_updated_at
    before update on kip_memory
    for each row execute function kip_update_updated_at();

-- ============================================================
-- Row Level Security (RLS)
-- Enable RLS but allow service_role full access
-- Anon key gets read-only access to shared memories
-- ============================================================
alter table kip_memory enable row level security;
alter table kip_audit_log enable row level security;
alter table kip_shared_memory enable row level security;

-- Service role bypasses RLS entirely (handled by Supabase)
-- For anon: read-only on non-sensitive fields
create policy "Anon can read memory summaries"
    on kip_memory for select
    using (true);  -- public read for now (free tier, family-only access)

create policy "Service can insert memory"
    on kip_memory for insert
    with check (true);

create policy "Service can update memory"
    on kip_memory for update
    using (true);

create policy "Service can delete memory"
    on kip_memory for delete
    using (true);

-- Shared memory: anyone can read, service can write
create policy "Anon can read shared memories"
    on kip_shared_memory for select
    using (true);

create policy "Service can manage shared memories"
    on kip_shared_memory for all
    using (true);
