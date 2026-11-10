-- Accept an explicit day, English month and year as well as ISO in an exact source quote.
-- The stored value is still an ISO date, independently verified before saving a draft.
-- Update the per-item validator used by 0128, leaving the complete-minutes bounds intact.
create or replace function meetings.validate_content_before_long(p_cues jsonb,p_content jsonb) returns void language plpgsql set search_path='' as $$
declare item jsonb; cue jsonb; ids uuid[]:='{}'; id uuid;
begin
  if p_content is null or jsonb_typeof(p_content)<>'object' or octet_length(p_content::text)>24000
      or (select array_agg(key order by key) from jsonb_object_keys(p_content) key) is distinct from array['actions','client_recap','decisions','summary','title'] then raise exception 'minutes content has an invalid schema' using errcode='22023'; end if;
  if jsonb_typeof(p_content->'title') is distinct from 'string' or length(btrim(p_content->>'title')) not between 1 and 160
      or jsonb_typeof(p_content->'client_recap') is distinct from 'string' or length(btrim(p_content->>'client_recap')) not between 1 and 500
      or jsonb_typeof(p_content->'summary') is distinct from 'array' or jsonb_array_length(p_content->'summary') not between 1 and 4
      or jsonb_typeof(p_content->'decisions') is distinct from 'array' or jsonb_array_length(p_content->'decisions')>10
      or jsonb_typeof(p_content->'actions') is distinct from 'array' or jsonb_array_length(p_content->'actions')>10 then raise exception 'minutes fields exceed their bounds or have incorrect types' using errcode='22023'; end if;
  -- Inspect human text fields, not serialized UUIDs or typed metadata.
  perform conversation.require_redacted(p_content->>'title');
  perform conversation.require_redacted(p_content->>'client_recap');
  for item in select value from jsonb_array_elements((p_content->'summary')||(p_content->'decisions')) loop
    if (select array_agg(key order by key) from jsonb_object_keys(item) key) is distinct from array['cue_id','quote','text'] then raise exception 'factual items need only cue_id, quote and text' using errcode='22023'; end if;
    perform meetings.validate_quote(p_cues,item);
  end loop;
  for item in select value from jsonb_array_elements(p_content->'actions') loop
    if (select array_agg(key order by key) from jsonb_object_keys(item) key) is distinct from array['action_id','commitment','condition','cue_id','due_on','due_quote','owner','quote','text'] then raise exception 'action has an invalid schema' using errcode='22023'; end if;
    cue:=meetings.validate_quote(p_cues,item); id:=(item->>'action_id')::uuid;
    if id is null or id=any(ids) or length(item->>'text')>200 or jsonb_typeof(item->'owner') is distinct from 'string' or item->>'owner' not in ('rm','client','unassigned')
        or jsonb_typeof(item->'commitment') is distinct from 'string' or item->>'commitment' not in ('agreed','conditional','tentative','unclear') then raise exception 'action identity, owner or commitment is invalid' using errcode='22023'; end if;
    ids:=array_append(ids,id);
    if item->>'owner'<>'unassigned' and item->>'owner' is distinct from cue->>'speaker' then raise exception 'action owner must match the quoted speaker or remain unassigned' using errcode='22023'; end if;
    if item->>'commitment'='conditional' then
      if jsonb_typeof(item->'condition') is distinct from 'string' or length(btrim(item->>'condition'))=0 or position(item->>'condition' in cue->>'text')=0 then raise exception 'conditional action needs an exact condition quote' using errcode='22023'; end if;
    elsif item->'condition'<>'null'::jsonb then raise exception 'only conditional actions carry a condition' using errcode='22023'; end if;
    if item->>'commitment'='agreed' and lower(item->>'quote') ~ '\m(if|unless|might|maybe|possibly)\M' then raise exception 'conditional or tentative wording cannot become an unconditional agreed action' using errcode='22023'; end if;
    if item->'due_on'<>'null'::jsonb then
      if jsonb_typeof(item->'due_on') is distinct from 'string' or item->>'due_on' !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'
          or jsonb_typeof(item->'due_quote') is distinct from 'string' or length(btrim(item->>'due_quote'))=0
          or position(item->>'due_quote' in cue->>'text')=0 then raise exception 'due date needs exact date evidence in the same cue' using errcode='22023'; end if;
      if item->>'due_quote' !~* ('\m('||(item->>'due_on')||'|'||
          to_char((item->>'due_on')::date,'FMDD FMMonth YYYY')||'|'||
          to_char((item->>'due_on')::date,'DD FMMonth YYYY')||')\M') then
        raise exception 'due date needs exact calendar-date evidence in the same cue' using errcode='22023';
      end if;
    elsif item->'due_quote'<>'null'::jsonb then raise exception 'due_quote requires an explicit due_on date' using errcode='22023'; end if;
  end loop;
end $$;
