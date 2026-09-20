-- Filter authorization/scoping predicates before vector ranking. Applying the
-- predicates after an ANN scan can return no rows when the nearest global
-- candidates belong to other cases, even though matching scoped rows exist.
create or replace function match_case_chunks(
  query_embedding vector,
  match_count int,
  p_tenant text,
  p_case_id text default null,
  p_sources text[] default '{}'
)
returns table (id text, case_id text, source text, content text, metadata jsonb, similarity float)
language sql stable as $$
  with filtered as materialized (
    select e.id, e.case_id, e.source, e.content, e.metadata, e.embedding
    from case_embeddings e
    where e.tenant_id = p_tenant
      and (p_case_id is null or e.case_id is null or e.case_id = p_case_id)
      and (
        coalesce(array_length(p_sources, 1), 0) = 0
        or exists (select 1 from unnest(p_sources) s where e.source like s || '%')
      )
  )
  select e.id, e.case_id, e.source, e.content, e.metadata,
         1 - (e.embedding <=> query_embedding) as similarity
  from filtered e
  order by e.embedding <=> query_embedding
  limit match_count
$$;
