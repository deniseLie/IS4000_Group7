-- Knowledge search precision (retrieval evaluation, backend/evaluation/retrieval.py).
-- 0008 ORs every question word, so any shared body word matched: "Tell me a joke" returned four articles and only
-- 16% of questions with no approved article returned nothing. A section now matches only when at least one question
-- word is in its heading or keywords (weight A); body words still count towards the rank. A score threshold was
-- tried and rejected: unrelated questions scored up to 0.86 while correct matches scored as low as 0.29.
-- Keywords are search metadata only: cited headings and bodies are unchanged, so no article version is bumped.
create or replace function kb.search(p_query text,p_categories text[],p_k int)
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
  foreach token in array tsvector_to_array(to_tsvector('simple',p_query)) loop
    query := query || plainto_tsquery('english',token);
  end loop;
  if numnode(query)=0 then return; end if;
  -- Weights are {D,C,B,A}: a non-zero rank with only A counted means a heading or keyword matched.
  return query select 'kb:'||a.article_id||'@v'||a.version||'#'||s.anchor,a.article_id,a.version,s.anchor,
    a.title,s.heading,s.body,a.publisher,a.scope,a.reference_url,a.checked_on,a.review_due,
    ts_rank_cd(s.search_vector,query,32)
  from kb.articles a join kb.sections s using(article_id,version)
  where a.status='published' and (a.kind='demo' or current_date between a.checked_on and a.review_due)
    and (cardinality(p_categories)=0 or a.categories && p_categories) and s.search_vector @@ query
    and ts_rank_cd('{0,0,0,1}'::float4[],s.search_vector,query)>0
  order by ts_rank_cd(s.search_vector,query,32) desc,a.article_id,s.anchor limit p_k;
end $$;

-- Customer wording for the published sections; broad words that pulled unrelated articles are removed
-- (support: close, dispute, transfer, refund; cards: payment, due, limit, interest, late; card-limit-change: change, steps,
-- days; card-controls: card, new, not working, charge, amount, received).
update kb.sections s set keywords=k.keywords
from kb.articles a,(values
  ('cards','main','card credit fee declined decline not working atm online payment shop payment error'),
  ('support','main','human person real support staff contact handoff speak talk someone'),
  ('lost-card','main','lost stolen stole missing card block freeze wallet purse replacement'),
  ('card-limit-change','main','credit limit card limit increase decrease higher lower raise trip temporary permanent'),
  ('card-controls','freeze','freeze unfreeze frozen lock block unblock temporarily'),
  ('card-controls','replace','replace replacement damaged broken faulty'),
  ('card-controls','dispute','dispute chargeback charged twice wrong refund merchant cancelled'),
  ('app-login-recovery','steps','locked locked out lockout app mobile phone login log in sign in forgot password reset unlock access attempts'),
  ('sg-accounts','main','fixed deposit maturity matures early withdrawal savings current account open an account savings account minimum balance'),
  ('sg-card-payments','main','minimum payment minimum sum pay the minimum unpaid interest credit card payment due late fee bill'),
  ('sg-card-disputes','main','dispute chargeback unauthorised unauthorized non-delivery not delivered never delivered merchant refund'),
  ('overseas-transfers','main','overseas transfer international transfer send money abroad telegraphic swift iban wire remittance foreign'),
  ('transfer-timing','main','pending transfer transfers fast landlord rent payee delayed arrive resend send again on time stuck still going')
) as k(article_id,anchor,keywords)
where s.article_id=k.article_id and s.anchor=k.anchor and a.article_id=s.article_id and a.version=s.version and a.status='published';
