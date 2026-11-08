-- Consent withdrawal by request (PDPC Advisory Guidelines on Key Concepts, ch. 12: 12.38 deemed consent can be withdrawn,
-- 12.41 about 10 business days is reasonable, 12.50 tell her the consequences, 12.52 stop using the data). The Data sharing
-- switch (consent.revoke, 0109) stays instant and opens no ticket. This adds the route for what the switch cannot do:
--   1. Category consent_withdrawal ("Stop using my data"): raised by the client (My requests, or the chat's offer) or by her
--      RM (rm_create_service_request); owned by the Data protection team, worked from the SQL editor like data_deletion
--      (0113); 10 business days. The RM can follow it and add messages but not change its status (0089 rm_send_client_update).
--   2. support.add_internal_note: an owner-only note on the ticket's timeline, never returned to the client.
--   3. consent.recording_opt_outs: a standing per-client "do not record my meetings". consent.after_appointment creates no
--      notice-based recording grant for an opted-out client. consent.recording_opted_out(customer) is the check the meeting
--      side's meetings.recording_allowed (0121) is to call; that change is the meeting side's and is not made here.
--   4. consent.withdraw_by_request(reference, category): ends her active grants of that category (end_reason withdrawn; the
--      0110 chat-topics trigger still forgets inferred topics) and, for meeting_recording, records the opt-out.
--   5. consent.revoked notice v2 in plain words.
-- Audit: consent.withdrawn (per grant), consent.recording_opted_out, handoff.ticket_note_added; ids and codes only.

-- 1. The request category ---------------------------------------------------------------------------------------------
insert into support.request_categories(category,label,description,owner_team,decided_by,target_hours,fields,documents,kb_article,customer_raisable) values
 ('consent_withdrawal','Stop using my data','Ask DemoBank to stop using your personal data for something you agreed to or were told about, '
  'such as recording your meetings. To stop sharing transaction insights or chat insights with your relationship manager now, switch them off in Data sharing.',
  'Data protection team','The data protection team checks what the bank must keep by law and tells you what changes.',240,
  '[{"key":"what","label":"What should we stop using","kind":"choice","required":true,
     "choices":["Meeting recordings","Transaction insights","Chat insights","Something else"]},
    {"key":"details","label":"Anything the team should know (optional)","kind":"text","required":false}]','{}',null,true);

-- The same 10-business-day target as data_deletion (0113), by the end of the day in Singapore.
create trigger consent_withdrawal_target before insert on support.tickets for each row when (new.category='consent_withdrawal')
  execute function support.data_deletion_target();

-- 2. Internal notes ---------------------------------------------------------------------------------------------------
-- A note for the team's own record (for example how her identity was confirmed). It keeps the ticket's status and is
-- internal: 0071's own_public_rows policy, customer_read_tickets and rm.ticket_json never return it.
create function support.add_internal_note(p_reference text,p_note text) returns void
language plpgsql volatile security definer set search_path='' as $$
declare t support.tickets;
begin
  select * into t from support.tickets where reference=p_reference;
  if not found then raise exception 'ticket % not found',p_reference using errcode='P0002'; end if;
  if p_note is null or length(btrim(p_note)) not between 1 and 500 then raise exception 'a note of 1-500 characters is required' using errcode='22023'; end if;
  perform conversation.require_redacted(p_note);
  insert into support.ticket_events(ticket_id,customer_id,status,public_note,internal) values(t.ticket_id,t.customer_id,t.status,btrim(p_note),true);
  perform audit.record('handoff','handoff.ticket_note_added','success','support.tickets',t.ticket_id::text,
    jsonb_build_object('category',t.category,'internal',true),t.case_id,p_customer_id=>t.customer_id);
end $$;

-- 3. Recording opt-out ------------------------------------------------------------------------------------------------
-- One row per client who asked the bank to stop recording her meetings. No policies and no grants: only the owner and
-- the security-definer functions below read it.
create table consent.recording_opt_outs (
  customer_id  bigint primary key references banking.customer_profiles(customer_id) on delete cascade,
  ticket_id    uuid not null references support.tickets(ticket_id),
  opted_out_at timestamptz not null default clock_timestamp()
);
alter table consent.recording_opt_outs enable row level security;

create function consent.recording_opted_out(p_customer bigint) returns boolean language sql stable security definer set search_path='' as $$
  select exists(select 1 from consent.recording_opt_outs where customer_id=p_customer)
$$;

