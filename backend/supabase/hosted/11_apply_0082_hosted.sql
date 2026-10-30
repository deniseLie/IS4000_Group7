-- Existing DemoBank project: apply only after reviewing hosted schema and RLS.
begin;

set local lock_timeout='10s';
set local statement_timeout='120s';

select pg_advisory_xact_lock(hashtext('demobank.hosted_migrations'));

do $apply_0082$
declare previous supabase_migrations.schema_migrations; script text:=$source_0082$-- Released source SHA256: f8d04e742c276b3bd41111ae94b9d158c92256db3b745cfb0e3e874cc3905d5d
-- Code-review follow-ups for the client persona (CR8, CR9, CR12, CR14, CR15). Synthetic data only.
--
-- 1. A lost or stolen card reported by a signed-in client becomes an urgent fraud ticket (handoff reason card_lost),
--    worded as a lost card rather than as charges the client does not recognise.
-- 2. A question for the relationship manager on a topic that already has an open RM ticket is added to that ticket as a
--    client update instead of opening a duplicate (customer_ticket_follow_up). The assistant decides the topic by rules.
-- 3. Documents the client selected in the chat can be attached to the RM ticket (support.ticket_attachments); only her
--    own registered documents and her own open tickets qualify. My requests lists them by file name.
-- 4. Demo data: Michelle gets a second card (Travel Card ending 5592) so card questions can ask which card she means,
--    and a bank fee on her multi-currency account so fee questions have a real charge to explain.
-- Nothing here lets the assistant decide, approve or close anything.

-- 1. Lost or stolen card handoffs ----------------------------------------------------------------------------------
create or replace function actions.handoff_target(p_target jsonb,p_customer_id bigint)
returns jsonb language plpgsql volatile security definer set search_path=''
as $$
begin
  if jsonb_typeof(p_target->'reason') is distinct from 'string' or p_target->>'reason' not in ('card_verification','card_dispute','card_lost','general_support')
      or jsonb_typeof(p_target->'priority') is distinct from 'string' or p_target->>'priority' not in ('P0','P1','P2','P3')
      or jsonb_typeof(p_target->'summary') is distinct from 'string' or length(btrim(p_target->>'summary')) not between 1 and 1000 then
    raise exception 'handoff.create requires a registered reason, priority and redacted summary of at most 1000 characters' using errcode='22023';
  end if;
  perform conversation.require_redacted(p_target->>'summary');
  return jsonb_build_object('reason',p_target->>'reason','priority',p_target->>'priority','summary',p_target->>'summary');
end $$;
revoke all on function actions.handoff_target(jsonb,bigint) from public,anon,authenticated;

create or replace function support.ticket_for_handoff() returns trigger language plpgsql security definer set search_path='' as $$
begin
  if tg_op='INSERT' and new.customer_id is not null then
    perform support.open_ticket(new.customer_id,new.case_id,
      case when new.reason in ('card_verification','card_dispute','card_lost') then 'suspected_fraud' else 'rm_handover' end,
      case new.reason when 'card_verification' then 'Card charges reported as not recognised: the fraud team contacts you, blocks the card if needed and reviews the charges.'
        when 'card_dispute' then 'Disputed card charges for bank staff review.'
        when 'card_lost' then 'Card reported lost or stolen: the fraud team contacts you, blocks the card and arranges a replacement if needed.'
        else left(new.summary,500) end,
      '{}',case new.priority when 'P0' then 'urgent' when 'P1' then 'high' else 'normal' end,'handoff',p_handoff_id=>new.handoff_id);
  elsif tg_op='UPDATE' and new.status is distinct from old.status then
    perform support.move_ticket(t.ticket_id,case new.status when 'claimed' then 'in_review' when 'closed' then 'resolved' else 'submitted' end)
      from support.tickets t where t.handoff_id=new.handoff_id;
  end if;
  return null;
end $$;

