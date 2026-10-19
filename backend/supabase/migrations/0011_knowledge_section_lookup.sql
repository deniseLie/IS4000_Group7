-- Public source details preserve the exact version/anchor after an article is retired.
-- Drafts remain hidden by RLS. This does not expose any customer or operational schema.
create function public.kb_get_section(p_article_id text,p_version int,p_anchor text)
returns table(cite text,article_id text,version int,anchor text,title text,heading text,body text,
  publisher text,scope text,reference_url text,checked_on date,review_due date,score real,published boolean)
language sql stable security invoker set search_path=''
as $$
  select 'kb:'||a.article_id||'@v'||a.version||'#'||s.anchor,a.article_id,a.version,s.anchor,
    a.title,s.heading,s.body,a.publisher,a.scope,a.reference_url,a.checked_on,a.review_due,0::real,a.status='published'
  from kb.articles a join kb.sections s using(article_id,version)
  where a.article_id=p_article_id and a.version=p_version and s.anchor=p_anchor
$$;
revoke all on function public.kb_get_section(text,int,text) from public;
grant execute on function public.kb_get_section(text,int,text) to anon,authenticated;
