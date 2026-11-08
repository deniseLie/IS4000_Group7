-- PDF rendering reads an exact persisted approved recap; clients receive only their own follow-up rows.
create function public.meeting_recap_document(p_meeting_id uuid) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
declare w meetings.workflows;d meetings.drafts;own_client boolean;
begin
 w:=meetings.require_participant(p_meeting_id);
 own_client:=coalesce(banking.my_customer_id()=(select customer_id from rm.appointments where appointment_id=p_meeting_id),false);
 select draft.* into d from meetings.drafts draft join meetings.effects effect using(draft_id)
 where draft.meeting_id=p_meeting_id and draft.note_id is not null and effect.scope='recap' and effect.target_key='client'
 order by draft.version desc limit 1;
 if not found then return null;end if;
 perform meetings.read_audit((select customer_id from rm.appointments where appointment_id=p_meeting_id),
  'meetings.drafts',d.draft_id::text,'meeting.recap_document');
 return jsonb_build_object('meeting_id',p_meeting_id,'document_id',d.draft_id,'version',d.version,'content_hash',d.content_hash,
  'purpose',w.purpose,'text',d.content->>'client_recap','followups',
  (select coalesce(jsonb_agg(jsonb_build_object('owner',f.owner_role,'text',f.action->>'text','status',f.status,'due_on',f.action->'due_on') order by f.followup_id),'[]')
   from meetings.followups f where f.meeting_id=p_meeting_id and (not own_client or f.owner_role='client')));
end $$;
revoke all on function public.meeting_recap_document(uuid) from public,anon;
grant execute on function public.meeting_recap_document(uuid) to authenticated;
