-- Existing DemoBank project: apply only after reviewing hosted schema and RLS.
begin;

set local lock_timeout='10s';
set local statement_timeout='120s';

select pg_advisory_xact_lock(hashtext('demobank.hosted_migrations'));

do $apply_0096$
declare previous supabase_migrations.schema_migrations; script text:=$source_0096$-- Released source SHA256: 788e6231b7bf19b2385ea1a3f4f3827a0f6e2ea130b360b36cec1d696375a963
-- Card controls for the client (CR19), benchmarked against UBS's in-app card services. Synthetic data only.
--
-- Revisits part of 0081 (CR12): the client gets the low-risk, everyday controls back, while everything that needs a
-- decision stays with a bank team.
--   1. Freeze and unfreeze. The client can freeze an active card herself (card.temporary_block) and unfreeze it
--      (card.unblock) only when the freeze was her own and no fraud case or lost/stolen report is open. A card the
--      bank blocked, or one she reported lost or stolen, stays blocked until the bank acts. Permanent cancellation
--      and the old fraud dispute draft (card.dispute, 0066) stay bank-only.
--   2. Replace a lost, stolen or damaged card (new request category card_replacement). A lost or stolen card is
--      frozen at once when the request is recorded; card operations cancel it and send a new card with a new number.
--   3. Card limit change rules: steps of SGD 1,000 between 1,000 and 50,000; a different amount from today's limit;
--      not below what she owes; a temporary change is an increase of at most 90 days; a higher limit needs her
--      declaration; one decided change per card every 30 days.
--   4. Dispute a card charge she recognises (new category card_dispute): a posted charge on that card from the last
--      60 days, a reason from a fixed list, whether she contacted the merchant, and a declaration. One open dispute
--      per charge. A charge she does not recognise is still reported to the fraud team (0081).
-- Rule refusals use SQLSTATE P1002 with a message written for the client; the API shows it as is.
-- The bank decides every request: nothing here approves a limit, refunds a charge or cancels a card.

-- 1. Freeze / unfreeze ---------------------------------------------------------------------------------------------
create function actions.card_freeze_target(p_target jsonb,p_customer_id bigint)
returns jsonb language plpgsql volatile security definer set search_path='' as $$
declare target jsonb:=actions.normalise_card_target(p_target); card banking.credit_cards;
begin
  if p_customer_id is null or p_customer_id is distinct from banking.my_customer_id() then
    raise exception 'a card freeze needs the signed-in customer''s own scope' using errcode='42501';
  end if;
  select * into card from banking.credit_cards where customer_id=p_customer_id and card_id=substring(target->>'card_id' from 6)::bigint;
  if not found then raise exception 'card is not available in this customer scope' using errcode='42501'; end if;
  if card.status='temporarily_blocked' then raise exception 'This card is already frozen.' using errcode='P1002'; end if;
  if card.status<>'active' then raise exception 'Only an active card can be frozen.' using errcode='P1002'; end if;
  return jsonb_build_object('card_id',target->>'card_id','last4',card.last4);
end $$;

-- Why the client may not unfreeze this card right now, or null when she may.
create function banking.card_unfreeze_blocker(p_customer_id bigint,p_card banking.credit_cards)
returns text language sql stable security definer set search_path='' as $$
  select case
    when p_card.status<>'temporarily_blocked' then 'Only a frozen card can be unfrozen.'
    when exists(select 1 from support.tickets t where t.customer_id=p_customer_id and t.category='suspected_fraud'
        and t.status in ('submitted','in_review','waiting_on_client'))
      then 'The fraud team is reviewing a case on your cards, so only the bank can unfreeze this card. Follow the request in My requests.'
    when exists(select 1 from support.tickets t where t.customer_id=p_customer_id and t.category='card_replacement'
        and t.details->>'card'=p_card.last4 and t.status in ('submitted','in_review','waiting_on_client'))
      then 'You reported this card lost, stolen or damaged, so it stays frozen until card operations replace it.'
    when coalesce((select c.type='card.temporary_block' from actions.commands c
        where c.customer_id=p_customer_id and c.state='succeeded' and c.type in ('card.temporary_block','card.unblock','card.cancel_permanently')
          and c.target->>'card_id'='card_'||p_card.card_id
          and not exists(select 1 from actions.staff_commands s where s.command_id=c.command_id)
        order by c.confirmed_at desc nulls last,c.proposed_at desc limit 1),false) is not true
      then 'The bank froze this card, so only the bank can unfreeze it. Use Talk to a person or call the number on the back of your card.'
  end $$;

