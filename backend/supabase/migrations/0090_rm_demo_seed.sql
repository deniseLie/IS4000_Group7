-- Persona 2 demo data for the RM workspace (RM1-RM12, RM16). Synthetic data only; no real people or accounts.
-- * Sophie's own meeting notes for Michelle, Grace and Hafiz (one deliberately unclear), client-stated preferences,
--   upcoming maturities and reviews, two open RM tasks (one overdue) and confirmed appointments with a purpose.
-- * Tan Mei Ling (1007), inherited from Rachel on 26 Oct 2026 so the handover brief (RM16) also works on the hosted
--   project (David is local only): Rachel's notes (one private, so it is withheld from Sophie), a chat answer that was
--   handed to the RM, an open request raised with Rachel, a handover note under active consent, and two older notes
--   whose consent expired or was revoked (both must be excluded).
-- Appointment and follow-up dates are relative to the day this migration is applied (the next weekday at a set time),
-- so the demo always has upcoming meetings. David's rows are added only where he exists (local test database).

do $seed$
declare
  today date:=(clock_timestamp() at time zone 'Asia/Singapore')::date;
  sophie uuid:='00000000-0000-4000-8000-100200000002';
  rachel uuid:='00000000-0000-4000-8000-100300000001';
  priya uuid:='00000000-0000-4000-8000-100100000002';
  has_david boolean:=exists(select 1 from banking.customer_profiles where customer_id=1003);
  t support.tickets; sess uuid; kase uuid; q uuid; a uuid; h uuid;
