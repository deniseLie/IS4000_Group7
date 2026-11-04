-- Knowledge without the facts table. kb.facts (0008, rows from 0055 and 0098) duplicated figures that are already in the
-- published section text; the only reader was the retail fall-below check, which now keeps that synthetic term itself
-- (agents/retail_checks.FALL_BELOW). Sections, citations and retired versions are unchanged.
-- The in-memory article copy is also gone, so one approved article is read whole by id: kb_get_article returns the
-- published, in-review sections of an article (RM ticket guidance names an article, not a search).
drop function public.kb_get_fact(text);
drop function kb.get_fact(text);
drop table kb.facts;

create function kb.get_article(p_article_id text)
returns setof kb.search_result
language sql stable security invoker set search_path=''
as $$
  select 'kb:'||a.article_id||'@v'||a.version||'#'||s.anchor,a.article_id,a.version,s.anchor,
    a.title,s.heading,s.body,a.publisher,a.scope,a.reference_url,a.checked_on,a.review_due,0::real
  from kb.articles a join kb.sections s using(article_id,version)
  where a.article_id=p_article_id and a.status='published' and (a.kind='demo' or current_date between a.checked_on and a.review_due)
  order by s.anchor
$$;
create function public.kb_get_article(p_article_id text)
returns setof kb.search_result language sql stable security invoker set search_path=''
as $$ select * from kb.get_article(p_article_id) $$;
revoke all on function kb.get_article(text),public.kb_get_article(text) from public;
grant execute on function kb.get_article(text),public.kb_get_article(text) to anon,authenticated;