-- Same as 0068, with the client's own unfreeze back; permanent cancellation stays with the bank (0081).
create or replace function actions.customer_card_change(p_target jsonb,p_customer_id bigint,p_operation text)
returns jsonb language plpgsql volatile security definer set search_path='' as $$
declare card banking.credit_cards; blocker text;
begin
  if p_operation<>'unblock' then
    raise exception 'only the bank can cancel a card; to replace a lost, stolen or damaged card, raise a replacement request' using errcode='42501';
  end if;
  if p_customer_id is null or p_customer_id is distinct from banking.my_customer_id() then
    raise exception 'a card change needs the signed-in customer''s own scope' using errcode='42501';
  end if;
  select * into card from banking.credit_cards where customer_id=p_customer_id and card_id=substring(p_target->>'card_id' from 6)::bigint;
  if not found then raise exception 'card is not available in this customer scope' using errcode='42501'; end if;
  blocker:=banking.card_unfreeze_blocker(p_customer_id,card);
  if blocker is not null then raise exception '%',blocker using errcode='P1002'; end if;
  return jsonb_build_object('card_id',p_target->>'card_id','last4',card.last4,'from_status',card.status);
end $$;

-- Same as 0068; the client's unfreeze is checked again when she confirms (a fraud case may have opened meanwhile).
create or replace function actions.execute_card_unblock(p_command actions.commands)
returns jsonb language plpgsql volatile security definer set search_path='' as $$
declare card banking.credit_cards;
begin
  if p_command.target ? 'handoff_id' then return actions.execute_staff_unblock(p_command); end if;
  select * into card from banking.credit_cards where customer_id=p_command.customer_id and card_id=substring(p_command.target->>'card_id' from 6)::bigint;
  if not found or banking.card_unfreeze_blocker(p_command.customer_id,card) is not null then
    raise exception 'the card can no longer be unfrozen by the client; review it again' using errcode='P1001';
  end if;
  return actions.execute_customer_card_change(p_command,'active','card_unblocked_by_customer','card.unblocked_by_customer');
end $$;

update actions.executors set validator='actions.card_freeze_target(jsonb,bigint)'::regprocedure where type='card.temporary_block';

-- The client's card read says whether she may unfreeze each card herself, and why not.
create or replace function public.customer_read_cards(p_customer_id bigint)
returns jsonb language plpgsql volatile security invoker set search_path='' as $$
declare cid bigint:=banking.require_read_customer(p_customer_id); data jsonb;
begin
  select coalesce(jsonb_agg(to_jsonb(r) order by r.card_id),'[]') into data from(
    select 'card_'||c.card_id card_id,c.card_name name,c.network,c.last4,c.status,c.currency,c.credit_limit "limit",c.outstanding_balance outstanding,
      c.minimum_payment_due minimum_payment,c.payment_due_date due_date,
      case when c.status='temporarily_blocked' then banking.card_unfreeze_blocker(cid,c) end unfreeze_blocked_reason
      from banking.credit_cards c where c.customer_id=cid order by c.card_id limit 200)r;
  return banking.customer_result(cid,'customer.get_credit_cards','banking.credit_cards',data);
end $$;

