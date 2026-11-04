-- Store section vectors from inside the hosted SQL editor (no database password needed), the same way as
-- `python -m scripts.embed_knowledge`: each published section is embedded as title, heading and body, and
-- content_sha256 is the SHA-256 of that text, so `scripts.embed_knowledge --check` agrees with this file.
-- Run after migration 0099 and again after every knowledge migration; only new or changed sections are embedded.
--
-- 1. Replace PASTE_EMBEDDING_API_KEY_HERE with EMBEDDING_API_KEY from backend/.env (never LLM_API_KEY).
--    If EMBEDDING_MODEL or EMBEDDING_BASE_URL in backend/.env differ from the values below, change them here too.
-- 2. Run. Do not save the tab as a snippet, remove the key afterwards, and never commit this file with a key in it.
--    The statement text (with the key) may stay in the database's statement statistics; rotate the key if that matters.
-- The http extension exists only inside this transaction; on any error everything rolls back.
begin;
create extension if not exists http with schema extensions;

do $embed$
declare
  api_key text:='PASTE_EMBEDDING_API_KEY_HERE';
  p_model text:='openai/text-embedding-3-small';
  p_url text:='https://openrouter.ai/api/v1/embeddings';
  batch jsonb; reply extensions.http_response; body jsonb; rounds int:=0; total int:=0;
begin
  if api_key='PASTE_EMBEDDING_API_KEY_HERE' then raise exception 'put the embedding key in api_key first'; end if;
  perform extensions.http_set_curlopt('CURLOPT_TIMEOUT_MS','60000');
  loop
    rounds:=rounds+1;
    if rounds>50 then raise exception 'stopped after 50 batches; sections still pending'; end if;
    select jsonb_agg(jsonb_build_object('article_id',p.article_id,'version',p.version,'anchor',p.anchor,'text',p.txt)
             order by p.article_id,p.version,p.anchor)
      into batch
      from (select s.article_id,s.version,s.anchor,a.title||E'\n'||s.heading||E'\n'||s.body as txt
              from kb.sections s join kb.articles a using(article_id,version)
              left join kb.section_embeddings e
                on e.article_id=s.article_id and e.version=s.version and e.anchor=s.anchor and e.model=p_model
             where a.status='published'
               and (e.content_sha256 is null or e.content_sha256<>
                    encode(extensions.digest(convert_to(a.title||E'\n'||s.heading||E'\n'||s.body,'UTF8'),'sha256'),'hex'))
             order by 1,2,3 limit 16) p;
    exit when batch is null;

    reply:=extensions.http(row('POST',p_url,
      array[extensions.http_header('Authorization','Bearer '||api_key)],'application/json',
      jsonb_build_object('model',p_model,'input',
        (select jsonb_agg(x->'text' order by o) from jsonb_array_elements(batch) with ordinality t(x,o)))::text
    )::extensions.http_request);
    if reply.status<>200 then
      raise exception 'embedding provider returned %: %',reply.status,left(reply.content,300);
    end if;
    body:=reply.content::jsonb;
    if not body ? 'data' or jsonb_array_length(body->'data')<>jsonb_array_length(batch) then
      raise exception 'unexpected provider reply: %',left(reply.content,300);
    end if;

    insert into kb.section_embeddings(article_id,version,anchor,model,content_sha256,embedding)
    select b->>'article_id',(b->>'version')::int,b->>'anchor',p_model,
           encode(extensions.digest(convert_to(b->>'text','UTF8'),'sha256'),'hex'),
           (select array_agg(v::real order by i) from jsonb_array_elements_text(d->'embedding') with ordinality q(v,i))::extensions.vector
      from jsonb_array_elements(batch) with ordinality t(b,o)
      join jsonb_array_elements(body->'data') d on (d->>'index')::int=o-1
    on conflict (article_id,version,anchor,model) do update
      set content_sha256=excluded.content_sha256,embedding=excluded.embedding,embedded_at=now();
    total:=total+jsonb_array_length(batch);
  end loop;
  raise notice 'embedded % sections with %',total,p_model;
end $embed$;

drop extension http;
commit;

-- Every published section should have a current vector: missing and stale are 0.
select count(*) as published,
  count(*) filter (where e.content_sha256 is null) as missing,
  count(*) filter (where e.content_sha256<>encode(extensions.digest(convert_to(a.title||E'\n'||s.heading||E'\n'||s.body,'UTF8'),'sha256'),'hex')) as stale
from kb.sections s join kb.articles a using(article_id,version)
left join kb.section_embeddings e on e.article_id=s.article_id and e.version=s.version and e.anchor=s.anchor and e.model='openai/text-embedding-3-small'
where a.status='published';
