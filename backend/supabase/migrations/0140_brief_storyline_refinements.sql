-- Storyline refinements to the lifecycle Brief Writer (meetings.briefs, 0129/0139); it stays the one brief writer.
-- 1. Upcoming maturities and reviews (next 90 days) from the book-checked profile reader become their own record
--    sources, so "Time deposit ****4410 matures 17 Nov 2026" is a cited Record, not hidden inside the portfolio total.
-- 2. A source-bound clarification code, discuss_funding_options, for meetings whose agreed topics include property
--    financing or investments. Code renders the text (options to discuss, not advice); the model only selects it.
-- Both change the canonical source hash, so a brief saved before this migration shows as needing a refresh.

alter function public.meeting_preparation_inputs(uuid) set schema meetings;
alter function meetings.meeting_preparation_inputs(uuid) rename to preparation_inputs_before_milestones;
revoke all on function meetings.preparation_inputs_before_milestones(uuid) from public,anon,authenticated,demobank_meetings,demobank_meeting_ingress;
create function public.meeting_preparation_inputs(p_meeting_id uuid) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
declare frame jsonb;profile jsonb;a rm.appointments;item jsonb;extra jsonb:='[]';days int;
begin
 frame:=meetings.preparation_inputs_before_milestones(p_meeting_id);
 select * into a from rm.appointments where appointment_id=p_meeting_id;
 profile:=public.rm_client_profile(a.customer_id);
 if jsonb_typeof(profile->'data'->'milestones') is distinct from 'array' then
  raise exception 'the scoped profile reader must return its current milestone collection' using errcode='22023';end if;
 for item in select value from jsonb_array_elements(profile->'data'->'milestones') loop
  if item->>'kind' is null or item->>'title' is null or item->>'due_on' is null then
   raise exception 'a current milestone is missing its kind, title or date' using errcode='22023';end if;
  continue when item->>'kind'='collateral_alert';  -- the alert is its own mandatory source (0139)
  days:=(item->>'due_on')::date-rm.today();
  continue when days<0 or days>90;
  extra:=extra||jsonb_build_array(meetings.brief_source(p_meeting_id,'rm_client_profile','milestone:'||meetings.hash(item),profile->>'call_id',
   format('Recorded date: %s on %s (in %s days).%s%s',item->>'title',coalesce(item->>'due_text',item->>'due_on'),days,
    case when item->>'amount' is not null and item->>'currency' is not null then format(' Amount: %s %s.',item->>'currency',item->>'amount') else '' end,
    case when item->>'details' is not null then ' '||(item->>'details')||'.' else '' end),null,null));
 end loop;
 if jsonb_array_length(frame->'sources')+jsonb_array_length(extra)>128 then
  raise exception 'preparation exceeds 128 sources after adding current dates; narrow the reader window before generation' using errcode='22023';end if;
 return frame||jsonb_build_object('sources',(frame->'sources')||extra,'source_hash',meetings.hash(jsonb_build_array(frame->>'source_hash',
  (select coalesce(jsonb_agg(value-'reference'),'[]') from jsonb_array_elements(extra)))));
end $$;
revoke all on function public.meeting_preparation_inputs(uuid) from public,anon;
grant execute on function public.meeting_preparation_inputs(uuid) to authenticated;

-- The funding-options clarification is accepted only when the agreed topics include property financing or investments.
create or replace function public.meeting_save_brief(p_meeting_id uuid,p_revision int,p_source_hash text,p_plan jsonb,p_command_id uuid) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
declare w meetings.workflows;inputs jsonb;section jsonb;item jsonb;known jsonb;selected jsonb:='[]';ids text[]:='{}';manifest jsonb;
 b meetings.briefs;receipt jsonb;params jsonb:=jsonb_build_array(p_revision,p_source_hash,p_plan);next_version int;
begin
 w:=meetings.require_rm(p_meeting_id);
 receipt:=meetings.replay(p_command_id,p_meeting_id,'save_brief',params);if receipt is not null then return receipt;end if;
 inputs:=public.meeting_preparation_inputs(p_meeting_id);
 if p_revision is distinct from w.revision or inputs->>'source_hash' is distinct from p_source_hash then raise exception 'preparation records or permission changed during generation; refresh the brief' using errcode='P1001';end if;
 known:=(inputs->'sources')||(inputs->'talking_points');
 if p_plan is null or (select array_agg(key) from jsonb_object_keys(p_plan) key) is distinct from array['sections']
  or jsonb_typeof(p_plan->'sections') is distinct from 'array' or jsonb_array_length(p_plan->'sections') not between 1 and 7
 then raise exception 'brief plan must contain bounded source-reference sections' using errcode='22023';end if;
 for section in select value from jsonb_array_elements(p_plan->'sections') loop
  if (select array_agg(key order by key) from jsonb_object_keys(section) key) is distinct from array['heading','items']
   or section->>'heading' not in ('profile','compliance','requests','history','shared_topics','transaction_insights','questions')
   or jsonb_typeof(section->'items') is distinct from 'array' or jsonb_array_length(section->'items') not between 1 and 30
  then raise exception 'invalid brief section' using errcode='22023';end if;
  for item in select value from jsonb_array_elements(section->'items') loop
   if (select array_agg(key order by key) from jsonb_object_keys(item) key) is distinct from array['kind','question','source_id']
    or item->>'kind' not in ('record','question') or not exists(select 1 from jsonb_array_elements(known) x where x->>'source_id'=item->>'source_id')
    or (item->>'source_id')||':'||(item->>'kind')=any(ids)
    or (item->>'kind'='record' and item->'question'<>'null'::jsonb)
    or (item->>'kind'='question' and coalesce(item->>'question','') not in ('confirm_goal','review_status','clarify_next_step','discuss_shared_topic','check_information','compare_product_terms','confirm_document_next_step','review_collateral_next_step','discuss_funding_options'))
    or (item->>'question'='discuss_funding_options' and not coalesce((inputs->'topics') ?| array['home_loan','investments'],false))
    or (item->>'question'='review_collateral_next_step' and not exists(select 1 from jsonb_array_elements(known) x where x->>'source_id'=item->>'source_id' and x->>'reference' like 'rm_collateral_alert:%'))
   then raise exception 'brief items must cite unique available sources and supported question codes' using errcode='22023';end if;
   ids:=array_append(ids,(item->>'source_id')||':'||(item->>'kind'));selected:=selected||jsonb_build_array(item->'source_id');
  end loop;
 end loop;
 if cardinality(ids)>30 then raise exception 'brief supports at most thirty selected source items' using errcode='22023';end if;
 select coalesce(jsonb_agg(jsonb_build_object('source_id',x->'source_id','grant_id',x->'grant_id','category',x->'category','point_id',x->'point'->'point_id')),'[]') into manifest
 from jsonb_array_elements(known) x where selected ? (x->>'source_id');
 select coalesce(max(stored.version),0)+1 into next_version from meetings.briefs stored where meeting_id=p_meeting_id;
 insert into meetings.briefs(brief_id,meeting_id,version,revision,source_hash,content_hash,plan,source_manifest,grants_used,status)
 values(p_command_id,p_meeting_id,next_version,w.revision,p_source_hash,meetings.hash(jsonb_build_array(p_source_hash,p_plan)),p_plan,manifest,inputs->'grants_used','draft') returning * into b;
 return meetings.remember(p_command_id,p_meeting_id,'save_brief',params,to_jsonb(b));
end $$;

