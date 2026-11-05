-- Evaluation clean-up (build plan 5b). Run before each QA1 client run, together with 34_reset_meeting_demo.sql.
--
-- Some client cases find an open request of the same kind and reuse it instead of opening a new one. This is by design:
-- an RM handover on the same topic is added to her open ticket (0082), and an open urgent lost-card request is quoted
-- (build plan 2b). Requests left open by earlier runs therefore change what C10 (investment advice), C11 (lost card) and
-- C20 (fee waiver) see. This closes Michelle's (1002) open rm_handover tickets on those three topics, each with a note
-- she can read in My requests: fees or rates, investment or product advice, and an urgent lost card. On 5 Nov 2026 these
-- were TCK-001052, TCK-001066 and TCK-001081; later runs open new ones, so the tickets are chosen by topic, not by number.
-- C11b opens its own urgent request first (setup_turns), so it needs nothing left open. Other tickets, the meeting demo
-- and the audit log are untouched; each change is audited (handoff.ticket_status_changed). Safe to run more than once.
begin;

do $$
declare r record;
begin
  for r in select reference from support.tickets
    where customer_id=1002 and category='rm_handover' and status in ('submitted','in_review','waiting_on_client')
      and (summary like '[Fees or rates]%' or summary like '[Investment or product advice]%'
        or summary like '[Urgent card] Card reported lost or stolen%')
  loop
    perform support.set_ticket_status(r.reference,'closed','Closed by the demo evaluation clean-up (test data).');
  end loop;
end $$;

-- Expect no rows: nothing on those topics is still open.
select reference,status,left(summary,40) as summary_start from support.tickets
where customer_id=1002 and category='rm_handover' and status in ('submitted','in_review','waiting_on_client')
  and (summary like '[Fees or rates]%' or summary like '[Investment or product advice]%'
    or summary like '[Urgent card] Card reported lost or stolen%');
commit;