-- 2. Adding a client question to an open RM ticket -------------------------------------------------------------------
-- The note is the client's own redacted words. Repeating the latest note (a retried turn) adds nothing.
create function public.customer_ticket_follow_up(p_customer_id bigint,p_reference text,p_note text)
returns jsonb language plpgsql volatile security definer set search_path='' as $$
declare cid bigint:=banking.require_read_customer(p_customer_id); ticket support.tickets; note text; added boolean:=false;
begin
  if p_reference is null or p_reference !~ '^TCK-[0-9]{6,}$' or p_note is null or btrim(p_note)='' or length(p_note)>2000 then
    raise exception 'a follow-up needs a ticket reference and a short note' using errcode='22023';
  end if;
  perform conversation.require_redacted(p_note);
  select * into ticket from support.tickets where reference=p_reference and customer_id=cid for update;
  if not found or ticket.category<>'rm_handover' or ticket.status not in ('submitted','in_review','waiting_on_client') then
    raise exception 'only your own open request to your relationship manager can take a follow-up' using errcode='42501';
  end if;
  note:=left('You added: '||btrim(regexp_replace(p_note,'\s+',' ','g')),500);
  if not exists(select 1 from support.ticket_events e where e.ticket_id=ticket.ticket_id and e.public_note=note
      and e.event_id=(select max(event_id) from support.ticket_events where ticket_id=ticket.ticket_id)) then
    insert into support.ticket_events(ticket_id,customer_id,status,public_note) values(ticket.ticket_id,cid,ticket.status,note);
    update support.tickets set updated_at=clock_timestamp() where ticket_id=ticket.ticket_id;
    perform audit.record('handoff','handoff.ticket_followed_up','success','support.tickets',ticket.ticket_id::text,
      jsonb_build_object('category',ticket.category,'status',ticket.status),ticket.case_id,p_customer_id=>cid);
    added:=true;
  end if;
  return jsonb_build_object('reference',ticket.reference,'status',ticket.status,'added',added);
end $$;
revoke all on function public.customer_ticket_follow_up(bigint,text,text) from public,anon;
grant execute on function public.customer_ticket_follow_up(bigint,text,text) to authenticated;

-- 3. Attachments on RM tickets ---------------------------------------------------------------------------------------
create table support.ticket_attachments (
  ticket_id   uuid not null references support.tickets(ticket_id) on delete cascade,
  document_id uuid not null references compliance.documents(document_id) on delete cascade,
  customer_id bigint not null references banking.customer_profiles(customer_id) on delete cascade,
  attached_at timestamptz not null default now(),
  primary key (ticket_id,document_id)
);
create index ticket_attachments_customer_idx on support.ticket_attachments(customer_id);
alter table support.ticket_attachments enable row level security;
grant select on support.ticket_attachments to authenticated;
create policy own_rows on support.ticket_attachments for select to authenticated using (customer_id=(select banking.my_customer_id()));

create function public.customer_attach_ticket_documents(p_customer_id bigint,p_reference text,p_document_ids uuid[])
returns jsonb language plpgsql volatile security definer set search_path='' as $$
declare cid bigint:=banking.require_read_customer(p_customer_id); ticket support.tickets; attached int:=0;
begin
  if p_reference is null or p_reference !~ '^TCK-[0-9]{6,}$' or p_document_ids is null or cardinality(p_document_ids) not between 1 and 5 then
    raise exception 'attach between one and five documents to a ticket reference' using errcode='22023';
  end if;
  select * into ticket from support.tickets where reference=p_reference and customer_id=cid for update;
  if not found or ticket.category<>'rm_handover' or ticket.status not in ('submitted','in_review','waiting_on_client') then
    raise exception 'documents can only be attached to your own open request to your relationship manager' using errcode='42501';
  end if;
  if exists(select 1 from unnest(p_document_ids) d where not exists(select 1 from compliance.documents x where x.document_id=d and x.customer_id=cid)) then
    raise exception 'only your own registered documents can be attached' using errcode='42501';
  end if;
  insert into support.ticket_attachments(ticket_id,document_id,customer_id)
    select ticket.ticket_id,d,cid from unnest(p_document_ids) d on conflict do nothing;
  get diagnostics attached=row_count;
  if attached>0 then
    perform audit.record('handoff','handoff.ticket_attached','success','support.tickets',ticket.ticket_id::text,
      jsonb_build_object('documents',attached),ticket.case_id,p_customer_id=>cid);
  end if;
  return jsonb_build_object('reference',ticket.reference,'attached',attached);
