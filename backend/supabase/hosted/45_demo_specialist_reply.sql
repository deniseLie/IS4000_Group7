-- Storyline scene 4 (demo only, synthetic data): the bank teams reply to Sophie's hand-offs.
-- The specialist and compliance teams work tickets in the bank's own case tools, which are outside the prototype
-- (architecture box 19, "Bank teams"). This script stands in for them: it records one public reply on Michelle's
-- latest open financing-specialist and compliance-question tickets. Michelle sees the note in My requests and her
-- Inbox; Sophie sees it in Ticket inbox; the next brief lists the request with its new status.
-- Run in the SQL Editor after Sophie has sent the hand-offs in scene 3. Safe to run again: closed tickets are skipped.
do $$
declare t record;replies int:=0;
begin
  select reference into t from support.tickets
   where customer_id=1002 and category='financing_specialist' and status in ('submitted','in_review')
   order by created_at desc limit 1;
  if found then
    perform support.set_ticket_status(t.reference,'waiting_on_client',
      'Mortgage and Lombard specialists: thank you, we have your goal (condo purchase next year) and timeline from your relationship manager. '
      ||'To compare property financing with a Lombard loan we will need: proof of income for the last 2 years, a recent portfolio statement, '
      ||'and the option to purchase once you have one. Please upload these in the app; there is no need to repeat your goals.');
    replies:=replies+1;
  end if;
  select reference into t from support.tickets
   where customer_id=1002 and category='compliance_documents' and status in ('submitted','in_review')
   order by created_at desc limit 1;
  if found then
    perform support.set_ticket_status(t.reference,'resolved',
      'Compliance review team: the signed Bangkok sale agreement and the bank transfer record are enough to show the source of the funds. '
      ||'No further documents are needed for this question.');
    replies:=replies+1;
  end if;
  raise notice 'Recorded % bank team repl%. Send the hand-offs in scene 3 first if this says 0.',replies,case when replies=1 then 'y' else 'ies' end;
end $$;
