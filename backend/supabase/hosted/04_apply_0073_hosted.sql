-- Existing DemoBank project: apply only after reviewing hosted schema and RLS.
begin;

set local lock_timeout='10s';
set local statement_timeout='120s';

select pg_advisory_xact_lock(hashtext('demobank.hosted_migrations'));

do $apply_0073$
declare previous supabase_migrations.schema_migrations; script text:=$source_0073$-- Released source SHA256: 5fdbc5aa03aefeb5f3b7ce43ebe88638da1e227fa4f41e501a8b11939fe7ab27
-- One client persona: questions the assistant cannot answer go straight to the client's relationship manager.
-- A support handoff (reason general_support: "Talk to a person", a withheld answer, or a question that needs a person)
-- now opens an 'rm_handover' ticket owned by the relationship manager, with the redacted question as its summary.
-- Fraud handoffs keep going to fraud operations. Nothing here lets the bot decide or close anything.

insert into support.request_categories(category,label,description,owner_team,decided_by,target_hours,fields,documents,kb_article,customer_raisable) values
 ('rm_handover','Question for your relationship manager','Something the assistant could not answer, passed to your relationship manager.',
  'Your relationship manager','Your relationship manager answers you directly.',24,'[]','{}','support',false);

create or replace function support.ticket_for_handoff() returns trigger language plpgsql security definer set search_path='' as $$
begin
  if tg_op='INSERT' and new.customer_id is not null then
    perform support.open_ticket(new.customer_id,new.case_id,
      case when new.reason in ('card_verification','card_dispute') then 'suspected_fraud' else 'rm_handover' end,
      case new.reason when 'card_verification' then 'Card blocked after a suspected unauthorised charge: identity check and card decision.'
        when 'card_dispute' then 'Disputed card charges for bank staff review.'
        else left(new.summary,500) end,
      '{}',case new.priority when 'P0' then 'urgent' when 'P1' then 'high' else 'normal' end,'handoff',p_handoff_id=>new.handoff_id);
  elsif tg_op='UPDATE' and new.status is distinct from old.status then
    perform support.move_ticket(t.ticket_id,case new.status when 'claimed' then 'in_review' when 'closed' then 'resolved' else 'submitted' end)
      from support.tickets t where t.handoff_id=new.handoff_id;
  end if;
  return null;
end $$;

-- Same read as 0071 plus the linked handoff id, so the assistant can quote the reference of the ticket it just opened.
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
        where e.ticket_id=t.ticket_id and not e.internal order by e.created_at desc,e.event_id desc limit 1)) item
    from support.tickets t join support.request_categories c using(category)
    where t.customer_id=cid order by t.created_at desc limit 200) x;
  return banking.customer_result(cid,'tickets.list','support.tickets',data);
end $$;

-- Michelle's demo review is due on 30 Nov 2026 (the persona's date), so the demo is not already overdue.
update compliance.cases set due_date='2026-11-30' where customer_id=1002 and due_date<'2026-11-30' and status not in ('completed','closed');
update compliance.info_requests set due_date='2026-11-30' where customer_id=1002 and status='open' and due_date<'2026-11-30';
$source_0073$;
begin
  select * into previous from supabase_migrations.schema_migrations where version='0073';
  if found then
    if previous.name is distinct from 'rm_handover' or previous.statements is distinct from array[script] then
      raise exception 'migration 0073 is recorded with different SQL; inspect history before proceeding';
    end if;
    raise notice 'migration 0073 already applied with identical SQL';
  else
    execute script;
    insert into supabase_migrations.schema_migrations(version,name,statements)
      values('0073','rm_handover',array[script]);
  end if;
end $apply_0073$;

commit;
select version,name from supabase_migrations.schema_migrations order by version;
