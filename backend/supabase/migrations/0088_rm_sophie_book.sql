-- F4: synthetic data for Persona 2, Sophie Lim (DEMO), relationship manager. Synthetic data only.
-- Michelle's long-standing advisor record (0063) becomes Sophie, so Michelle sees no relationship-manager change and her
-- appointments, hours and joining details stay consistent. David's current assignment (0004) moves from Marcus to
-- Sophie; Rachel stays his previous RM and leaves three meeting notes and a handover note he shared with consent.
-- Two more synthetic clients join Sophie's book and one belongs to Priya (another RM, like Alex) for access tests.
-- Open tickets across the book mix categories and urgency, with one urgent suspected fraud and one past its target.
-- Rows for David are added only where he exists (the hosted project keeps only Michelle, 06_keep_only_michelle.sql).
--
-- Sophie's login: create the Auth user demo.sophie@example.com, then set its app_metadata to
-- {"demobank_role":"rm","rm_id":"00000000-0000-4000-8000-100200000002"} through an administrator and re-run the link
-- statement at the end (step 6). RM access rules (F12) and RM reads (F14) come later; until then no policy lets Sophie read
-- customer rows, and meeting notes and staff logins are readable by no client.

-- 1. Sophie
update rm.advisors set full_name='Sophie Lim (DEMO)',role='Senior Relationship Manager',team_name='Private Clients Team A',
  email='sophie.lim@demobank.example'
where advisor_id='00000000-0000-4000-8000-100200000002';
update rm.meeting_options set joining_details=replace(replace(joining_details,'Daniel Tan (DEMO)','Sophie Lim (DEMO)'),'DEMO-DT-ROOM-1','DEMO-SL-ROOM-1')
where advisor_id='00000000-0000-4000-8000-100200000002';

create table rm.staff_logins (
  advisor_id   uuid primary key references rm.advisors(advisor_id) on delete cascade,
  demo_email   text not null unique check (demo_email=lower(demo_email)),
  auth_user_id uuid unique references auth.users(id) on delete set null
);
alter table rm.staff_logins enable row level security;
revoke all on rm.staff_logins from public,anon,authenticated;
insert into rm.staff_logins(advisor_id,demo_email) values ('00000000-0000-4000-8000-100200000002','demo.sophie@example.com')
on conflict (advisor_id) do nothing;

-- 2. Sophie's book: Michelle (already assigned), David (inherited), Grace and Hafiz. Wei Ming belongs to Priya.
insert into banking.customer_profiles (customer_id, demo_email, full_name, income_range, employment_status, preferred_language) values
  (1004, 'demo.grace@example.com',   'Grace Koh (DEMO)',     'SGD 12,000 - 20,000 / month', 'employed',      'en'),
  (1005, 'demo.hafiz@example.com',   'Hafiz Rahman (DEMO)',  'SGD 20,000 and above / month', 'self_employed', 'en'),
  (1006, 'demo.weiming@example.com', 'Ong Wei Ming (DEMO)',  'SGD 8,000 - 12,000 / month',  'employed',      'en')
on conflict (customer_id) do nothing;

insert into banking.bank_accounts (account_id, customer_id, account_type, masked_account_number, balance, currency, opened_on) values
  (10041, 1004, 'savings',  '****2281',  46210.80, 'SGD', '2020-07-15'),
  (10051, 1005, 'current',  '****9904', 132560.00, 'SGD', '2017-03-28'),
  (10061, 1006, 'savings',  '****5517',  18940.25, 'SGD', '2022-11-02')
on conflict (account_id) do nothing;

insert into banking.credit_cards (card_id, customer_id, card_name, network, last4, credit_limit, outstanding_balance, minimum_payment_due, payment_due_date, status) values
  (10041, 1004, 'Rewards Card',   'visa',       '2207', 12000.00, 1840.35, 50.00, '2026-11-22', 'active'),
  (10051, 1005, 'Signature Card', 'mastercard', '6634', 30000.00, 4120.00, 82.40, '2026-11-18', 'active')
on conflict (card_id) do nothing;

insert into rm.assignments (assignment_id, customer_id, advisor_id, started_on, ended_on, transition_reason_code) values
  ('00000000-0000-4000-8000-100400000012', 1004, '00000000-0000-4000-8000-100200000002', '2024-05-02', null, null),
  ('00000000-0000-4000-8000-100500000012', 1005, '00000000-0000-4000-8000-100200000002', '2025-01-10', null, null),
  ('00000000-0000-4000-8000-100600000012', 1006, '00000000-0000-4000-8000-100100000002', '2023-09-01', null, null)
on conflict (assignment_id) do nothing;

update rm.assignments set advisor_id='00000000-0000-4000-8000-100200000002'
where assignment_id='00000000-0000-4000-8000-100300000012' and advisor_id='00000000-0000-4000-8000-100300000002';