begin
  if exists(select 1 from rm.client_milestones) then return; end if;  -- safe to re-run

  -- Mei Ling: profile, banking, and the recorded change from Rachel to Sophie.
  insert into banking.customer_profiles(customer_id,demo_email,full_name,income_range,employment_status,preferred_language) values
    (1007,'demo.meiling@example.com','Tan Mei Ling (DEMO)','SGD 20,000 and above / month','employed','en') on conflict (customer_id) do nothing;
  insert into banking.bank_accounts(account_id,customer_id,account_type,masked_account_number,balance,currency,opened_on) values
    (10071,1007,'savings','****8820',212400.00,'SGD','2016-05-09') on conflict (account_id) do nothing;
  insert into banking.credit_cards(card_id,customer_id,card_name,network,last4,credit_limit,outstanding_balance,minimum_payment_due,payment_due_date,status) values
    (10071,1007,'Signature Card','mastercard','3318',25000.00,980.50,50.00,today+20,'active') on conflict (card_id) do nothing;
  insert into rm.assignments(assignment_id,customer_id,advisor_id,started_on,ended_on,transition_reason_code) values
    ('00000000-0000-4000-8000-100700000011',1007,rachel,'2023-04-03','2026-10-25',null),
    ('00000000-0000-4000-8000-100700000012',1007,sophie,'2026-10-26',null,'team_restructure') on conflict (assignment_id) do nothing;

  -- Meeting notes. Sophie's own (book-visible), Rachel's for Mei Ling, and private notes that only their author may read.
  insert into rm.meeting_notes(note_id,customer_id,advisor_id,met_on,format,title,summary,commitments,decisions,client_requests,visibility) values
    ('00000000-0000-4000-8000-100200000061',1002,sophie,'2026-04-15','in_person','Annual review',
     'Annual review. Michelle''s consulting income is steady and she keeps a large SGD balance in her multi-currency account for business cash flow. She asked about placing part of it in a 6-month fixed deposit when her current fixed deposit matures in November, and wants the options compared first. She prefers video meetings in the afternoon.',
     array['Send Michelle fixed deposit options before her November maturity'],array['Keep the business float in the multi-currency account'],
     array['Compare 6-month fixed deposit options'],'book'),
    ('00000000-0000-4000-8000-100200000062',1002,sophie,'2026-09-24','video','Periodic review preparation',
     'Michelle explained that the SGD 120,000 incoming transfer on 22 Sep 2026 came from the sale of a property abroad. She will upload the sale agreement for her periodic review. She mentioned she may ask for a review of her Signature Card limit later in the year; no request was made.',
     array['Remind Michelle to upload the sale agreement before her review is due on 30 Nov 2026'],'{}','{}','book'),
    ('00000000-0000-4000-8000-100200000063',1002,sophie,'2026-10-20','phone','Short call',
     'Short call. Michelle mentioned "the thing for her mother" but it was not clear whether she means a new account or a transfer. Need to follow up.',
     array['Clarify what Michelle needs for her mother'],'{}','{}','book'),
    ('00000000-0000-4000-8000-100400000061',1004,sophie,'2026-08-12','video','Quarterly check-in',
     'Quarterly check-in. Grace is renovating her flat and pays her contractor by FAST transfer in stages; the next payment is expected in November. She wants to keep at least SGD 30,000 in savings as an emergency fund. She asked for general information about renovation loans.',
     array['Share general information about renovation loans'],array['Keep at least SGD 30,000 in savings'],array['General information on renovation loans'],'book'),
    ('00000000-0000-4000-8000-100500000061',1005,sophie,'2026-06-03','in_person','Business banking review',
     'Hafiz runs a design studio and uses his current account for business payments. He felt the monthly account fees were high and asked whether they could be reviewed. He is planning a family trip to Japan in December. He prefers to meet in person at the Private Clients Centre.',
     array['Ask the product team whether Hafiz''s current account fees can be reviewed'],'{}',array['Review of current account fees'],'book'),
    ('00000000-0000-4000-8000-100700000061',1007,rachel,'2025-07-08','in_person','Onboarding review',
     'Onboarding review. Mei Ling is a doctor with irregular hours. She prefers an email summary after each meeting and cannot meet on Saturdays. She has a 12-month fixed deposit maturing in December 2026 and wants to discuss renewal options before then.',
     array['Discuss fixed deposit renewal options before the December 2026 maturity'],'{}','{}','book'),
    ('00000000-0000-4000-8000-100700000062',1007,rachel,'2026-06-19','phone','Joint account request',
     'Mei Ling asked to add her husband as a joint holder on her savings account. Rachel explained the forms and the in-branch identity check. Mei Ling has not confirmed a date for the branch visit.',
     array['Send the joint account forms','Confirm a branch appointment for the identity check'],'{}',array['Add her husband as a joint account holder'],'book'),
    ('00000000-0000-4000-8000-100700000063',1007,rachel,'2026-09-30','phone','Private reminder',
     'Private working note by the previous relationship manager.','{}','{}','{}','author_only')
  on conflict (note_id) do nothing;
  if has_david then
    insert into rm.meeting_notes(note_id,customer_id,advisor_id,met_on,format,title,summary,visibility) values
      ('00000000-0000-4000-8000-100300000054',1003,rachel,'2026-10-10','phone','Private reminder','Private working note by the previous relationship manager.','author_only')
    on conflict (note_id) do nothing;
    update rm.meeting_notes set title=case met_on when '2025-03-12' then 'Annual review' when '2025-11-20' then 'Overseas transfer set-up' else 'Mid-year check-in' end
      where customer_id=1003 and title is null and advisor_id=rachel;
  end if;

  insert into rm.client_preferences(customer_id,preference,recorded_on,recorded_by,source_note_id) values
    (1002,'Prefers video meetings in the afternoon','2026-04-15',sophie,'00000000-0000-4000-8000-100200000061'),
    (1005,'Prefers to meet in person at the Private Clients Centre','2026-06-03',sophie,'00000000-0000-4000-8000-100500000061'),
    (1007,'Prefers an email summary after each meeting','2025-07-08',rachel,'00000000-0000-4000-8000-100700000061'),
    (1007,'Cannot meet on Saturdays','2025-07-08',rachel,'00000000-0000-4000-8000-100700000061');
  if has_david then
    insert into rm.client_preferences(customer_id,preference,recorded_on,recorded_by,source_note_id) values
      (1003,'Prefers phone calls before 10am','2025-03-12',rachel,'00000000-0000-4000-8000-100300000051');
  end if;

  insert into rm.client_milestones(customer_id,kind,title,due_on,amount,currency,details) values
    (1002,'fixed_deposit_maturity','Fixed deposit ****4410 matures',today+10,150000.00,'SGD','6-month SGD fixed deposit'),
    (1004,'portfolio_review','Quarterly portfolio check-in due',today+6,null,null,'Agreed quarterly check-in'),
    (1005,'account_review','Annual business account review',today+20,null,null,'Yearly review of the business current account'),
    (1007,'fixed_deposit_maturity','Fixed deposit ****9051 matures',greatest(date '2026-12-15',today+14),80000.00,'SGD','12-month SGD fixed deposit'),
    (1006,'insurance_renewal','Home insurance renewal',today+5,null,null,'Priya''s client: never shown to Sophie');
  if has_david then
    insert into rm.client_milestones(customer_id,kind,title,due_on,details) values (1003,'portfolio_review','Annual review due',today+25,'Yearly review');
  end if;

  insert into rm.tasks(advisor_id,customer_id,note_id,title,owner_label,due_on,created_at) values
    (sophie,1002,'00000000-0000-4000-8000-100200000061','Send Michelle fixed deposit options','Sophie Lim',today+5,clock_timestamp()-interval '20 days'),
    (sophie,1005,'00000000-0000-4000-8000-100500000061','Ask the product team whether Hafiz''s current account fees can be reviewed','Sophie Lim',today-2,clock_timestamp()-interval '40 days');

  -- Confirmed appointments with a recorded purpose (Grace's has none recorded), plus one held time that is not confirmed.
  insert into rm.appointments(customer_id,advisor_id,starts_at,ends_at,format,status,hold_expires_at,joining_details,purpose)
  select v.customer_id,sophie,s.at,s.at+interval '30 minutes',v.format,v.status,case when v.status='held' then clock_timestamp()+interval '3 days' end,
    (select o.joining_details from rm.meeting_options o where o.advisor_id=sophie and o.format=v.format),v.purpose
  from (values (1002,3,'14:30','video','confirmed','Periodic review documents and fixed deposit maturity',0),
               (1004,4,'11:00','phone','confirmed',null,0),
               (1005,1,'16:00','in_person','confirmed','Current account fee review',7),
               (1004,5,'15:00','video','held',null,0)) v(customer_id,dow,at_time,format,status,purpose,extra)
  cross join lateral (select ((today+((v.dow-extract(isodow from today)::int+6)%7)+1+v.extra)+v.at_time::time) at time zone 'Asia/Singapore' at) s;
  if has_david then
    insert into rm.appointments(customer_id,advisor_id,starts_at,ends_at,format,status,joining_details,purpose)
    select 1003,sophie,s.at,s.at+interval '30 minutes','in_person','confirmed',
      (select o.joining_details from rm.meeting_options o where o.advisor_id=sophie and o.format='in_person'),'Introductory meeting after the change of relationship manager'
    from (select ((today+((2-extract(isodow from today)::int+6)%7)+1)+time '10:00') at time zone 'Asia/Singapore' at) s;
  end if;

  -- Mei Ling's request raised with Rachel (its trigger opens an RM ticket) and what the bank told her about it.
  insert into rm.requests(request_id,customer_id,advisor_id,who,what,urgency,deadline,country,currency,status,created_at,updated_at) values
    ('00000000-0000-4000-8000-100700000031',1007,rachel,'Tan Mei Ling','Joint account forms to add my husband to my savings account','normal',null,'SG',null,'sent',
     '2026-10-02 11:20+08','2026-10-02 11:20+08') on conflict (request_id) do nothing;
  select * into t from support.tickets where request_id='00000000-0000-4000-8000-100700000031';
  perform support.move_ticket(t.ticket_id,'in_review','Your relationship manager has the joint account forms ready and will send them with the branch appointment options.');

  -- A chat question the assistant could not answer (a fixed deposit renewal rate), handed to the RM as a ticket.
  insert into conversation.sessions(customer_id,mode,language,started_at,last_active_at) values (1007,'personal','English','2026-10-27 20:05+08','2026-10-27 20:06+08')
    returning session_id into sess;
  insert into conversation.cases(session_id,customer_id,topic,priority,created_at) values (sess,1007,'relationship','P3','2026-10-27 20:05+08') returning case_id into kase;
  insert into conversation.messages(session_id,customer_id,role,redacted_text,created_at) values
    (sess,1007,'user','What interest rate will I get if I renew my fixed deposit in December?','2026-10-27 20:05+08') returning message_id into q;
  insert into conversation.messages(session_id,customer_id,role,redacted_text,created_at) values
    (sess,1007,'assistant','I can''t quote a renewal rate for your fixed deposit; rates are set by the bank at the time of renewal. I''ve passed your question to your relationship manager, who will reply to you. You can follow it in My requests.','2026-10-27 20:06+08')
    returning message_id into a;
  insert into conversation.turns(turn_id,session_id,customer_id,case_id,user_message_id,answer_message_id,state,run_id,request_hash,revision,created_at)
    values (gen_random_uuid(),sess,1007,kase,q,a,'completed',gen_random_uuid(),repeat('0',64),1,'2026-10-27 20:05+08');
  insert into support.handoffs(customer_id,case_id,reason,priority,summary,created_at)
    values (1007,kase,'general_support','P3','Question for your relationship manager (fees or rates): What interest rate will I get if I renew my fixed deposit in December?','2026-10-27 20:06+08')
    returning handoff_id into h;

  -- Mei Ling's handover notes for Sophie: one under active consent, one whose consent expired, one revoked.
  insert into consent.grants(grant_id,customer_id,purpose,recipient,categories,granted_at,expires_at,revoked_at) values
    ('00000000-0000-4000-8000-100700000041',1007,'rm_brief',sophie::text,array['agenda','profile'],'2026-10-27 09:00+08','2027-04-30 23:59+08',null),
    ('00000000-0000-4000-8000-100700000043',1007,'rm_brief',sophie::text,array['profile'],'2026-01-05 09:00+08','2026-10-01 00:00+08',null),
    ('00000000-0000-4000-8000-100700000045',1007,'rm_brief',sophie::text,array['profile'],'2026-10-26 09:00+08','2027-04-30 23:59+08','2026-10-28 18:00+08')
  on conflict (grant_id) do nothing;
  insert into rm.shared_summaries(summary_id,customer_id,advisor_id,content,consent_grant_id,shared_at) values
    ('00000000-0000-4000-8000-100700000042',1007,sophie,'Handover note for my new relationship manager: I am planning to move to a bigger flat next year and may need a home loan, so I would like general information first. Please call after 6pm on weekdays; I work shifts.','00000000-0000-4000-8000-100700000041','2026-10-27 09:05+08'),
    ('00000000-0000-4000-8000-100700000044',1007,sophie,'Older note (consent expired): please post statements to my old address.','00000000-0000-4000-8000-100700000043','2026-01-05 09:05+08'),
    ('00000000-0000-4000-8000-100700000046',1007,sophie,'Withdrawn note (consent revoked): details about a family matter.','00000000-0000-4000-8000-100700000045','2026-10-26 09:05+08')
  on conflict (summary_id) do nothing;
end $seed$;
