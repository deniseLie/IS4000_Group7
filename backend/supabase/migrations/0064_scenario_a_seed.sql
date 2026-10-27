-- Workflow 1 Part B (docs/workflows/workflow-1-alex.md): two small earlier charges from the same merchant as Alex's
-- unrecognised SGD 2,400 charge, so the card-testing pattern rule has records to find. Synthetic data only.
-- The card's outstanding balance includes them (2,464.20 + 1.00 + 4.99); the guard keeps a re-run or a changed card untouched.

insert into banking.transactions (transaction_id, customer_id, account_id, card_id, txn_date, merchant, description, amount, status, is_overseas) values
  (100115, 1001, null, 10011, '2026-10-12 21:03+08', 'ONLINE MERCHANT 8831', 'Card purchase', -1.00, 'posted', true),
  (100116, 1001, null, 10011, '2026-10-14 22:47+08', 'ONLINE MERCHANT 8831', 'Card purchase', -4.99, 'posted', true)
on conflict (transaction_id) do nothing;

update banking.credit_cards set outstanding_balance = 2470.19
where card_id = 10011 and customer_id = 1001 and outstanding_balance = 2464.20;
