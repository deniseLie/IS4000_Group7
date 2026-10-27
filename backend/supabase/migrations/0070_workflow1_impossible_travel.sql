-- Workflow 1 Part B, revised (docs/workflows/workflow-1-alex.md): "impossible travel". On Fri 17 Oct 2026 card 4821 is used
-- in Singapore at 7:15 pm SGT and then twice in the United States at 9:42 pm and 9:58 pm SGT. The rule in fraud_records flags
-- the later charges, so each card charge now records its merchant country and the reader returns the charge time.
-- 0064's two small card-testing charges (100115/100116) are rewritten in place rather than deleted. Invented merchants; synthetic data only.

alter table banking.transactions add column merchant_country text check (merchant_country ~ '^[A-Z]{2}$');

insert into banking.transactions (transaction_id, customer_id, account_id, card_id, txn_date, merchant, description, amount, status, is_overseas, merchant_country) values
  (100115, 1001, null, 10011, '2026-10-17 19:15+08', 'Hawker Delight, Tanjong Pagar', 'Card purchase', -12.50, 'posted', false, 'SG'),
  (100116, 1001, null, 10011, '2026-10-17 21:42+08', 'SUNSET GRILL LA',               'Card purchase', -86.40, 'posted', true,  'US'),
  (100111, 1001, null, 10011, '2026-10-17 21:58+08', 'TECHVAULT ELECTRONICS',         'Card purchase', -2400.00, 'posted', true, 'US')
on conflict (transaction_id) do update set txn_date=excluded.txn_date, merchant=excluded.merchant, description=excluded.description,
  amount=excluded.amount, status=excluded.status, is_overseas=excluded.is_overseas, merchant_country=excluded.merchant_country
  where banking.transactions.customer_id=1001 and banking.transactions.card_id=10011;
update banking.transactions set merchant_country='SG' where transaction_id=100113 and customer_id=1001 and card_id=10011;

-- The outstanding balance follows the charges (64.20 + 2,400.00 + 12.50 + 86.40); a card changed since its seed stays untouched.
update banking.credit_cards set outstanding_balance = 2563.10
where card_id = 10011 and customer_id = 1001 and outstanding_balance in (2464.20, 2470.19);

-- Same checks, filters and order as 0028; adds the charge time and merchant country for the travel rule.
create or replace function public.customer_read_transactions(p_customer_id bigint,p_from date,p_to date,p_filters jsonb)
returns jsonb language plpgsql volatile security invoker set search_path=''
as $$
declare cid bigint:=banking.require_read_customer(p_customer_id); data jsonb;
begin
  if (p_from is not null and p_to is not null and p_from>p_to) or jsonb_typeof(p_filters) is distinct from 'object' then
    raise exception 'transaction reads require ordered dates and an object of filters' using errcode='22023';
  end if;
  if exists(select 1 from banking.transactions t where t.customer_id=cid and (
      (t.card_id is not null and not exists(select 1 from banking.credit_cards c where c.customer_id=cid and c.card_id=t.card_id)) or
      (t.account_id is not null and not exists(select 1 from banking.bank_accounts a where a.customer_id=cid and a.account_id=t.account_id)))) then
    raise exception 'a customer transaction references an unavailable owned account or card' using errcode='23514';
  end if;
  select coalesce(jsonb_agg(r.data order by r.txn_date desc,r.transaction_id desc),'[]') into data from(
    select t.txn_date,t.transaction_id,jsonb_build_object('txn_id','txn_'||t.transaction_id,
      'date',(t.txn_date at time zone 'Asia/Singapore')::date,'txn_at',t.txn_date,'description',t.merchant,'amount',t.amount,'currency',t.currency,
      'status',t.status,'card_id',case when c.card_id is not null then 'card_'||c.card_id end,'card_last4',c.last4,
      'is_overseas',t.is_overseas,'merchant_country',t.merchant_country,'failure_reason',t.failure_reason) data
    from banking.transactions t left join banking.credit_cards c on c.card_id=t.card_id and c.customer_id=cid
    where t.customer_id=cid and (p_from is null or t.txn_date>=p_from::timestamp at time zone 'Asia/Singapore')
      and (p_to is null or t.txn_date<(p_to+1)::timestamp at time zone 'Asia/Singapore')
      and (not(p_filters?'status') or t.status=p_filters->>'status')
      and (not(p_filters?'txn_id') or 'txn_'||t.transaction_id=p_filters->>'txn_id')
      and (not(p_filters?'card_id') or 'card_'||t.card_id=p_filters->>'card_id')
      and (not(p_filters?'currency') or t.currency=p_filters->>'currency')
      and (not(p_filters?'is_overseas') or t.is_overseas=(p_filters->>'is_overseas')::boolean)
    order by t.txn_date desc,t.transaction_id desc limit 200)r;
  return banking.customer_result(cid,'customer.get_transactions','banking.transactions',data);
end $$;

-- App login help after an unexpected one-time code: the lock is protective, reset only in the official app, never share the code.
-- The assistant has no sign-in or one-time-code records, so the guidance says so; any link to card charges comes from the customer.
update kb.articles set status='retired' where article_id='app-login-recovery' and version=1 and status='published';
insert into kb.articles(article_id,version,title,categories,publisher,scope,kind,status,reference_url,checked_on,review_due) values
  ('app-login-recovery',2,'Mobile app login recovery',array['retail','fraud'],'DemoBank coursework project','Fictional demo guidance; not a live bank policy.','demo','published',null,null,null);
insert into kb.sections(article_id,version,anchor,heading,body,keywords) values
  ('app-login-recovery',2,'steps','Mobile app login recovery','If you are locked out of the mobile app: 1. Close and reopen the official app and check that it is updated. 2. On the sign-in screen choose Forgot password and follow the steps; a one-time code is sent only to your registered phone. 3. Enter that code only in the official app, never in chat, email or a phone call. 4. If the app says your access is locked after several attempts, wait for the stated time or use the official app''s Help service to unlock it. 5. If you did not try to sign in yourself, treat it as a security issue and contact your bank through its official app or website. This chat never needs your password, PIN or one-time code.','locked locked out lockout app mobile phone login log in sign in forgot password unlock access'),
  ('app-login-recovery',2,'unexpected-code','A one-time code you did not ask for','If you received a one-time code (OTP) by SMS that you did not request, someone else may have tried to sign in to your account with your details. If the app then says your account is locked for your security, the lock is protecting you: it stops further sign-in attempts and is not a penalty. If card charges you do not recognise appeared around the same time, the two are likely linked; report both to the bank. Reset your password only inside the official DemoBank app with Forgot password. Never use a link in an SMS, email or chat message, even one that looks like it is from DemoBank. Never share a one-time code with anyone, including callers, bank staff and this chat; DemoBank never asks for it. This chat cannot see sign-in attempts, devices or where a sign-in came from.','otp one-time code code sms text message did not request didnt ask weird unexpected locked for your security account locked someone else sign in attempt reset password link');
