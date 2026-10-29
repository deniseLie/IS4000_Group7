-- Existing DemoBank project: apply only after reviewing hosted schema and RLS.
begin;

set local lock_timeout='10s';
set local statement_timeout='120s';

select pg_advisory_xact_lock(hashtext('demobank.hosted_migrations'));

do $apply_0081$
declare previous supabase_migrations.schema_migrations; script text:=$source_0081$-- Released source SHA256: 055cdaa2b4337069168d6ae8539067c139666a4f51a9b9fa6f281df7e43ffaa6
-- Card actions are the bank's, not the client's (CR12, decided 30 Oct 2026). The client can no longer block, unblock or
-- cancel a card, or send a dispute draft, from the app or the assistant. Instead, when she reports charges she does not
-- recognise, the assistant gives the urgent steps and passes the case to the fraud team as an urgent ticket (a P0
-- card_verification handoff); the fraud team contacts her, blocks the card if needed and reviews the charges.
-- Staff card changes (scripts/staff_support.py, migration 0030) are unchanged. Earlier receipts stay readable.

create function actions.bank_only_target(p_target jsonb,p_customer_id bigint)
returns jsonb language plpgsql immutable set search_path='' as $$
begin
  raise exception 'only the bank can block a card or record a dispute; report the charges to the fraud team' using errcode='42501';
end $$;
revoke all on function actions.bank_only_target(jsonb,bigint) from public,anon,authenticated;
update actions.executors set validator='actions.bank_only_target(jsonb,bigint)'::regprocedure where type in ('card.temporary_block','card.dispute');

-- The customer's own unblock and cancel (0068) are refused; the staff path (a target with a handoff_id) is untouched.
create or replace function actions.customer_card_change(p_target jsonb,p_customer_id bigint,p_operation text)
returns jsonb language plpgsql volatile security definer set search_path='' as $$
begin
  raise exception 'only the bank can unblock or cancel a card; contact the fraud team or your relationship manager' using errcode='42501';
end $$;

-- The fraud ticket now says what the fraud team has to do, since the card is no longer blocked by the client first.
create or replace function support.ticket_for_handoff() returns trigger language plpgsql security definer set search_path='' as $$
begin
  if tg_op='INSERT' and new.customer_id is not null then
    perform support.open_ticket(new.customer_id,new.case_id,
      case when new.reason in ('card_verification','card_dispute') then 'suspected_fraud' else 'rm_handover' end,
      case new.reason when 'card_verification' then 'Card charges reported as not recognised: the fraud team contacts you, blocks the card if needed and reviews the charges.'
        when 'card_dispute' then 'Disputed card charges for bank staff review.'
        else left(new.summary,500) end,
      '{}',case new.priority when 'P0' then 'urgent' when 'P1' then 'high' else 'normal' end,'handoff',p_handoff_id=>new.handoff_id);
  elsif tg_op='UPDATE' and new.status is distinct from old.status then
    perform support.move_ticket(t.ticket_id,case new.status when 'claimed' then 'in_review' when 'closed' then 'resolved' else 'submitted' end)
      from support.tickets t where t.handoff_id=new.handoff_id;
  end if;
  return null;
end $$;

-- Knowledge: the assistant no longer offers a block or a dispute draft.
update kb.articles set status='retired' where article_id='lost-card' and version=2 and status='published';
update kb.articles set status='retired' where article_id='unrecognised-charge' and version=2 and status='published';
update kb.articles set status='retired' where article_id='support' and version=3 and status='published';
insert into kb.articles(article_id,version,title,categories,publisher,scope,kind,status,reference_url,checked_on,review_due) values
  ('lost-card',3,'Lost or stolen card guidance',array['fraud'],'DemoBank coursework project','Fictional demo guidance; not a live bank policy.','demo','published',null,null,null),
  ('unrecognised-charge',3,'Unrecognised card charge',array['fraud'],'DemoBank coursework project','Fictional demo guidance; not a live bank policy.','demo','published',null,null,null),
  ('support',4,'Getting human support',array['retail','fraud','compliance','document','relationship'],'DemoBank coursework project','Fictional demo guidance; not a live bank policy.','demo','published',null,null,null);
insert into kb.sections(article_id,version,anchor,heading,body,keywords) values
  ('lost-card',3,'main','Lost or stolen card guidance','Report a lost or stolen card to your bank immediately through the official fraud line printed on the back of your card or the report function in its official app. If the official app offers a freeze control, use it. The assistant cannot block, unblock or cancel a card; only the bank can. When you are signed in, reporting it here also passes it to the fraud team as urgent and gives you a reference to follow in My requests. Review recent activity and report unfamiliar transactions. Do not wait for a reply here if your card is at risk. Never share your PIN, password, OTP, CVV or full card number.','lost stolen missing card block freeze'),
  ('unrecognised-charge',3,'urgent-steps','Urgent steps for an unrecognised charge','If you do not recognise a card charge, act now: 1. Call the official fraud line printed on the back of your card, or use the report function in the official app. 2. Freeze the card in the official app if it offers that control. 3. Check your other recent transactions. 4. Never share your PIN, password, one-time code or full card number. When you are signed in, the assistant passes the charges to the fraud team as urgent and gives you a reference to follow in My requests. The fraud team contacts you, blocks the card if needed and reviews the charges. The assistant cannot block a card, investigate, decide or reverse a charge.','unrecognised unrecognized unfamiliar charge fraud suspicious transaction dont recognise not mine block dispute'),
  ('unrecognised-charge',3,'patterns','Small charges before a larger one','Several small charges from the same merchant shortly before a larger one can be a sign that someone is testing a stolen card number. A pattern like this is a reason to check the charges with the bank, not proof of fraud. Include every charge you do not recognise when you report them.','small charges smaller card testing pattern before weird earlier'),
  ('unrecognised-charge',3,'replacement-card','Replacement card after a block','Only the bank blocks or cancels a card. A block stops new card payments but does not cancel the card or reverse any charge. Identity verification and permanent cancellation are done by bank staff. After staff confirm the card should be cancelled, the bank issues a replacement card with a new number to your registered address.','replacement card new card block cancel cancellation verification'),
  ('support',4,'main','Getting human support','When you are signed in, Talk to a person shows a short summary; approving it passes your question to your relationship manager with a reference to follow in My requests. Charges you do not recognise go to the fraud team as urgent instead. As a guest you can review, edit and copy a summary for your own use; copying does not send anything. For a real issue, contact your bank using its official app or website. The assistant cannot make payments, block or cancel cards, close accounts, dispute, refund or reverse charges, or change banking records.','human person support staff contact close dispute transfer refund handoff');
$source_0081$;
begin
  select * into previous from supabase_migrations.schema_migrations where version='0081';
  if found then
    if previous.name is distinct from 'bank_only_card_actions' or previous.statements is distinct from array[script] then
      raise exception 'migration 0081 is recorded with different SQL; inspect history before proceeding';
    end if;
    raise notice 'migration 0081 already applied with identical SQL';
  else
    execute script;
    insert into supabase_migrations.schema_migrations(version,name,statements)
      values('0081','bank_only_card_actions',array[script]);
  end if;
end $apply_0081$;

commit;
select version,name from supabase_migrations.schema_migrations order by version;