insert into notify.templates(template_key,version,locale,channel,title,body,is_service) values
 ('card.blocked',3,'en','in_app','Card frozen','Your synthetic card ending {last4} is frozen, so new card payments are declined. Payments already made are not reversed. Unfreeze it on Overview under Cards when you are ready. See Actions for the receipt.',true),
 ('card.unblocked_by_customer',2,'en','in_app','Card unfrozen','Your synthetic card ending {last4} is unfrozen; new card payments can go through again. See Actions for the receipt. No real bank card is connected.',true);

-- 2-4. Request categories -------------------------------------------------------------------------------------------
update support.request_categories set version=2,
  description='Ask for a higher or lower credit card limit, for a period or permanently. Limits change in steps of SGD 1,000, up to SGD 50,000, once every 30 days per card.',
  fields='[{"key":"card","label":"Card (last 4 digits)","kind":"card","required":true},
    {"key":"new_limit","label":"New limit (SGD, in steps of 1,000)","kind":"amount","required":true},
    {"key":"duration","label":"Temporary or permanent","kind":"choice","required":true,"choices":["temporary","permanent"]},
    {"key":"until","label":"Needed until (temporary only, up to 90 days)","kind":"date","required":false},
    {"key":"reason","label":"Reason","kind":"choice","required":true,"choices":["Travel","Large purchase","Change in income","Emergency expense","Lower my spending","Other"]},
    {"key":"reason_details","label":"Anything the team should know","kind":"text","required":false},
    {"key":"declaration","label":"For a higher limit: my income and employment details with the bank are up to date and I can repay it","kind":"confirm","required":false}]'
where category='card_limit_change';

insert into support.request_categories(category,version,label,description,owner_team,decided_by,target_hours,fields,documents,kb_article,customer_raisable) values
 ('card_replacement',1,'Replace a lost, stolen or damaged card',
  'Report a card lost, stolen, damaged or not working and get a new card. A lost or stolen card is frozen as soon as you submit. Charges you do not recognise go to the fraud team instead.',
  'Card operations team','Card operations cancel the old card and send a new one, with a new number, to your registered address.',48,
  '[{"key":"card","label":"Card (last 4 digits)","kind":"card","required":true},
    {"key":"what_happened","label":"What happened","kind":"choice","required":true,"choices":["Lost","Stolen","Damaged","Not working"]},
    {"key":"last_had","label":"When you last had it (lost or stolen)","kind":"date_past","required":false},
    {"key":"declaration","label":"I understand the bank will cancel this card and send a replacement with a new number to my registered address","kind":"confirm","required":true}]',
  '{}','card-controls',true),
 ('card_dispute',1,'Dispute a card charge',
  'Dispute a charge you recognise but think is wrong: charged twice, the wrong amount, not delivered, cancelled or a refund that never came. Charges can be disputed up to 60 days after they are posted.',
  'Card disputes team','The card disputes team reviews it with the merchant and decides any refund; the assistant cannot.',120,
  '[{"key":"card","label":"Card (last 4 digits)","kind":"card","required":true},
    {"key":"transaction","label":"Charge","kind":"transaction","required":true},
    {"key":"reason","label":"Reason","kind":"choice","required":true,"choices":["Charged twice","Wrong amount","Goods or service not received","Cancelled but still charged","Refund not received"]},
    {"key":"contacted_merchant","label":"Have you contacted the merchant?","kind":"choice","required":true,"choices":["Yes","No"]},
    {"key":"what_happened","label":"What happened","kind":"text","required":true},
    {"key":"declaration","label":"I recognise this charge and the information I give is true","kind":"confirm","required":true}]',
  '{}','card-controls',true);

-- Several replacement or dispute requests can be open at once (one per card / per charge), so the one-open-per-category
-- rule (0071) leaves them out; execute_ticket below reuses the open request for the same card or charge instead.
drop index support.tickets_one_open_form_idx;
create unique index tickets_one_open_form_idx on support.tickets(customer_id,category)
  where source in ('form','chat') and status in ('submitted','in_review','waiting_on_client') and category not in ('card_replacement','card_dispute');
