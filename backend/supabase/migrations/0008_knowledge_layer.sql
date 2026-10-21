-- Versioned public knowledge, read-only for app roles. No embeddings or live website fetches.
-- Content changes ship in new migrations; cite the exact article version and section anchor.
-- Hosted deployment is pending: expose kb in the Data API if using schema-qualified table reads.
-- Public RPC wrappers below can be called through the already-exposed public schema.
create schema kb;
revoke all on schema kb from public;
grant usage on schema kb to anon,authenticated;

create table kb.articles (
  article_id text not null check (article_id ~ '^[a-z0-9][a-z0-9-]{0,59}$'),
  version int not null check (version > 0),
  title text not null check (length(btrim(title)) between 1 and 200),
  categories text[] not null check (cardinality(categories) between 1 and 5
    and array_position(categories,null) is null
    and categories <@ array['retail','fraud','compliance','document','relationship']::text[]),
  publisher text not null check (length(btrim(publisher)) between 1 and 200),
  scope text not null check (length(btrim(scope)) between 1 and 1000),
  kind text not null check (kind in ('demo','external')),
  status text not null check (status in ('draft','published','retired')),
  reference_url text,
  checked_on date,
  review_due date,
  primary key (article_id,version),
  check ((kind='demo' and reference_url is null and checked_on is null and review_due is null)
    or (kind='external' and reference_url is not null and checked_on is not null and review_due is not null
      and reference_url ~ '^https://(www\.moneysense\.gov\.sg|www\.abs\.org\.sg)/[^[:space:]@]*$'
      and review_due >= checked_on))
);
create unique index articles_one_published_version on kb.articles(article_id) where status='published';

create table kb.sections (
  article_id text not null,
  version int not null,
  anchor text not null check (anchor ~ '^[a-z0-9][a-z0-9-]{0,39}$'),
  heading text not null check (length(btrim(heading)) between 1 and 200),
  body text not null check (length(btrim(body)) between 1 and 16000),
  keywords text not null,
  search_vector tsvector generated always as
    (setweight(to_tsvector('english',heading),'A') || setweight(to_tsvector('english',keywords),'A')
      || setweight(to_tsvector('english',body),'B')) stored,
  primary key (article_id,version,anchor),
  foreign key (article_id,version) references kb.articles on delete restrict
);
create index sections_search_gin on kb.sections using gin(search_vector);

create table kb.facts (
  fact_id text primary key check (fact_id ~ '^[a-z][a-z0-9_.]{0,79}$'),
  value jsonb not null check (jsonb_typeof(value)='number'
    or (jsonb_typeof(value)='string' and length(btrim(value #>> '{}'))>0)),
  unit text not null check (length(btrim(unit)) between 1 and 32),
  article_id text not null,
  version int not null,
  anchor text not null,
  foreign key (article_id,version,anchor) references kb.sections on delete restrict
);

alter table kb.articles enable row level security;
alter table kb.sections enable row level security;
alter table kb.facts enable row level security;
revoke all on kb.articles,kb.sections,kb.facts from public,anon,authenticated;
grant select on kb.articles,kb.sections,kb.facts to anon,authenticated;
-- Old published citations remain readable; only current published versions enter search/fact lookup.
create policy published_articles on kb.articles for select to anon,authenticated using (status in ('published','retired'));
create policy published_sections on kb.sections for select to anon,authenticated using
  (exists (select 1 from kb.articles a where a.article_id=sections.article_id and a.version=sections.version));
create policy published_facts on kb.facts for select to anon,authenticated using
  (exists (select 1 from kb.articles a where a.article_id=facts.article_id and a.version=facts.version));

create type kb.search_result as (cite text,article_id text,version int,anchor text,title text,heading text,body text,
  publisher text,scope text,reference_url text,checked_on date,review_due date,score real);
create function kb.search(p_query text,p_categories text[],p_k int)
returns setof kb.search_result
language plpgsql stable security invoker set search_path=''
as $$
declare query tsquery := ''::tsquery; token text;
begin
  if p_query is null or length(p_query)>4000 then
    raise exception 'knowledge query must be non-null and at most 4000 characters' using errcode='22023';
  end if;
  if p_k is null or p_k not between 1 and 20 then
    raise exception 'knowledge result limit must be between 1 and 20' using errcode='22023';
  end if;
  if p_categories is null or array_position(p_categories,null) is not null
      or not p_categories <@ array['retail','fraud','compliance','document','relationship']::text[] then
    raise exception 'knowledge categories must contain only registered domains' using errcode='22023';
  end if;
  -- OR together native normalised terms: natural questions contain words absent from article prose.
  -- Operators are assembled as tsquery values, never interpolated into SQL or query syntax.
  foreach token in array tsvector_to_array(to_tsvector('simple',p_query)) loop
    query := query || plainto_tsquery('english',token);
  end loop;
  if numnode(query)=0 then return; end if;
  return query select 'kb:'||a.article_id||'@v'||a.version||'#'||s.anchor,a.article_id,a.version,s.anchor,
    a.title,s.heading,s.body,a.publisher,a.scope,a.reference_url,a.checked_on,a.review_due,
    ts_rank_cd(s.search_vector,query,32)
  from kb.articles a join kb.sections s using(article_id,version)
  where a.status='published' and (a.kind='demo' or current_date between a.checked_on and a.review_due)
    and (cardinality(p_categories)=0 or a.categories && p_categories) and s.search_vector @@ query
  order by ts_rank_cd(s.search_vector,query,32) desc,a.article_id,s.anchor limit p_k;
end $$;

create function kb.get_fact(p_fact_id text)
returns table(value jsonb,unit text,source_id text)
language sql stable security invoker set search_path=''
as $$
  select f.value,f.unit,'kb:'||f.article_id||'@v'||f.version||'#'||f.anchor
  from kb.facts f join kb.articles a using(article_id,version)
  where f.fact_id=p_fact_id and a.status='published'
    and (a.kind='demo' or current_date between a.checked_on and a.review_due)
$$;

-- A named composite preserves the typed search columns for PostgREST introspection.
create function public.kb_search(p_query text,p_categories text[],p_k int)
returns setof kb.search_result language sql stable security invoker set search_path=''
as $$ select * from kb.search(p_query,p_categories,p_k) $$;
create function public.kb_get_fact(p_fact_id text)
returns table(value jsonb,unit text,source_id text)
language sql stable security invoker set search_path=''
as $$ select * from kb.get_fact(p_fact_id) $$;
revoke all on function kb.search(text,text[],int),kb.get_fact(text),public.kb_search(text,text[],int),public.kb_get_fact(text) from public;
grant execute on function kb.search(text,text[],int),kb.get_fact(text),public.kb_search(text,text[],int),public.kb_get_fact(text) to anon,authenticated;
