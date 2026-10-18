-- Synthetic seed for Alex, Michelle and David (docs/synthetic-data-spec.md). Fake data only.
-- Safe to re-run: every insert skips existing ids. Create the three Auth users (emails = demo_email) first,
-- or re-run the final link statement after creating them.

-- Profiles
insert into banking.customer_profiles (customer_id, demo_email, full_name, income_range, employment_status, preferred_language) values
  (1001, 'demo.alex@example.com',     'Alex Tan (DEMO)',    'SGD 5,000 - 8,000 / month',     'employed',      'en'),
  (1002, 'demo.michelle@example.com', 'Michelle Ng (DEMO)', 'SGD 20,000 and above / month',  'self_employed', 'en'),
  (1003, 'demo.david@example.com',    'David Lee (DEMO)',   'SGD 12,000 - 20,000 / month',   'employed',      'en')
on conflict (customer_id) do nothing;

-- Banking
insert into banking.bank_accounts (account_id, customer_id, account_type, masked_account_number, balance, currency, opened_on) values
  (10011, 1001, 'savings',        '****6012',   5230.45, 'SGD', '2021-04-12'),
  (10021, 1002, 'multi_currency', '****7734', 250000.00, 'SGD', '2018-09-03'),
  (10031, 1003, 'current',        '****3390',  84120.00, 'SGD', '2019-01-21')
on conflict (account_id) do nothing;

insert into banking.credit_cards (card_id, customer_id, card_name, network, last4, credit_limit, outstanding_balance, minimum_payment_due, payment_due_date, status) values
  (10011, 1001, 'Rewards Card', 'visa', '4821', 8000.00, 2464.20, 50.00, '2026-11-05', 'active')
on conflict (card_id) do nothing;

insert into banking.transactions (transaction_id, customer_id, account_id, card_id, txn_date, merchant, description, amount, status, is_overseas) values
  (100111, 1001, null,  10011, '2026-10-17 21:42+08', 'ONLINE MERCHANT 8831',   'Card purchase',          -2400.00, 'posted',  true),
  (100112, 1001, 10011, null,  '2026-10-17 09:15+08', 'Transfer to landlord',   'FAST transfer, rent',    -1800.00, 'pending', false),
  (100113, 1001, null,  10011, '2026-10-15 18:30+08', 'Demo Supermarket',       'Card purchase',            -64.20, 'posted',  false),
  (100114, 1001, 10011, null,  '2026-10-01 08:00+08', 'Employer Pte Ltd (DEMO)', 'Salary',                  5600.00, 'posted',  false),
  (100211, 1002, 10021, null,  '2026-09-22 11:05+08', 'Incoming transfer (DEMO)', 'Telegraphic transfer in', 120000.00, 'posted', true)
on conflict (transaction_id) do nothing;

insert into banking.loans (loan_id, customer_id, loan_type, original_amount, outstanding_amount, monthly_repayment, next_due_date) values
  (10011, 1001, 'personal', 10000.00, 6200.00, 320.00, '2026-11-01')
on conflict (loan_id) do nothing;

-- Michelle: compliance case
insert into compliance.cases (case_id, customer_id, case_type, title, notice_text, notice_date, due_date, status, status_updated_at) values
  ('00000000-0000-4000-8000-100200000001', 1002, 'periodic_review', 'Periodic client review 2026',
   'As part of our regular client review, please provide the documents listed in your checklist by 15 November 2026.',
   '2026-10-01', '2026-11-15', 'awaiting_client_documents', '2026-10-12 10:00+08')
on conflict (case_id) do nothing;

insert into compliance.case_internal (case_id, risk_rating, reviewer_note) values
  ('00000000-0000-4000-8000-100200000001', 'medium', 'INTERNAL (DEMO): never shown to the client.')
on conflict (case_id) do nothing;

insert into compliance.requested_items (item_id, case_id, customer_id, label, instructions, submission_status, sort_order) values
  ('00000000-0000-4000-8000-100200000011', '00000000-0000-4000-8000-100200000001', 1002, 'Proof of residential address',
   'A utility bill or bank statement dated within the last 3 months, showing your full name and address.', 'not_submitted', 1),
  ('00000000-0000-4000-8000-100200000012', '00000000-0000-4000-8000-100200000001', 1002, 'Source of wealth declaration',
   'Complete and sign the source of wealth form.', 'submitted_awaiting_review', 2),
  ('00000000-0000-4000-8000-100200000013', '00000000-0000-4000-8000-100200000001', 1002, 'Bank statements (last 3 months)',
   'Statements from your main external bank account.', 'submitted_awaiting_review', 3),
  ('00000000-0000-4000-8000-100200000014', '00000000-0000-4000-8000-100200000001', 1002, 'Tax residency self-certification',
   'Complete the self-certification form for each country where you are tax resident.', 'unknown', 4)
on conflict (item_id) do nothing;