-- 0109's trigger, unchanged except that an opted-out client gets no notice-based recording grant.
create or replace function consent.after_appointment() returns trigger language plpgsql security definer set search_path='' as $$
declare g uuid;
begin
  if new.status='confirmed' and (tg_op='INSERT' or old.status is distinct from 'confirmed') and not consent.recording_opted_out(new.customer_id)
      and not exists(select 1 from consent.grants x
      where x.customer_id=new.customer_id and x.purpose='rm_brief' and x.recipient=new.advisor_id::text and 'meeting_recording'=any(x.categories)
        and x.revoked_at is null and (x.expires_at is null or x.expires_at>clock_timestamp())) then
    insert into consent.grants(customer_id,purpose,recipient,categories,granted_at,scope,basis,notice_version)
      values(new.customer_id,'rm_brief',new.advisor_id::text,array['meeting_recording'],clock_timestamp(),'{}','notice',new.recording_notice_version)
      returning grant_id into g;
    perform audit.record('consent','consent.noticed','success','consent.grants',g::text,
      jsonb_build_object('category','meeting_recording','notice_version',new.recording_notice_version,'meeting_id',new.appointment_id),p_customer_id=>new.customer_id);
  end if;
  if tg_op='UPDATE' and new.status='confirmed' and new.ends_at is distinct from old.ends_at then
    update consent.grants set expires_at=new.ends_at+interval '30 days' where until_meeting_id=new.appointment_id and revoked_at is null;
  end if;
  if tg_op='UPDATE' and new.status='cancelled' and old.status<>'cancelled' then
    perform consent.close_meeting_grants(new.appointment_id,'meeting_cancelled');
  end if;
  return null;
end $$;

-- 4. Withdrawal by request --------------------------------------------------------------------------------------------
alter table consent.grants drop constraint grants_end_reason_check;
alter table consent.grants add constraint grants_end_reason_check
  check (end_reason in ('revoked','replaced','meeting_closed','meeting_cancelled','request_closed','withdrawn'));

-- The team's step once it has checked the request (docs/workflows/consent-withdrawal-runbook.md). The ticket decides whose
-- grants end; the category is the team's reading of her request. A grant of that category alone ends (withdrawn); an
-- earlier 0037 grant covering several categories stops covering this one. Ending a chat_topics grant fires 0110's
-- insights.grant_changed, so the chat insights picked up from her chats go at once.
create function consent.withdraw_by_request(p_reference text,p_category text) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
declare t support.tickets; g consent.grants; ended int:=0; narrowed int; opted int:=0;
begin
  if p_category is null or p_category not in ('transaction_insights','chat_topics','meeting_recording') then
    raise exception 'category must be transaction_insights, chat_topics or meeting_recording' using errcode='22023';
  end if;
  select * into t from support.tickets where reference=p_reference for update;
  if not found then raise exception 'ticket % not found',p_reference using errcode='P0002'; end if;
  if t.category<>'consent_withdrawal' then raise exception 'ticket % is not a consent withdrawal request',p_reference using errcode='22023'; end if;
  if t.status not in ('submitted','in_review','waiting_on_client') then raise exception 'ticket % is not open',p_reference using errcode='P1001'; end if;
  perform pg_advisory_xact_lock(hashtext('consent.customer'),hashtext(t.customer_id::text));
  for g in update consent.grants set revoked_at=clock_timestamp(),end_reason='withdrawn'
      where customer_id=t.customer_id and purpose='rm_brief' and categories=array[p_category] and revoked_at is null
        and (expires_at is null or expires_at>clock_timestamp()) returning * loop
    ended:=ended+1;
    perform audit.record('consent','consent.withdrawn','success','consent.grants',g.grant_id::text,
      jsonb_build_object('category',p_category,'reference',t.reference),t.case_id,p_customer_id=>t.customer_id);
  end loop;
  update consent.grants set categories=array_remove(categories,p_category)
    where customer_id=t.customer_id and purpose='rm_brief' and p_category=any(categories) and cardinality(categories)>1 and revoked_at is null;
  get diagnostics narrowed=row_count;
  if p_category='meeting_recording' then
    insert into consent.recording_opt_outs(customer_id,ticket_id) values(t.customer_id,t.ticket_id) on conflict (customer_id) do nothing;
    get diagnostics opted=row_count;
    if opted>0 then
      perform audit.record('consent','consent.recording_opted_out','success','support.tickets',t.ticket_id::text,
        jsonb_build_object('category',p_category,'reference',t.reference),t.case_id,p_customer_id=>t.customer_id);
    end if;
  end if;
  return jsonb_build_object('reference',t.reference,'customer_id',t.customer_id,'category',p_category,'grants_ended',ended,'grants_narrowed',narrowed,
    'recording_opted_out',consent.recording_opted_out(t.customer_id));
end $$;

revoke all on function support.add_internal_note(text,text),consent.recording_opted_out(bigint),consent.withdraw_by_request(text,text)
  from public,anon,authenticated;
revoke all on consent.recording_opt_outs from public,anon,authenticated;

-- 5. The notice after she switches something off on Data sharing, in plain words. The consent branch of notify.deliver
-- renders it without variables, so it names no category or person.
insert into notify.templates(template_key,version,locale,channel,title,body,is_service) values
  ('consent.revoked',2,'en','in_app','Sharing stopped','You switched off sharing with your relationship manager, and it stopped at once: they no longer see '
   'what you switched off. Records the bank must keep by law are kept. To share again, or to see what you still share, open Data sharing. The receipt is in Activity.',true);
