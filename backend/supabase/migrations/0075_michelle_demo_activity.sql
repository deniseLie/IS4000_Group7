-- Michelle's own demo activity for urgent card help (CR12) and a pending payment (CR13), so the client persona can
-- demo them without another customer. On Mon 27 Oct 2026 her Signature Card (7310) is used in Singapore at 12:40 pm SGT and
-- then twice in the United Kingdom at 2:05 pm and 2:21 pm SGT, which the impossible-travel rule flags. A transfer to her
-- interior designer is still pending. Invented merchants; synthetic data only.

insert into banking.transactions (transaction_id, customer_id, account_id, card_id, txn_date, merchant, description, amount, status, is_overseas, merchant_country) values
  (100214, 1002, null, 10021, '2026-10-27 12:40+08', 'Orchard Bistro, Singapore', 'Card purchase', -68.20, 'posted', false, 'SG'),
  (100215, 1002, null, 10021, '2026-10-27 14:05+08', 'MAYFAIR AUDIO LONDON',     'Card purchase', -1.00, 'posted', true, 'GB'),
  (100216, 1002, null, 10021, '2026-10-27 14:21+08', 'LUXEHAUS LONDON',          'Card purchase', -3850.00, 'posted', true, 'GB'),
  (100217, 1002, 10021, null, '2026-10-28 09:10+08', 'Transfer to Lim Interiors', 'FAST transfer, renovation deposit', -12000.00, 'pending', false, null)
on conflict (transaction_id) do nothing;

-- The card balance follows the new charges (1,250.40 + 68.20 + 1.00 + 3,850.00); a card changed since its seed stays untouched.
update banking.credit_cards set outstanding_balance = 5169.60 where card_id = 10021 and customer_id = 1002 and outstanding_balance = 1250.40;