create unique index tickets_one_open_card_idx on support.tickets(customer_id,category,(details->>'card'))
  where source in ('form','chat') and status in ('submitted','in_review','waiting_on_client') and category='card_replacement';
create unique index tickets_one_open_dispute_idx on support.tickets(customer_id,(details->>'transaction'))
  where status in ('submitted','in_review','waiting_on_client') and category='card_dispute';

-- The rules for the three card categories, checked after the field formats (0071). p_customer_id is the client's.
create function actions.card_request_rules(p_target jsonb,p_customer_id bigint)
returns void language plpgsql stable security definer set search_path='' as $$
declare d jsonb:=p_target->'details'; card banking.credit_cards; amount numeric; until date; txn banking.transactions;
  today date:=(clock_timestamp() at time zone 'Asia/Singapore')::date; last_decided timestamptz; existing support.tickets;
begin
  select * into card from banking.credit_cards c where c.customer_id=p_customer_id and c.last4=d->>'card' and c.status in ('active','temporarily_blocked')
    order by c.card_id limit 1;
  if p_target->>'category'='card_limit_change' then
    amount:=(d->>'new_limit')::numeric;
    if amount%1000<>0 or amount<1000 or amount>50000 then
      raise exception 'Choose a new limit in steps of SGD 1,000, from SGD 1,000 to SGD 50,000.' using errcode='P1002';
    end if;
    if amount=card.credit_limit then
      raise exception 'Your card ending % already has a limit of SGD %.',card.last4,to_char(card.credit_limit,'FM999,999,990') using errcode='P1002';
    end if;
    if amount<card.outstanding_balance then
      raise exception 'A new limit cannot be lower than what you owe on this card (SGD %).',to_char(card.outstanding_balance,'FM999,999,990.00') using errcode='P1002';
    end if;
    if d->>'duration'='temporary' then
      if amount<card.credit_limit then raise exception 'A temporary change can only raise the limit. To lower it, choose permanent.' using errcode='P1002'; end if;
      if d->>'until' is null then raise exception 'Choose the date the temporary limit should end.' using errcode='P1002'; end if;
      until:=(d->>'until')::date;
      if until<=today or until>today+90 then raise exception 'A temporary limit can last from tomorrow up to 90 days.' using errcode='P1002'; end if;
    elsif d ? 'until' then
      raise exception 'A permanent change has no end date; remove it or choose temporary.' using errcode='P1002';
    end if;
    if amount>card.credit_limit and d->>'declaration' is distinct from 'yes' then
      raise exception 'To ask for a higher limit, confirm that your income and employment details are up to date.' using errcode='P1002';
    end if;
    select max(t.updated_at) into last_decided from support.tickets t where t.customer_id=p_customer_id and t.category='card_limit_change'
      and t.details->>'card'=card.last4 and t.status in ('resolved','closed') and t.updated_at>clock_timestamp()-interval '30 days';
    if last_decided is not null then
      raise exception 'The limit on your card ending % was last reviewed on %. You can ask again from %.',card.last4,
        to_char(last_decided at time zone 'Asia/Singapore','FMDD Mon YYYY'),to_char((last_decided+interval '30 days') at time zone 'Asia/Singapore','FMDD Mon YYYY')
        using errcode='P1002';
    end if;
  elsif p_target->>'category'='card_replacement' then
    if d->>'last_had' is not null and d->>'what_happened' not in ('Lost','Stolen') then
      raise exception 'Only give the date you last had the card for a lost or stolen card.' using errcode='P1002';
    end if;
  elsif p_target->>'category'='card_dispute' then
    select * into txn from banking.transactions t where t.customer_id=p_customer_id and t.card_id=card.card_id
      and 'txn_'||t.transaction_id=d->>'transaction';
    if not found then raise exception 'Choose a charge made on your card ending %.',card.last4 using errcode='P1002'; end if;
    if txn.status<>'posted' or txn.amount>=0 then
      raise exception 'Only a posted charge can be disputed. A pending charge can be disputed once it is posted.' using errcode='P1002';
    end if;
    if txn.txn_date<clock_timestamp()-interval '60 days' then
      raise exception 'This charge was posted more than 60 days ago, so it can no longer be disputed here. Use Talk to a person.' using errcode='P1002';
    end if;
    select * into existing from support.tickets t where t.customer_id=p_customer_id and t.category='card_dispute'
      and t.details->>'transaction'=d->>'transaction' and t.status in ('resolved','closed') order by t.updated_at desc limit 1;
    if found then
      raise exception 'This charge was already reviewed in request %.',existing.reference using errcode='P1002';
    end if;
  end if;
