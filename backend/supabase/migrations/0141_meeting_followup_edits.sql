-- Follow up step (design: FollowUp): the RM edits each follow-up (task text, owner, due date) and the client recap
-- before she sends them. The approved minutes are not changed: the exact approved version (hash) is still required.
-- The edits are part of the idempotent command (a replay with other edits is refused), the follow-up stores the
-- edited action with "edited": true, the audit row records that it was edited, and the shared recap shows the
-- text she sent. Nothing is sent before she presses Send.

drop function public.meeting_create_followups(uuid,uuid,int,text,int,int,uuid[],uuid);
create function public.meeting_create_followups(p_meeting_id uuid,p_draft_id uuid,p_version int,p_content_hash text,p_revision int,p_consent_epoch int,p_action_ids uuid[],p_command_id uuid,p_edits jsonb default null) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
declare d meetings.drafts; a rm.appointments; action jsonb; edit jsonb; id uuid; computed_key text; existing meetings.followups; task jsonb; task_id uuid; result jsonb:='[]'; receipt jsonb;
  params jsonb:=jsonb_build_array(p_draft_id,p_version,p_content_hash,p_revision,p_consent_epoch,to_jsonb(p_action_ids),coalesce(p_edits,'{}'::jsonb));
begin
  d:=meetings.require_draft(p_meeting_id,p_draft_id,p_version,p_content_hash,p_revision,p_consent_epoch);
  if d.note_id is null then raise exception 'approve the exact minutes before approving follow-up creation' using errcode='P1001'; end if;
  if p_action_ids is null or cardinality(p_action_ids) not between 1 and 10 or cardinality(p_action_ids)<>(select count(distinct v) from unnest(p_action_ids) v) then raise exception 'select 1-10 distinct agreed action IDs' using errcode='22023'; end if;
  if p_edits is not null and (jsonb_typeof(p_edits)<>'object' or exists(select 1 from jsonb_object_keys(p_edits) k where k not in (select v::text from unnest(p_action_ids) v))) then
    raise exception 'edits must name selected action IDs' using errcode='22023'; end if;
  receipt:=meetings.replay(p_command_id,p_meeting_id,'create_followups',params); if receipt is not null then return receipt; end if;
  select * into a from rm.appointments where appointment_id=p_meeting_id;
  foreach id in array p_action_ids loop
    select value into action from jsonb_array_elements(d.content->'actions') where (value->>'action_id')::uuid=id;
    if action is null or action->>'commitment'<>'agreed' or action->>'owner' not in ('rm','client') then
      raise exception 'only explicitly selected, agreed actions with an identified owner can become follow-ups' using errcode='22023'; end if;
    computed_key:=meetings.hash(jsonb_build_array(d.source_id,action-'action_id'));  -- the minutes item, not the edit
    edit:=p_edits->(id::text);
    if edit is not null then
      if jsonb_typeof(edit)<>'object' or exists(select 1 from jsonb_object_keys(edit) k where k not in ('text','owner','due_on')) then
        raise exception 'a follow-up edit may change only text, owner and due_on' using errcode='22023'; end if;
      if edit ? 'text' and (jsonb_typeof(edit->'text')<>'string' or char_length(btrim(edit->>'text')) not between 3 and 300) then
        raise exception 'follow-up text must be 3-300 characters' using errcode='22023'; end if;
      if edit ? 'owner' and coalesce(edit->>'owner','') not in ('rm','client') then raise exception 'follow-up owner must be rm or client' using errcode='22023'; end if;
      if edit ? 'due_on' and jsonb_typeof(edit->'due_on')<>'null' and (jsonb_typeof(edit->'due_on')<>'string' or (edit->>'due_on') !~ '^\d{4}-\d{2}-\d{2}$') then
        raise exception 'due_on must be a date (YYYY-MM-DD) or null' using errcode='22023'; end if;
      if edit ? 'due_on' and jsonb_typeof(edit->'due_on')='string' then perform (edit->>'due_on')::date; end if;
      action:=action||jsonb_build_object('text',coalesce(btrim(edit->>'text'),action->>'text'),'owner',coalesce(edit->>'owner',action->>'owner'),
        'due_on',case when edit ? 'due_on' then edit->'due_on' else coalesce(action->'due_on','null'::jsonb) end,'edited',true);
    end if;
    select * into existing from meetings.followups f where f.meeting_id=p_meeting_id and f.item_key=computed_key;
    if found then result:=result||jsonb_build_array(jsonb_build_object('followup_id',existing.followup_id,'status',existing.status)); continue; end if;
    task_id:=null;
    if action->>'owner'='rm' then
      task:=public.rm_create_tasks(a.customer_id,d.note_id,jsonb_build_array(jsonb_build_object('request_key',id,'title',action->>'text','owner','RM',
        'details','Approved meeting follow-up','due_on',action->>'due_on')));
      if jsonb_array_length(task->'created')>0 then task_id:=(task#>>'{created,0,task_id}')::uuid;
      elsif jsonb_array_length(task->'duplicates')>0 then task_id:=(task#>>'{duplicates,0,existing_task_id}')::uuid;
      else raise exception 'approved RM task was not created or linked' using errcode='P1001'; end if;
    end if;
    insert into meetings.followups(followup_id,meeting_id,draft_id,source_id,item_key,action,owner_role,task_id,status)
      values(id,p_meeting_id,d.draft_id,d.source_id,computed_key,action,action->>'owner',task_id,'open');
    perform audit.record('action','action.meeting_followup_created','success','meetings.followups',id::text,
      jsonb_build_object('meeting_id',p_meeting_id,'draft_id',d.draft_id,'edited',edit is not null),p_actor_type=>'staff',p_actor_id=>auth.uid()::text,p_customer_id=>a.customer_id);
    result:=result||jsonb_build_array(jsonb_build_object('followup_id',id,'status','open'));
  end loop;
  return meetings.remember(p_command_id,p_meeting_id,'create_followups',params,jsonb_build_object('followups',result));
end $$;

drop function public.meeting_share_recap(uuid,uuid,int,text,int,int,uuid);
create function public.meeting_share_recap(p_meeting_id uuid,p_draft_id uuid,p_version int,p_content_hash text,p_revision int,p_consent_epoch int,p_command_id uuid,p_text text default null) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
declare d meetings.drafts;a rm.appointments;receipt jsonb;message uuid:=gen_random_uuid();recap text;
  params jsonb:=jsonb_build_array(p_draft_id,p_version,p_content_hash,p_revision,p_consent_epoch,p_text);
begin
 d:=meetings.require_draft(p_meeting_id,p_draft_id,p_version,p_content_hash,p_revision,p_consent_epoch);
 if d.note_id is null then raise exception 'approve the exact minutes before sharing the recap' using errcode='P1001';end if;
 if p_text is not null and char_length(btrim(p_text)) not between 10 and 2000 then raise exception 'the recap must be 10-2000 characters' using errcode='22023';end if;
 recap:=coalesce(btrim(p_text),d.content->>'client_recap');
 receipt:=meetings.replay(p_command_id,p_meeting_id,'share_recap',params);if receipt is not null then return receipt;end if;
 select e.receipt into receipt from meetings.effects e where draft_id=d.draft_id and scope='recap' and target_key='client';
 if receipt is null then
  select * into a from rm.appointments where appointment_id=p_meeting_id;
  -- The inbox remains a short notification (0128); the shared workspace shows the full recap she sent.
  receipt:=public.rm_send_client_update(message,a.customer_id,null,d.content->'summary'->0->>'text',null,least(d.version,50),false);
  if receipt->>'denied'='true' then raise exception 'RM authorization changed before sharing' using errcode='42501';end if;
  receipt:=jsonb_build_object('message_id',message,'draft_id',d.draft_id,'shared',true,'edited',p_text is not null,'recap_text',recap);
  insert into meetings.effects values(d.draft_id,'recap','client',receipt);
  perform audit.record('action','action.meeting_recap_shared','success','meetings.effects',d.draft_id::text,
    jsonb_build_object('meeting_id',p_meeting_id,'edited',p_text is not null),p_actor_type=>'staff',p_actor_id=>auth.uid()::text,p_customer_id=>a.customer_id);
 end if;
 return meetings.remember(p_command_id,p_meeting_id,'share_recap',params,receipt);
end $$;

-- The shared recap is the text she sent (falls back to the approved draft text for recaps shared before 0141).
alter function public.meeting_view(uuid) rename to meeting_view_before_edits;
alter function public.meeting_view_before_edits(uuid) set schema meetings;
revoke all on function meetings.meeting_view_before_edits(uuid) from public,anon,authenticated;
create function public.meeting_view(p_meeting_id uuid) returns jsonb language plpgsql volatile security definer set search_path='' as $$
declare result jsonb;sent text;
begin
 result:=meetings.meeting_view_before_edits(p_meeting_id);
 select e.receipt->>'recap_text' into sent from meetings.effects e join meetings.drafts d using(draft_id)
 where d.meeting_id=p_meeting_id and e.scope='recap' and e.target_key='client' order by d.version desc limit 1;
 return case when sent is null then result else result||jsonb_build_object('shared_recap',sent) end;
end $$;

do $$ declare f text; begin
  foreach f in array array['public.meeting_create_followups(uuid,uuid,int,text,int,int,uuid[],uuid,jsonb)','public.meeting_share_recap(uuid,uuid,int,text,int,int,uuid,text)','public.meeting_view(uuid)'] loop
    execute format('revoke all on function %s from public,anon,demobank_meetings,demobank_meeting_ingress',f);
    execute format('grant execute on function %s to authenticated',f);
  end loop;
end $$;
