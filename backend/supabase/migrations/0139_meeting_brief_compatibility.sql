-- Preserve historical copilot versions while both RM screens use the lifecycle's reference-only current brief.
-- Historical text is filtered inside SQL before the authenticated Data API can return it after access/source changes.
create function meetings.copilot_brief_current(p_brief meetings.copilot_briefs) returns boolean
language plpgsql stable security definer set search_path='' as $$
declare checked jsonb;
begin
 if p_brief.advisor_id is distinct from rm.current_advisor() then return false;end if;
 checked:=rm.brief_recheck(p_brief);
 return jsonb_array_length(checked->'withdrawn')=0 and jsonb_array_length(checked->'removed_points')=0
  and not (checked->>'stale')::boolean;
end $$;
revoke all on function meetings.copilot_brief_current(meetings.copilot_briefs) from public,anon,authenticated,demobank_meetings,demobank_meeting_ingress;

alter function public.rm_meeting_brief(uuid) rename to rm_meeting_brief_before_lifecycle;
revoke all on function public.rm_meeting_brief_before_lifecycle(uuid) from public,anon,authenticated,demobank_meetings,demobank_meeting_ingress;
create function public.rm_meeting_brief(p_meeting_id uuid) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
declare result jsonb;stored meetings.copilot_briefs;brief jsonb;
begin
 result:=public.rm_meeting_brief_before_lifecycle(p_meeting_id);brief:=result#>'{data,brief}';
 if brief='null'::jsonb then return result;end if;
 select * into stored from meetings.copilot_briefs where brief_id=(brief->>'brief_id')::uuid;
 if not found then raise exception 'the historical briefing changed during its scoped read' using errcode='P1001';end if;
 if not meetings.copilot_brief_current(stored) then
  brief:=brief||jsonb_build_object('stale',true,'content',jsonb_build_object(
   'summary','Earlier briefing content is withheld because its source access, records or assigned RM changed. Prepare a current version before acting.',
   'sections',jsonb_build_array(jsonb_build_object('heading','Earlier briefing requires a new review','items',jsonb_build_array(
    jsonb_build_object('text','Earlier source text is no longer available in this snapshot. Prepare the current briefing.','kind','gap','owner','none','source_ids','[]'::jsonb)))),
   'sources','[]'::jsonb,'validation',jsonb_build_object('withheld',1),'context',jsonb_build_object('client_id',stored.customer_id,'appointment_id',stored.meeting_id)));
  result:=jsonb_set(result,'{data,brief}',brief);
 end if;
 return result;
end $$;
revoke all on function public.rm_meeting_brief(uuid) from public,anon;
grant execute on function public.rm_meeting_brief(uuid) to authenticated;

create function meetings.check_copilot_brief_approval() returns trigger
language plpgsql security definer set search_path='' as $$
begin
 if new.status='approved' and old.status<>'approved' and not meetings.copilot_brief_current(new) then
  raise exception 'historical briefing sources, access or assignment changed; prepare and review a current version before approval' using errcode='P1001';
 end if;
 return new;
end $$;
revoke all on function meetings.check_copilot_brief_approval() from public,anon,authenticated,demobank_meetings,demobank_meeting_ingress;
create trigger copilot_brief_approval_current before update of status on meetings.copilot_briefs
for each row execute function meetings.check_copilot_brief_approval();

alter function public.rm_meeting_brief_approve(uuid,uuid,integer,uuid) rename to rm_meeting_brief_approve_before_lifecycle;
revoke all on function public.rm_meeting_brief_approve_before_lifecycle(uuid,uuid,integer,uuid) from public,anon,authenticated,demobank_meetings,demobank_meeting_ingress;
create function public.rm_meeting_brief_approve(p_meeting_id uuid,p_brief_id uuid,p_version integer,p_command_id uuid) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
declare a rm.appointments;b meetings.copilot_briefs;
begin
 a:=rm.brief_meeting(p_meeting_id);
 select * into b from meetings.copilot_briefs where meeting_id=a.appointment_id order by version desc limit 1 for update;
 if not found or b.brief_id<>p_brief_id or b.version<>p_version then
  raise exception 'approval references a replaced brief; read the latest version first' using errcode='P1001';end if;
 if not meetings.copilot_brief_current(b) then
  raise exception 'historical briefing sources, access or assignment changed; prepare and review a current version before approval' using errcode='P1001';end if;
 return public.rm_meeting_brief_approve_before_lifecycle(p_meeting_id,p_brief_id,p_version,p_command_id);
end $$;
revoke all on function public.rm_meeting_brief_approve(uuid,uuid,integer,uuid) from public,anon;
grant execute on function public.rm_meeting_brief_approve(uuid,uuid,integer,uuid) to authenticated;