end $$;

-- Same as 0071 with two new field kinds (confirm: the client ticked a declaration; transaction: one of her card
-- charges) and the card rules above.
create or replace function actions.ticket_target(p_target jsonb,p_customer_id bigint)
returns jsonb language plpgsql volatile security definer set search_path='' as $$
declare target jsonb:=actions.normalise_ticket(p_target); cat support.request_categories; field jsonb; value text; key text;
  today date:=(clock_timestamp() at time zone 'Asia/Singapore')::date;
begin
  select * into cat from support.request_categories where category=target->>'category' and customer_raisable;
  if not found then raise exception 'this request category cannot be raised by a customer' using errcode='22023'; end if;
  for key in select jsonb_object_keys(target->'details') loop
    if not exists(select 1 from jsonb_array_elements(cat.fields) f where f->>'key'=key) then
      raise exception 'ticket detail % is not part of this category',key using errcode='22023';
    end if;
  end loop;
  for field in select * from jsonb_array_elements(cat.fields) loop
    value:=target->'details'->>(field->>'key');
    if value is null then
      if (field->>'required')::boolean then raise exception 'ticket detail % is required',field->>'key' using errcode='22023'; end if;
      continue;
    end if;
    if field->>'kind'='amount' and value !~ '^[0-9]{1,9}(\.[0-9]{1,2})?$'
        or field->>'kind'='choice' and not (field->'choices' ? value)
        or field->>'kind' in ('date','date_past') and value !~ '^\d{4}-\d\d-\d\d$'
        or field->>'kind'='card' and value !~ '^[0-9]{4}$'
        or field->>'kind'='confirm' and value<>'yes'
        or field->>'kind'='transaction' and value !~ '^txn_[1-9][0-9]{0,17}$' then
      raise exception 'ticket detail % has the wrong format',field->>'key' using errcode='22023';
    end if;
    if field->>'kind' in ('date','date_past') then
      begin
        if field->>'kind'='date' and value::date<today or field->>'kind'='date_past' and value::date>today then
          raise exception 'ticket date % is outside the allowed range',field->>'key' using errcode='22023';
        end if;
      exception when invalid_datetime_format or datetime_field_overflow then
        raise exception 'ticket date % is not a calendar date',field->>'key' using errcode='22023';
      end;
    end if;
    if field->>'kind'='card' and not exists(select 1 from banking.credit_cards c where c.customer_id=p_customer_id and c.last4=value
        and c.status in ('active','temporarily_blocked')) then
      raise exception 'the card is not one of your open cards' using errcode='42501';
    end if;
  end loop;
  if target->>'category' in ('card_limit_change','card_replacement','card_dispute') then perform actions.card_request_rules(target,p_customer_id); end if;
  return target||jsonb_build_object('category_label',cat.label,'owner_team',cat.owner_team,'decided_by',cat.decided_by,'target_hours',cat.target_hours);
end $$;