end $$;
revoke all on function public.customer_attach_ticket_documents(bigint,text,uuid[]) from public,anon;
grant execute on function public.customer_attach_ticket_documents(bigint,text,uuid[]) to authenticated;

-- Same read as 0073 plus attachment file names (no storage paths).
create or replace function public.customer_read_tickets(p_customer_id bigint)
returns jsonb language plpgsql volatile security invoker set search_path='' as $$
declare cid bigint:=banking.require_read_customer(p_customer_id); data jsonb;
begin
  select coalesce(jsonb_agg(item order by (item->>'created_at')::timestamptz desc),'[]') into data from (
    select jsonb_build_object('ticket_id',t.ticket_id,'reference',t.reference,'category',t.category,'category_label',c.label,
      'summary',t.summary,'details',t.details,'urgency',t.urgency,'status',t.status,'owner_team',t.owner_team,'decided_by',t.decided_by,
      'source',t.source,'handoff_id',t.handoff_id,'created_at',t.created_at,'updated_at',t.updated_at,'target_at',t.target_at,
      'overdue',t.status in ('submitted','in_review') and t.target_at<clock_timestamp(),
      'last_update',(select jsonb_build_object('at',e.created_at,'status',e.status,'note',e.public_note) from support.ticket_events e
        where e.ticket_id=t.ticket_id and not e.internal order by e.created_at desc,e.event_id desc limit 1),
      'attachments',(select coalesce(jsonb_agg(jsonb_build_object('document_id',d.document_id,'file_name',d.file_name) order by a.attached_at),'[]')
        from support.ticket_attachments a join compliance.documents d on d.document_id=a.document_id and d.customer_id=cid
        where a.ticket_id=t.ticket_id)) item
    from support.tickets t join support.request_categories c using(category)
    where t.customer_id=cid order by t.created_at desc limit 200) x;
  return banking.customer_result(cid,'tickets.list','support.tickets',data);
end $$;

create trigger audit_row after insert or update or delete on support.ticket_attachments for each row execute function audit.row_change('ticket_id','document_id');

-- 4. Demo data for Michelle (1002) -----------------------------------------------------------------------------------
-- A second card, so "what is my card balance?" has to ask which card. Its balance equals its own charges below.
insert into banking.credit_cards(card_id,customer_id,card_name,network,last4,credit_limit,outstanding_balance,minimum_payment_due,payment_due_date,status) values
  (10022,1002,'Travel Card','visa','5592',10000.00,316.20,50.00,'2026-11-25','active')
on conflict(card_id) do nothing;

-- The Travel Card's annual fee and one purchase; a bank fee on the multi-currency account for fee questions (CR15).
insert into banking.transactions (transaction_id, customer_id, account_id, card_id, txn_date, merchant, description, amount, status, is_overseas, merchant_country) values
  (100239, 1002, 10021, null,  '2026-10-03 09:31+08', 'DemoBank fee', 'Incoming telegraphic transfer fee', -25.00, 'posted', false, null),
  (100240, 1002, null,  10022, '2026-10-05 00:05+08', 'DemoBank fee', 'Annual card fee, Travel Card', -196.20, 'posted', false, 'SG'),
  (100241, 1002, null,  10022, '2026-10-10 19:15+08', 'Changi Duty Free', 'Card purchase', -120.00, 'posted', false, 'SG')
on conflict (transaction_id) do nothing;
$source_0082$;
begin
  select * into previous from supabase_migrations.schema_migrations where version='0082';
  if found then
    if previous.name is distinct from 'ticket_followups_and_lost_card' or previous.statements is distinct from array[script] then
      raise exception 'migration 0082 is recorded with different SQL; inspect history before proceeding';
    end if;
    raise notice 'migration 0082 already applied with identical SQL';
  else
    execute script;
    insert into supabase_migrations.schema_migrations(version,name,statements)
      values('0082','ticket_followups_and_lost_card',array[script]);
  end if;
end $apply_0082$;

commit;
select version,name from supabase_migrations.schema_migrations order by version;