insert into compliance.status_history (case_id, customer_id, status, note, changed_at)
select '00000000-0000-4000-8000-100200000001'::uuid, 1002, s, n, at::timestamptz
from (values
  ('review_opened',                'Your periodic review has started.',       '2026-10-01 09:00+08'),
  ('documents_requested',          'Documents requested in your notice.',     '2026-10-01 09:05+08'),
  ('documents_partially_received', 'We received some of your documents.',     '2026-10-10 15:20+08'),
  ('awaiting_client_documents',    'Some documents are still outstanding.',   '2026-10-12 10:00+08')) v(s, n, at)
where not exists (select 1 from compliance.status_history where case_id = '00000000-0000-4000-8000-100200000001');

insert into compliance.info_requests (request_id, case_id, customer_id, question, instructions, due_date, status, created_at) values
  ('00000000-0000-4000-8000-100200000021', '00000000-0000-4000-8000-100200000001', 1002,
   'Please explain the source of the SGD 120,000 incoming transfer received on 22 September 2026.',
   'Reply through secure messaging and attach any supporting document, such as a sale agreement or contract.',
   '2026-10-31', 'open', '2026-10-12 10:00+08')
on conflict (request_id) do nothing;

-- David: relationship manager change
insert into rm.advisors (advisor_id, full_name, role, team_name, email, phone) values
  ('00000000-0000-4000-8000-100300000001', 'Rachel Goh (DEMO)',  'Relationship Manager',        'Private Clients Team A', 'rachel.goh@demobank.example',  '+65 6000 0001'),
  ('00000000-0000-4000-8000-100300000002', 'Marcus Chua (DEMO)', 'Senior Relationship Manager', 'Private Clients Team B', 'marcus.chua@demobank.example', '+65 6000 0000')
on conflict (advisor_id) do nothing;

insert into rm.assignments (assignment_id, customer_id, advisor_id, started_on, ended_on, transition_reason_code) values
  ('00000000-0000-4000-8000-100300000011', 1003, '00000000-0000-4000-8000-100300000001', '2024-03-01', '2026-10-14', null),
  ('00000000-0000-4000-8000-100300000012', 1003, '00000000-0000-4000-8000-100300000002', '2026-10-15', null, 'team_restructure')
on conflict (assignment_id) do nothing;

insert into rm.working_hours (advisor_id, weekday, starts_at, ends_at)
select '00000000-0000-4000-8000-100300000002'::uuid, d, '09:00', '17:00' from generate_series(1, 5) d
on conflict (advisor_id, weekday) do nothing;

insert into rm.advisor_blocks (block_id, advisor_id, starts_at, ends_at) values
  ('00000000-0000-4000-8000-100300000021', '00000000-0000-4000-8000-100300000002', '2026-10-20 09:00+08', '2026-10-20 12:00+08'),
  ('00000000-0000-4000-8000-100300000022', '00000000-0000-4000-8000-100300000002', '2026-10-21 14:00+08', '2026-10-21 15:00+08')
on conflict (block_id) do nothing;

insert into rm.requests (request_id, customer_id, advisor_id, who, what, urgency, deadline, country, currency, status, created_at, updated_at) values
  ('00000000-0000-4000-8000-100300000031', 1003, '00000000-0000-4000-8000-100300000001', 'David Lee',
   'Update standing instruction for the quarterly USD transfer', 'normal', '2026-11-30', 'SG', 'USD', 'seen',
   '2026-10-08 14:00+08', '2026-10-09 09:30+08')
on conflict (request_id) do nothing;

insert into events.outbox (event_type, customer_id, payload)
select 'rm.assignment_changed', 1003, '{"assignment_id": "00000000-0000-4000-8000-100300000012"}'::jsonb
where not exists (select 1 from events.outbox where event_type = 'rm.assignment_changed' and customer_id = 1003);

-- Notifications
insert into notify.templates (template_key, version, locale, channel, title, body, is_service) values
  ('card.blocked',                 1, 'en', 'in_app', 'Card temporarily blocked', 'Your card ending {last4} is temporarily blocked. No new transactions will go through until it is unblocked.', true),
  ('compliance.deadline_reminder', 1, 'en', 'in_app', 'Review deadline reminder', 'Your {case_title} has outstanding items due by {due_date}.', true),
  ('rm.assignment_changed',        1, 'en', 'in_app', 'Your relationship manager has changed', '{advisor_name} is now your relationship manager.', true),
  ('consent.requested',            1, 'en', 'in_app', 'Consent requested', 'Please review a request to share {categories} with {recipient}.', false)
on conflict do nothing;

insert into notify.preferences (customer_id, channels, quiet_start, quiet_end) values
  (1001, '{in_app}',       '22:00', '07:00'),
  (1002, '{in_app,email}', null,    null),
  (1003, '{in_app}',       null,    null)
on conflict (customer_id) do nothing;

-- Link each persona to its Auth user (no-op until the user exists). Re-run after creating the users.
update banking.customer_profiles p set auth_user_id = u.id
from auth.users u
where p.customer_id in (1001, 1002, 1003) and p.auth_user_id is null and lower(u.email) = p.demo_email;