-- Same as 0071; an open replacement for the same card, or an open dispute of the same charge, is reused.
create or replace function actions.execute_ticket(p_command actions.commands)
returns jsonb language plpgsql volatile security definer set search_path='' as $$
declare ticket support.tickets; created boolean:=false; wanted text:=p_command.target->>'category';
begin
  if actions.ticket_target(p_command.target,p_command.customer_id)<>p_command.target then
    raise exception 'the request category changed; review a new ticket' using errcode='P1001';
  end if;
  select * into ticket from support.tickets t where t.customer_id=p_command.customer_id and t.category=wanted
    and t.status in ('submitted','in_review','waiting_on_client')
    and case wanted when 'card_replacement' then t.source in ('form','chat') and t.details->>'card'=p_command.target->'details'->>'card'
                      when 'card_dispute' then t.details->>'transaction'=p_command.target->'details'->>'transaction'
                      else t.source in ('form','chat') end
    order by t.created_at limit 1 for update;
  if not found then
    ticket:=support.open_ticket(p_command.customer_id,p_command.case_id,wanted,p_command.target->>'summary',
      p_command.target->'details',p_command.target->>'urgency','form',p_command_id=>p_command.command_id);
    created:=true;
  end if;
  return jsonb_build_object('result',jsonb_build_object('ticket_id',ticket.ticket_id,'reference',ticket.reference,'category',ticket.category,
      'status',ticket.status,'owner_team',ticket.owner_team,'target_at',ticket.target_at,'created',created),
    'event_type',case when created then 'ticket.recorded' else 'ticket.reused' end);
end $$;

