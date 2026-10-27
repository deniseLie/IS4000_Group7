-- Every demo persona has a relationship manager, so no customer sees an empty "no relationship manager" state.
-- Alex and Michelle keep one long-standing advisor (no change, so no transition wording); David's change is unchanged.
-- Synthetic data only; working hours and joining details mirror the existing advisor seed.

insert into rm.advisors (advisor_id, full_name, role, team_name, email, phone) values
  ('00000000-0000-4000-8000-100100000002', 'Priya Nair (DEMO)',  'Relationship Manager',        'Personal Banking Team C', 'priya.nair@demobank.example', '+65 6000 0002'),
  ('00000000-0000-4000-8000-100200000002', 'Daniel Tan (DEMO)',  'Senior Relationship Manager', 'Private Clients Team A',  'daniel.tan@demobank.example', '+65 6000 0003')
on conflict (advisor_id) do nothing;

insert into rm.assignments (assignment_id, customer_id, advisor_id, started_on, ended_on, transition_reason_code) values
  ('00000000-0000-4000-8000-100100000012', 1001, '00000000-0000-4000-8000-100100000002', '2023-06-01', null, null),
  ('00000000-0000-4000-8000-100200000012', 1002, '00000000-0000-4000-8000-100200000002', '2022-02-14', null, null)
on conflict (assignment_id) do nothing;

insert into rm.working_hours (advisor_id, weekday, starts_at, ends_at)
select a::uuid, d, '09:00', '17:00'
from unnest(array['00000000-0000-4000-8000-100100000002','00000000-0000-4000-8000-100200000002']) a, generate_series(1, 5) d
on conflict (advisor_id, weekday) do nothing;

insert into rm.meeting_options (advisor_id, format, joining_details) values
  ('00000000-0000-4000-8000-100100000002','video','Join from Appointments in the DemoBank app at the booked time. Demo meeting room: DEMO-PN-ROOM-1 (synthetic, no live video service).'),
  ('00000000-0000-4000-8000-100100000002','phone','Priya Nair (DEMO) calls the phone number recorded on your profile at the booked time.'),
  ('00000000-0000-4000-8000-100100000002','in_person','DemoBank Tampines Branch (DEMO), 2 Demo Avenue, Singapore. Show your appointment at reception.'),
  ('00000000-0000-4000-8000-100200000002','video','Join from Appointments in the DemoBank app at the booked time. Demo meeting room: DEMO-DT-ROOM-1 (synthetic, no live video service).'),
  ('00000000-0000-4000-8000-100200000002','phone','Daniel Tan (DEMO) calls the phone number recorded on your profile at the booked time.'),
  ('00000000-0000-4000-8000-100200000002','in_person','DemoBank Private Clients Centre (DEMO), Level 10, 1 Demo Plaza, Singapore. Show your appointment at reception.')
on conflict do nothing;

-- Everyday activity so every overview shows recent transactions (Alex's Workflow 1 records are unchanged).
insert into banking.transactions (transaction_id, customer_id, account_id, card_id, txn_date, merchant, description, amount, status, is_overseas) values
  (100311, 1003, 10031, null, '2026-10-01 08:00+08', 'Employer Holdings (DEMO)', 'Salary',                  14800.00, 'posted', false),
  (100312, 1003, 10031, null, '2026-10-05 10:20+08', 'Demo Utilities',           'GIRO bill payment',         -186.40, 'posted', false),
  (100313, 1003, 10031, null, '2026-10-12 19:05+08', 'Demo Insurance Co',        'Policy premium',            -420.00, 'posted', false),
  (100314, 1003, 10031, null, '2026-10-20 13:45+08', 'Demo Brokerage',           'Transfer to investment',  -5000.00, 'posted', false),
  (100212, 1002, 10021, null, '2026-10-03 09:30+08', 'Demo Consulting Clients',  'Invoice payment received', 18500.00, 'posted', false),
  (100213, 1002, 10021, null, '2026-10-14 16:10+08', 'Demo Property Mgmt',       'Rental payment',           -6200.00, 'posted', false)
on conflict (transaction_id) do nothing;