-- 3. Meeting notes: internal RM records. No client reads them; RM book policies arrive with F12.
create table rm.meeting_notes (
  note_id     uuid primary key default gen_random_uuid(),
  customer_id bigint not null references banking.customer_profiles(customer_id) on delete cascade,
  advisor_id  uuid not null references rm.advisors(advisor_id),
  met_on      date not null,
  format      text not null check (format in ('video','phone','in_person')),
  summary     text not null check (length(btrim(summary)) between 1 and 2000),
  commitments text[] not null default '{}' check (cardinality(commitments)<=10),
  created_at  timestamptz not null default now()
);
create index meeting_notes_customer_idx on rm.meeting_notes(customer_id,met_on desc);
create index meeting_notes_advisor_idx on rm.meeting_notes(advisor_id);
alter table rm.meeting_notes enable row level security;
revoke all on rm.meeting_notes from public,anon,authenticated;
create trigger audit_row after insert or update or delete on rm.meeting_notes for each row execute function audit.row_change('note_id','format');

insert into rm.meeting_notes (note_id, customer_id, advisor_id, met_on, format, summary, commitments)
select v.note_id::uuid, 1003, '00000000-0000-4000-8000-100300000001', v.met_on::date, v.format, v.summary, v.commitments
from (values
  ('00000000-0000-4000-8000-100300000051','2025-03-12','in_person',
   'Annual review. David wants to keep about SGD 80,000 in his current account for flexibility. We discussed placing surplus cash in fixed deposits; he asked to see the options at the next review. He prefers phone calls before 10am.',
   array['Send fixed deposit options before the next review']),
  ('00000000-0000-4000-8000-100300000052','2025-11-20','phone',
   'David set up a quarterly USD transfer to his daughter''s university account in Australia. He asked to be told before any change to overseas transfer fees.',
   array['Tell David before overseas transfer fees change']),
  ('00000000-0000-4000-8000-100300000053','2026-09-02','video',
   'Mid-year check-in. David plans to change the quarterly USD transfer amount from January and will send the new figure. He may refinance his home loan in 2027 and wants general information first, not advice.',
   array['Follow up on the new USD transfer amount','Share general home loan refinancing information'])
) v(note_id,met_on,format,summary,commitments)
where exists (select 1 from banking.customer_profiles where customer_id=1003)
on conflict (note_id) do nothing;

-- 4. David's handover note for Sophie, shared under an active consent grant (purpose rm_brief, recipient Sophie).
insert into consent.grants (grant_id, customer_id, purpose, recipient, categories, granted_at, expires_at)
select '00000000-0000-4000-8000-100300000041', 1003, 'rm_brief', '00000000-0000-4000-8000-100200000002', array['agenda','profile'],
  '2026-10-16 09:55+08', '2027-04-30 23:59+08'
where exists (select 1 from banking.customer_profiles where customer_id=1003)
on conflict (grant_id) do nothing;
insert into rm.shared_summaries (summary_id, customer_id, advisor_id, content, consent_grant_id, shared_at)
select '00000000-0000-4000-8000-100300000042', 1003, '00000000-0000-4000-8000-100200000002',
  'Handover note for my new relationship manager: I prefer phone calls before 10am. My quarterly USD transfer to my daughter''s university account will change from January. I would like general information about refinancing my home loan in 2027.',
  '00000000-0000-4000-8000-100300000041', '2026-10-16 10:00+08'
where exists (select 1 from consent.grants where grant_id='00000000-0000-4000-8000-100300000041')
on conflict (summary_id) do nothing;

-- 5. Open tickets across Sophie's book (Michelle's two from 0071 and David's RM request ticket already exist).
do $$
declare t support.tickets;
begin
  if not exists (select 1 from support.tickets where source='seed' and customer_id in (1004,1005)) then
    t:=support.open_ticket(1004,null,'suspected_fraud','Two card payments on my Rewards Card ending 2207 that I did not make.',
      '{"card":"2207"}','urgent','seed');
    perform support.move_ticket(t.ticket_id,'in_review');
    t:=support.open_ticket(1004,null,'payment_issue','A FAST transfer to my contractor has been pending for two days.',
      '{"payment":"FAST transfer"}','high','seed');
    update support.tickets set created_at=clock_timestamp()-interval '3 days',target_at=clock_timestamp()-interval '6 hours'
      where ticket_id=t.ticket_id;
    t:=support.open_ticket(1005,null,'card_limit_change','Temporary higher limit on my Signature Card for a family trip in December.',
      '{"card":"6634","duration":"temporary"}','normal','seed');
    t:=support.open_ticket(1005,null,'login_access','Locked out of the app after changing my phone.','{}','high','seed');
    perform support.move_ticket(t.ticket_id,'waiting_on_client','Please confirm your new phone number in the official app so the team can reset your access.');
    t:=support.open_ticket(1005,null,'rm_handover','Question for your relationship manager: can the fees on my current account be reviewed?',
      '{"topic":"fees or rates"}','normal','seed');
  end if;
  if exists (select 1 from banking.customer_profiles where customer_id=1003)
     and not exists (select 1 from support.tickets where source='seed' and customer_id=1003) then
    t:=support.open_ticket(1003,null,'personal_details_update','Update my mobile number.','{"change":"mobile number"}','normal','seed');
    t:=support.open_ticket(1003,null,'compliance_documents','Question about the documents for my next periodic review.','{}','low','seed');
  end if;
end $$;

-- 6. Link Sophie's login when the Auth user exists (safe to re-run after creating it).
update rm.staff_logins s set auth_user_id=u.id from auth.users u
where s.auth_user_id is null and lower(u.email)=s.demo_email;
