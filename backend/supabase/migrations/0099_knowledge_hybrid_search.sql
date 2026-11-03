-- Hybrid knowledge search (KNOWLEDGE_SEARCH=hybrid): keyword ranks from kb.search plus embedding similarity, fused.
-- Section vectors are written by the database owner with `python -m scripts.embed_knowledge` (its own embedding key);
-- app roles only read them. Each vector records its model and a hash of the embedded text (title, heading, body;
-- keywords are search metadata and are not embedded). Questions are embedded by the backend and passed in.
-- kb.search is unchanged and stays the keyword-only path. A section found only by similarity must reach
-- p_min_similarity, so a question no article covers can still return nothing (0097). No vector index: about 60
-- sections, so an exact scan is faster and returns exact neighbours.
do $check$
begin
  if exists (select 1 from pg_extension e join pg_namespace n on n.oid=e.extnamespace where e.extname='vector' and n.nspname<>'extensions') then
    raise exception 'pgvector is installed outside the extensions schema; run: alter extension vector set schema extensions';
  end if;
end $check$;
create extension if not exists vector with schema extensions;
grant usage on schema extensions to anon,authenticated;

create table kb.section_embeddings (
  article_id text not null,
  version int not null,
  anchor text not null,
  model text not null check (model ~ '^[a-z0-9][a-z0-9._/:-]{0,99}$'),
  content_sha256 text not null check (content_sha256 ~ '^[0-9a-f]{64}$'),
  embedding extensions.vector not null check (extensions.vector_dims(embedding) between 1 and 4096),
  embedded_at timestamptz not null default now(),
  primary key (article_id,version,anchor,model),
  foreign key (article_id,version,anchor) references kb.sections on delete restrict
);
alter table kb.section_embeddings enable row level security;
revoke all on kb.section_embeddings from public,anon,authenticated;
grant select on kb.section_embeddings to anon,authenticated;
create policy published_embeddings on kb.section_embeddings for select to anon,authenticated using
  (exists (select 1 from kb.articles a where a.article_id=section_embeddings.article_id and a.version=section_embeddings.version));

-- Reciprocal rank fusion (k=60) over the top 20 of each ranking; the fused score is the returned score.
create function kb.hybrid_search(p_query text,p_embedding real[],p_model text,p_min_similarity real,p_categories text[],p_k int)
returns setof kb.search_result
language plpgsql stable security invoker set search_path=''
as $$
declare query_vector extensions.vector;
begin
  if p_k is null or p_k not between 1 and 20 then
    raise exception 'knowledge result limit must be between 1 and 20' using errcode='22023';
  end if;
  if p_embedding is null or cardinality(p_embedding) not between 1 and 4096 or array_position(p_embedding,null) is not null then
    raise exception 'query embedding must hold 1 to 4096 numbers' using errcode='22023';
  end if;
  if p_model is null or p_model !~ '^[a-z0-9][a-z0-9._/:-]{0,99}$' then
    raise exception 'embedding model must be a model id' using errcode='22023';
  end if;
  if p_min_similarity is null or p_min_similarity not between -1 and 1 then
    raise exception 'minimum similarity must be between -1 and 1' using errcode='22023';
  end if;
  query_vector := p_embedding::extensions.vector;
  -- kb.search validates the query text and categories and applies the publication and review-date rules.
  return query
  with keyword as (
    select k.article_id,k.version,k.anchor,row_number() over (order by k.score desc,k.article_id,k.anchor) as rank
    from kb.search(p_query,p_categories,20) k
  ), nearby as (
    select e.article_id,e.version,e.anchor,1-(e.embedding operator(extensions.<=>) query_vector) as similarity
    from kb.section_embeddings e join kb.articles a using(article_id,version)
    where e.model=p_model and extensions.vector_dims(e.embedding)=cardinality(p_embedding)
      and a.status='published' and (a.kind='demo' or current_date between a.checked_on and a.review_due)
      and (cardinality(p_categories)=0 or a.categories && p_categories)
  ), semantic as (
    select s.article_id,s.version,s.anchor,s.similarity,row_number() over (order by s.similarity desc,s.article_id,s.anchor) as rank
    from nearby s order by s.similarity desc,s.article_id,s.anchor limit 20
  ), fused as (
    select article_id,version,anchor,(coalesce(1.0/(60+k.rank),0)+coalesce(1.0/(60+s.rank),0))::real as score
    from keyword k full join semantic s using(article_id,version,anchor)
    where k.rank is not null or s.similarity>=p_min_similarity
  )
  select 'kb:'||a.article_id||'@v'||a.version||'#'||sec.anchor,a.article_id,a.version,sec.anchor,
    a.title,sec.heading,sec.body,a.publisher,a.scope,a.reference_url,a.checked_on,a.review_due,f.score
  from fused f join kb.articles a using(article_id,version) join kb.sections sec using(article_id,version,anchor)
  order by f.score desc,a.article_id,sec.anchor limit p_k;
end $$;

create function public.kb_hybrid_search(p_query text,p_embedding real[],p_model text,p_min_similarity real,p_categories text[],p_k int)
returns setof kb.search_result language sql stable security invoker set search_path=''
as $$ select * from kb.hybrid_search(p_query,p_embedding,p_model,p_min_similarity,p_categories,p_k) $$;
revoke all on function kb.hybrid_search(text,real[],text,real,text[],int),public.kb_hybrid_search(text,real[],text,real,text[],int) from public;
grant execute on function kb.hybrid_search(text,real[],text,real,text[],int),public.kb_hybrid_search(text,real[],text,real,text[],int) to anon,authenticated;
