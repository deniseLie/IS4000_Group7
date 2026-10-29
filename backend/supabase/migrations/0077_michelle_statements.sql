-- Statements for the client persona (CR15). Michelle's monthly statements for the last 3 full months are built in the app
-- from her own synthetic transactions (Overview → Statements), so she needs a few months of everyday activity: her
-- consulting income on the 3rd, rent on the 14th, and Signature Card (7310) spending paid off in full on the 15th. Card
-- balances follow on: each month's charges equal the next month's payment, and September's charges (1,250.40) are her card
-- balance before the 27 Oct charges in 0075. Invented merchants; synthetic data only.
--
-- Older or certified copies are a "Statement or document copy" request (0071). The statements article v2 says so and
-- replaces the generic v1 text that pointed to "your bank's official app".

insert into banking.transactions (transaction_id, customer_id, account_id, card_id, txn_date, merchant, description, amount, status, is_overseas) values
  (100218, 1002, 10021, null, '2026-07-03 09:30+08', 'Demo Consulting Clients', 'Invoice payment received', 18500.00, 'posted', false),
  (100219, 1002, 10021, null, '2026-07-14 16:10+08', 'Demo Property Mgmt', 'Rental payment', -6200.00, 'posted', false),
  (100220, 1002, 10021, null, '2026-07-15 08:00+08', 'Signature Card payment', 'Card payment, card ending 7310', -860.15, 'posted', false),
  (100221, 1002, null, 10021, '2026-07-06 18:22+08', 'Riverside Grocer', 'Card purchase', -212.35, 'posted', false),
  (100222, 1002, null, 10021, '2026-07-12 22:41+08', 'Metro Cab', 'Card purchase', -48.60, 'posted', false),
  (100223, 1002, null, 10021, '2026-07-15 08:00+08', 'Payment received, thank you', 'Card payment', 860.15, 'posted', false),
  (100224, 1002, null, 10021, '2026-07-24 20:05+08', 'Lotus Dining', 'Card purchase', -136.80, 'posted', false),
  (100225, 1002, 10021, null, '2026-08-03 09:30+08', 'Demo Consulting Clients', 'Invoice payment received', 18500.00, 'posted', false),
  (100226, 1002, 10021, null, '2026-08-14 16:10+08', 'Demo Property Mgmt', 'Rental payment', -6200.00, 'posted', false),
  (100227, 1002, 10021, null, '2026-08-15 08:00+08', 'Signature Card payment', 'Card payment, card ending 7310', -397.75, 'posted', false),
  (100228, 1002, null, 10021, '2026-08-02 11:15+08', 'Riverside Grocer', 'Card purchase', -188.90, 'posted', false),
  (100229, 1002, null, 10021, '2026-08-15 08:00+08', 'Payment received, thank you', 'Card payment', 397.75, 'posted', false),
  (100230, 1002, null, 10021, '2026-08-16 15:30+08', 'Harbour Books', 'Card purchase', -64.20, 'posted', false),
  (100231, 1002, null, 10021, '2026-08-27 19:48+08', 'Lotus Dining', 'Card purchase', -152.40, 'posted', false),
  (100232, 1002, 10021, null, '2026-09-03 09:30+08', 'Demo Consulting Clients', 'Invoice payment received', 18500.00, 'posted', false),
  (100233, 1002, 10021, null, '2026-09-14 16:10+08', 'Demo Property Mgmt', 'Rental payment', -6200.00, 'posted', false),
  (100234, 1002, 10021, null, '2026-09-15 08:00+08', 'Signature Card payment', 'Card payment, card ending 7310', -405.50, 'posted', false),
  (100235, 1002, null, 10021, '2026-09-05 10:02+08', 'Riverside Grocer', 'Card purchase', -201.15, 'posted', false),
  (100236, 1002, null, 10021, '2026-09-15 08:00+08', 'Payment received, thank you', 'Card payment', 405.50, 'posted', false),
  (100237, 1002, null, 10021, '2026-09-19 14:20+08', 'Bayfront Hotel Spa', 'Card purchase', -780.00, 'posted', false),
  (100238, 1002, null, 10021, '2026-09-26 23:10+08', 'Metro Cab', 'Card purchase', -269.25, 'posted', false)
on conflict (transaction_id) do nothing;

update kb.articles set status='retired' where article_id='statements' and version=1 and status='published';
insert into kb.articles(article_id,version,title,categories,publisher,scope,kind,status,reference_url,checked_on,review_due) values
  ('statements',2,'Getting a statement',array['retail','document'],'DemoBank coursework project','Fictional demo guidance; not a live bank policy.','demo','published',null,null,null);
insert into kb.sections(article_id,version,anchor,heading,body,keywords) values
  ('statements',2,'main','Getting a statement','To get a monthly statement in DemoBank: 1. Open Overview. 2. Scroll to Statements. 3. Choose the account or card and the month. 4. Select View statement, then Print or save as PDF. Statements for the last three full months are available there. For an older month, a certified copy or a paper statement, raise a Statement or document copy request under My requests with the account or card, the period and the format; the operations team prepares it and you can follow it under My requests. The assistant cannot open, download or send a statement itself. These are synthetic demo statements, not real bank documents.','statement statements e-statement monthly statement download print pdf certified copy older statement');
