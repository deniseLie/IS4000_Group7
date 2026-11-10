-- Resolve explicit safety and statement intents against the current approved article.
-- Suppress weak matches for standalone requests the library does not cover. Compound
-- requests still search normally so an unsupported part does not hide a supported one.
create function kb.retrieval_scope(p_query text)
returns text language sql immutable strict security invoker set search_path=''
as $$
  select case
    when q ~ '\m(safe|secure|protective) (bank )?account\M'
      and q ~ '\m(move|transfer|send|shift)\M' then 'security'
    when q ~ '\m(used my card|unauthori[sz]ed (card |credit card )?(charge|transaction|payment)|unrecogni[sz]ed (charge|transaction|payment)|suspicious (charge|transaction|payment))\M'
      and q ~ '\m(locked out|can.t log in|cannot log in|can.t sign in|cannot sign in|unable to (log|sign) in|login blocked)\M'
      then 'account-security-after-fraud'
    when q ~ 'வங்கி[[:space:]]+அறிக்கை|对账单|對帳單|\mpenyata (bank|akaun|bulanan)\M' then 'statements'
    when q ~ '\m(and|also|plus|as well as)\M|\?.+\?' then null
    when q ~ '\m(deposit insurance|deposit protection|bank (fails|failure|collapses))\M'
      or q ~ '\m(opening hours|business hours|branch hours|when (are|do) (you|branches?) (open|close))\M'
      or q ~ '\m(cheque|check) deposit\M|\mdeposit (an? )?(cheque|check)\M'
      or q ~ '\m(close|closing|shut) (my |an? |the )?(savings |current |bank )?account\M'
      or q ~ '\mjoint (bank |savings |current )?accounts?\M'
      or q ~ '\m(tax|taxes|taxable)\M' and q ~ '\minterest\M'
      or q ~ '\m(email alerts?|alerts? by email|email notifications?|notify me by email)\M' and q ~ '\m(money|payment|deposit|transfer|funds)\M'
      or q ~ '\m(giro|standing instructions?|standing orders?)\M'
      or q ~ '\m(hotel|accommodation)\M' and q ~ '\m(hold|deposit|pre.authori[sz]ation)\M'
      or q ~ '\m(paynow|atm|daily transfer|transfer per day)\M'
        and q ~ '\m(limit|limits|cap|caps|maximum|how much|withdrawal limit|per day|in a day)\M'
      or q ~ '\m(write|compose|tell)\M' and q ~ '\m(poem|poetry|joke)\M'
      or q ~ '\m(phone|smartphone|handset)\M' and q ~ '\m(buy|purchase|choose|recommend)\M'
        and q !~ '\m(bank|app|card|account)\M'
      then ''
    else null
  end from (select lower(p_query) q) normalized
$$;

create function kb.scoped_sections(p_article text,p_categories text[],p_k int)
returns setof kb.search_result language sql stable security invoker set search_path=''
as $$
  select s.cite,s.article_id,s.version,s.anchor,s.title,s.heading,s.body,
    s.publisher,s.scope,s.reference_url,s.checked_on,s.review_due,1::real
  from kb.get_article(p_article) s join kb.articles a using(article_id,version)
  where cardinality(p_categories)=0 or a.categories && p_categories
  order by case when s.anchor in ('main','never-share','monthly') then 0 else 1 end,s.anchor
  limit p_k
$$;

create or replace function public.kb_search(p_query text,p_categories text[],p_k int)
returns setof kb.search_result language plpgsql stable security invoker set search_path=''
as $$
declare focus text:=kb.retrieval_scope(p_query);
begin
  if focus is null then
    return query select * from kb.search(p_query,p_categories,p_k);
  else
    perform kb.search(p_query,p_categories,p_k);  -- retain the normal input validation
    return query select * from kb.scoped_sections(focus,p_categories,p_k);
  end if;
end $$;

create or replace function public.kb_hybrid_search(p_query text,p_embedding real[],p_model text,p_min_similarity real,p_categories text[],p_k int)
returns setof kb.search_result language plpgsql stable security invoker set search_path=''
as $$
declare focus text:=kb.retrieval_scope(p_query);
begin
  if focus is null then
    return query select * from kb.hybrid_search(p_query,p_embedding,p_model,p_min_similarity,p_categories,p_k);
  else
    perform kb.hybrid_search(p_query,p_embedding,p_model,p_min_similarity,p_categories,p_k);
    return query select * from kb.scoped_sections(focus,p_categories,p_k);
  end if;
end $$;

revoke all on function kb.retrieval_scope(text),kb.scoped_sections(text,text[],int) from public;
grant execute on function kb.retrieval_scope(text),kb.scoped_sections(text,text[],int) to anon,authenticated;