-- Derive the incoming private-client product facts from the existing book-checked reader, keeping raw holdings out
-- of model evidence. A changed portfolio, loan or collateral status changes the canonical approval source hash.
alter function public.meeting_preparation_inputs(uuid) set schema meetings;
alter function meetings.meeting_preparation_inputs(uuid) rename to preparation_inputs_before_private_products;
revoke all on function meetings.preparation_inputs_before_private_products(uuid) from public,anon,authenticated,demobank_meetings,demobank_meeting_ingress;
create function public.meeting_preparation_inputs(p_meeting_id uuid) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
declare frame jsonb;profile jsonb;portfolio jsonb;financing jsonb;extra jsonb:='[]';a rm.appointments;item jsonb;
begin
 frame:=meetings.preparation_inputs_before_private_products(p_meeting_id);
 select * into a from rm.appointments where appointment_id=p_meeting_id;
 profile:=public.rm_client_profile(a.customer_id);
 if jsonb_typeof(profile->'data') is distinct from 'object' or jsonb_typeof(profile->'data'->'loans') is distinct from 'array' then
  raise exception 'the scoped profile reader must return its current loan collection' using errcode='22023';end if;
 for item in select value from jsonb_array_elements(profile->'data'->'loans') loop
  if item->>'type' is null or item->>'currency' is null or item->>'outstanding_amount' is null or item->>'status' is null then
   raise exception 'a current loan record is missing its product, currency, amount or status' using errcode='22023';end if;
  extra:=extra||jsonb_build_array(meetings.brief_source(p_meeting_id,'rm_client_profile','loan:'||meetings.hash(item),profile->>'call_id',
   format('Recorded financing product: %s. Outstanding: %s %s. Status: %s.',item->>'type',item->>'currency',item->>'outstanding_amount',item->>'status'),null,null));
 end loop;
 portfolio:=profile->'data'->'portfolio';financing:=profile->'data'->'financing';
 if portfolio is not null and portfolio<>'null'::jsonb then
  if jsonb_typeof(portfolio) is distinct from 'object' or jsonb_typeof(portfolio->'total_value') is distinct from 'number'
   or portfolio->>'mandate' is null or portfolio->>'currency' is null or portfolio->>'as_of' is null then
   raise exception 'the current portfolio summary is missing its mandate, value, currency or date' using errcode='22023';end if;
  extra:=extra||jsonb_build_array(meetings.brief_source(p_meeting_id,'rm_client_profile','portfolio',profile->>'call_id',
   format('Recorded investment portfolio: %s mandate, total %s %s, as of %s.',portfolio->>'mandate',portfolio->>'currency',portfolio->>'total_value',portfolio->>'as_of'),null,null));
 end if;
 if financing is not null and financing<>'null'::jsonb then
  if jsonb_typeof(financing) is distinct from 'object' or financing->>'currency' is null
   or financing->>'lombard_outstanding' is null or financing->>'lending_value' is null or financing->>'collateral_status' is null
   or financing->>'shortfall' is null then raise exception 'the current Lombard coverage record is incomplete' using errcode='22023';end if;
  extra:=extra||jsonb_build_array(meetings.brief_source(p_meeting_id,'rm_client_profile','financing',profile->>'call_id',
   format('Recorded Lombard borrowing: %s %s against a bank lending value of %s %s. Recorded collateral status: %s. Recorded shortfall: %s %s.',
    financing->>'currency',financing->>'lombard_outstanding',financing->>'currency',financing->>'lending_value',financing->>'collateral_status',financing->>'currency',financing->>'shortfall'),null,null));
  if financing->>'collateral_status'='below_limit' then
   extra:=extra||jsonb_build_array(meetings.brief_source(p_meeting_id,'rm_collateral_alert','current',profile->>'call_id',
    format('Recorded collateral alert: borrowing exceeds the bank lending value by %s %s. The RM must review the recorded shortfall with the client; only the bank decides any required action.',
     financing->>'currency',financing->>'shortfall'),null,null));
  end if;
 end if;
 if jsonb_array_length(frame->'sources')+jsonb_array_length(extra)>128 then
  raise exception 'preparation exceeds 128 sources after adding current product records; narrow the reader window before generation' using errcode='22023';end if;
 return frame||jsonb_build_object('sources',(frame->'sources')||extra,'source_hash',meetings.hash(jsonb_build_array(frame->>'source_hash',
  (select coalesce(jsonb_agg(value-'reference'),'[]') from jsonb_array_elements(extra)))));
end $$;
revoke all on function public.meeting_preparation_inputs(uuid) from public,anon;
grant execute on function public.meeting_preparation_inputs(uuid) to authenticated;

-- All meeting reads/writes take the appointment lock before appending to the global audit chain. Taking the
-- audit lock first here deadlocked concurrent workspace reads and the restricted graph worker.
create or replace function public.meeting_lifecycle_status(p_meeting_id uuid) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
declare a rm.appointments;result jsonb;
begin
 select * into a from rm.appointments where appointment_id=p_meeting_id;
 if not found then raise exception 'meeting appointment not found' using errcode='P0002';end if;
 if not coalesce(banking.my_customer_id()=a.customer_id or rm.current_advisor()=a.advisor_id and rm.in_book(a.advisor_id,a.customer_id),false) then
  raise exception 'meeting lifecycle is restricted to its current participants' using errcode='42501';end if;
 result:=meetings.sync_lifecycle(p_meeting_id);
 perform meetings.read_audit(a.customer_id,'meetings.threads',p_meeting_id::text,'meeting.lifecycle_status');
 return result;
end $$;
revoke all on function public.meeting_lifecycle_status(uuid) from public,anon;
grant execute on function public.meeting_lifecycle_status(uuid) to authenticated;

-- The additional clarification code is accepted only for an existing current collateral-alert source.
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
    or (item->>'kind'='question' and coalesce(item->>'question','') not in ('confirm_goal','review_status','clarify_next_step','discuss_shared_topic','check_information','compare_product_terms','confirm_document_next_step','review_collateral_next_step'))
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
