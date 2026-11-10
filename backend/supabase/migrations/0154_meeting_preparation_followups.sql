-- Previously reviewed briefs need a new version when the rendering rules change.
alter function public.meeting_preparation_inputs(uuid) set schema meetings;
alter function meetings.meeting_preparation_inputs(uuid) rename to preparation_inputs_before_topic_followups;
revoke all on function meetings.preparation_inputs_before_topic_followups(uuid) from public,anon,authenticated,demobank_meetings,demobank_meeting_ingress;

create function public.meeting_preparation_inputs(p_meeting_id uuid) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
declare frame jsonb;
begin
 frame:=meetings.preparation_inputs_before_topic_followups(p_meeting_id);
 return frame||jsonb_build_object('source_hash',meetings.hash(jsonb_build_array(frame->>'source_hash','brief-topic-followups-v1')));
end $$;
revoke all on function public.meeting_preparation_inputs(uuid) from public,anon;
grant execute on function public.meeting_preparation_inputs(uuid) to authenticated;

-- Keep older stored packs readable and allow new fixed questions only in their relevant contexts.
create or replace function public.meeting_save_pack(p_meeting_id uuid,p_revision int,p_source_hash text,p_amounts jsonb,p_plan jsonb,p_command_id uuid) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
declare w meetings.workflows;inputs jsonb;item jsonb;products jsonb;content jsonb;doc meetings.packs;next_version int;receipt jsonb;
 params jsonb:=jsonb_build_array(p_revision,p_source_hash,p_amounts,p_plan);chosen text[]:='{}';
begin
 w:=meetings.require_rm(p_meeting_id);
 receipt:=meetings.replay(p_command_id,p_meeting_id,'save_pack',params);if receipt is not null then return receipt;end if;
 inputs:=meetings.pack_inputs(p_meeting_id,p_amounts);
 if p_revision is distinct from w.revision or p_source_hash is distinct from inputs->>'source_hash' then raise exception 'agenda or catalogue terms changed during drafting; create a new pack' using errcode='P1001';end if;
 if p_plan is null or (select array_agg(key order by key) from jsonb_object_keys(p_plan) key) is distinct from array['agenda','product_ids','questions']
  or jsonb_typeof(p_plan->'agenda') is distinct from 'array' or jsonb_array_length(p_plan->'agenda') not between 1 and 6
  or jsonb_typeof(p_plan->'questions') is distinct from 'array' or jsonb_array_length(p_plan->'questions')>6
  or jsonb_typeof(p_plan->'product_ids') is distinct from 'array' or jsonb_array_length(p_plan->'product_ids')>5
 then raise exception 'pack plan requires supported agenda/question codes and catalogue references' using errcode='22023';end if;
 if (select count(*)<>count(distinct value) from jsonb_array_elements(p_plan->'agenda'))
  or (select count(*)<>count(distinct value) from jsonb_array_elements(p_plan->'questions'))
 then raise exception 'pack agenda and question codes must be distinct' using errcode='22023';end if;
 for item in select value from jsonb_array_elements(p_plan->'agenda') loop
  if jsonb_typeof(item)<>'string' or item#>>'{}' not in ('goals','status','documents','options','next_steps','questions') then raise exception 'unsupported pack agenda code' using errcode='22023';end if;
 end loop;
 for item in select value from jsonb_array_elements(p_plan->'questions') loop
  if jsonb_typeof(item)<>'string' or item#>>'{}' not in ('clarify_objective','confirm_documents','review_options','confirm_next_steps','ask_product_risks','confirm_timeframe','confirm_document_review','compare_catalogue_options')
   or (item#>>'{}'='confirm_document_review' and not coalesce((inputs->'topics') ? 'compliance_review',false))
   or (item#>>'{}'='compare_catalogue_options' and jsonb_array_length(p_plan->'product_ids')=0)
  then raise exception 'unsupported client-safe question code for this agenda' using errcode='22023';end if;
 end loop;
 for item in select value from jsonb_array_elements(p_plan->'product_ids') loop
  if jsonb_typeof(item)<>'string' or item#>>'{}'=any(chosen) or not exists(select 1 from jsonb_array_elements(inputs->'catalogue') x where x->>'item_id'=item#>>'{}')
  then raise exception 'the pack may reference only distinct current catalogue items for the agreed topics' using errcode='22023';end if;
  chosen:=array_append(chosen,item#>>'{}');
 end loop;
 select coalesce(jsonb_agg(x order by x->>'code'),'[]') into products from jsonb_array_elements(inputs->'catalogue') x where x->>'item_id'=any(chosen);
 content:=jsonb_build_object('kind','meeting_pack','title','Meeting discussion pack','purpose',inputs->'purpose','topics',inputs->'topics',
  'agenda',p_plan->'agenda','questions',p_plan->'questions','products',products,'stated_amounts',p_amounts,'as_of',inputs->'as_of');
 select coalesce(max(p.version),0)+1 into next_version from meetings.packs p where meeting_id=p_meeting_id;
 insert into meetings.packs(document_id,meeting_id,version,revision,source_hash,content_hash,plan,content,amounts,status)
 values(p_command_id,p_meeting_id,next_version,w.revision,p_source_hash,meetings.hash(content),p_plan,content,p_amounts,'draft') returning * into doc;
 return meetings.remember(p_command_id,p_meeting_id,'save_pack',params,to_jsonb(doc));
end $$;