-- Whichever way the request is raised (the client's form, the chat or her RM, 0089): a dispute records the charge in
-- words for the team, and a lost or stolen card is frozen at once and the request marked high.
create function support.card_request_effects() returns trigger language plpgsql security definer set search_path='' as $$
declare txn banking.transactions; frozen int:=0;
begin
  if new.category='card_dispute' and new.details ? 'transaction' then
    select * into txn from banking.transactions t where t.customer_id=new.customer_id and 'txn_'||t.transaction_id=new.details->>'transaction';
    if found then
      new.details:=new.details||jsonb_build_object('charge',left(to_char(txn.txn_date at time zone 'Asia/Singapore','FMDD Mon YYYY')||' · '||
        coalesce(txn.merchant,txn.description)||' · '||txn.currency||' '||to_char(abs(txn.amount),'FM999,999,990.00'),300));
    end if;
  elsif new.category='card_replacement' and new.details->>'what_happened' in ('Lost','Stolen') then
    update banking.credit_cards set status='temporarily_blocked' where customer_id=new.customer_id and last4=new.details->>'card' and status='active';
    get diagnostics frozen=row_count;
    if new.urgency in ('low','normal') then new.urgency:='high'; end if;
    new.details:=new.details||jsonb_build_object('card_frozen',case when frozen>0 then 'Frozen when reported' else 'Already frozen' end);
    perform audit.record('action','action.card_frozen_on_report','success','banking.credit_cards',new.details->>'card',
      jsonb_build_object('frozen',frozen>0),new.case_id,p_customer_id=>new.customer_id);
  end if;
  return new;
end $$;
create trigger card_request_effects before insert on support.tickets for each row
  when (new.category in ('card_dispute','card_replacement')) execute function support.card_request_effects();

revoke all on function actions.execute_card_unblock(actions.commands),actions.card_freeze_target(jsonb,bigint),banking.card_unfreeze_blocker(bigint,banking.credit_cards),
  actions.card_request_rules(jsonb,bigint),support.card_request_effects() from public,anon,authenticated;
-- customer_read_cards is security invoker and calls the blocker as the client.
grant execute on function banking.card_unfreeze_blocker(bigint,banking.credit_cards) to authenticated;

-- Knowledge -----------------------------------------------------------------------------------------------------------
update kb.articles set status='retired' where article_id='lost-card' and version=3 and status='published';
update kb.articles set status='retired' where article_id='card-limit-change' and version=1 and status='published';
insert into kb.articles(article_id,version,title,categories,publisher,scope,kind,status,reference_url,checked_on,review_due) values
  ('lost-card',4,'Lost or stolen card guidance',array['fraud'],'DemoBank coursework project','Fictional demo guidance; not a live bank policy.','demo','published',null,null,null),
  ('card-limit-change',2,'Changing your credit card limit',array['retail'],'DemoBank coursework project','Fictional demo guidance; not a live bank policy.','demo','published',null,null,null),
  ('card-controls',1,'Freezing, replacing and disputing a card',array['retail','fraud'],'DemoBank coursework project','Fictional demo guidance; not a live bank policy.','demo','published',null,null,null);
insert into kb.sections(article_id,version,anchor,heading,body,keywords) values
  ('lost-card',4,'main','Lost or stolen card guidance','If your card is lost or stolen, freeze it straight away: on Overview, under Cards, choose Manage and then Freeze card. Then raise a replacement request (Manage, then Report lost, stolen or damaged); a lost or stolen card is frozen as soon as you submit, and card operations cancel it and send a new card with a new number to your registered address. If you also see charges you do not recognise, report them in the chat: they go to the fraud team as urgent. You can unfreeze a card yourself only if you froze it and nothing is open with the fraud team or card operations. Never share your PIN, password, OTP, CVV or full card number.','lost stolen missing card block freeze replacement'),
  ('card-limit-change',2,'main','Changing your credit card limit','You can ask DemoBank for a higher or lower credit card limit, for a period or permanently, from My requests or from the card on Overview (Manage, then Change limit). Limits change in steps of SGD 1,000, from SGD 1,000 to SGD 50,000, and cannot be lower than what you owe. A temporary change raises the limit for up to 90 days. For a higher limit you confirm that your income and employment details with the bank are up to date. You can ask for one change per card every 30 days after the last request was decided. The cards and credit team decides after its own checks, which can include asking for recent proof of income. The assistant cannot approve, reject or predict the outcome, and a request does not change your limit until the bank confirms it. These are synthetic demo terms, not a live bank service standard.','credit limit card limit increase decrease higher lower raise trip temporary permanent limit change steps 30 days'),
  ('card-controls',1,'freeze','Freezing and unfreezing a card','You can freeze an active card yourself: on Overview, under Cards, choose Manage and then Freeze card, and confirm. New card payments are then declined; payments already made are not reversed. You can unfreeze it the same way if you froze it yourself. If the bank froze it, the fraud team is reviewing a case, or you reported the card lost or stolen, only the bank can unfreeze it. Only the bank cancels a card.','freeze unfreeze block unblock lock card temporarily'),
  ('card-controls',1,'replace','Replacing a card','To replace a lost, stolen, damaged or faulty card, choose Manage on the card and then Report lost, stolen or damaged. A lost or stolen card is frozen as soon as you submit. Card operations cancel the old card and send a new card with a new number to your registered address. You get a request reference to follow in My requests.','replace replacement new card damaged broken not working lost stolen'),
  ('card-controls',1,'dispute','Disputing a card charge','If you recognise a charge but think it is wrong (charged twice, the wrong amount, not delivered, cancelled but still charged, or a refund that never came), dispute it from Recent activity on Overview or from My requests within 60 days of the charge being posted. Say what happened and whether you contacted the merchant. The card disputes team reviews it with the merchant and decides any refund; the assistant cannot decide or refund. A charge you do not recognise is not a dispute: report it in the chat and it goes to the fraud team as urgent.','dispute charge charged twice wrong amount refund not received merchant chargeback');
$source_0096$;
begin
  select * into previous from supabase_migrations.schema_migrations where version='0096';
  if found then
    if previous.name is distinct from 'card_controls' or previous.statements is distinct from array[script] then
      raise exception 'migration 0096 is recorded with different SQL; inspect history before proceeding';
    end if;
    raise notice 'migration 0096 already applied with identical SQL';
  else
    execute script;
    insert into supabase_migrations.schema_migrations(version,name,statements)
      values('0096','card_controls',array[script]);
  end if;
end $apply_0096$;

commit;
select version,name from supabase_migrations.schema_migrations order by version;
