-- One-off repair for the hosted project (7 Nov 2026): 0115 and 0116 were run directly in the SQL Editor, so their objects
-- exist but are not recorded. This records them (after checking they are present), updates 0115's sections to the
-- released text, then applies 0117 and 0118 as usual. All or nothing. For a fresh project use 42_apply_0115_0118_hosted.sql.
begin;

set local lock_timeout='10s';
set local statement_timeout='120s';

select pg_advisory_xact_lock(hashtext('demobank.hosted_migrations'));

do $guard$
begin
  if to_regclass('meetings.copilot_briefs') is not null then raise exception 'meetings.copilot_briefs already exists; stop and send its definition'; end if;
  if exists(select 1 from supabase_migrations.schema_migrations where version in ('0117','0118')) then
    raise exception '0117 or 0118 is already recorded; stop and send the inventory result'; end if;
end $guard$;

do $repair_0115$
declare previous supabase_migrations.schema_migrations; script text:=$source_0115$-- Released source SHA256: 798f89f800d21eb2876ab191fd544637d558356bc625aea910fbc80c5f361316
-- Private-client financing (storyline alignment, 7 Nov 2026): DemoBank's lending now follows the structure a private bank
-- uses (research: docs/research/ubs-practices.md §12–13): borrowing against investments (Lombard) and property financing
-- arranged with a mortgage specialist. Personal and car loans are retired: a wealth client is not offered them in this demo.
-- Demo kind only: no rates, fees, lending values, limits, periods or real contact details; no article names another bank.
-- Retired versions stay readable as citations (0011).

update kb.articles set status='retired' where status='published' and (article_id,version) in (('loans',2),('home-loans',1),('personal-loans',1),('car-loans',1));
insert into kb.articles(article_id,version,title,categories,publisher,scope,kind,status,reference_url,checked_on,review_due) values
  ('loans',3,'Demo financing enquiries',array['retail'],'DemoBank coursework project','Fictional demo guidance; not a live bank policy.','demo','published',null,null,null),
  ('home-loans',2,'Property financing',array['retail'],'DemoBank coursework project','Fictional demo guidance; not a live bank policy.','demo','published',null,null,null),
  ('lombard-financing',1,'Borrowing against your investments',array['retail'],'DemoBank coursework project','Fictional demo guidance; not a live bank policy.','demo','published',null,null,null);
insert into kb.sections(article_id,version,anchor,heading,body,keywords) values
  ('loans',3,'main','Demo financing enquiries','The fictional DemoBank offers two kinds of financing to its private clients in this demo: property financing (a mortgage to buy or refinance a residential property) and borrowing against your investments (a Lombard loan secured on cash, shares, bonds or funds you hold with the bank). DemoBank does not offer personal loans or car loans in this demo. If it is not clear which kind of financing a question is about, ask which one before describing it. New financing is arranged through your relationship manager, not through the app or this chat. Only the bank can decide on an actual application, after its own credit checks. Sign in to see the outstanding amount and status of financing you already have. This chat cannot see applications in progress, submit an application or promise approval. This is general information, not advice or a contract.','loan loans financing borrow borrowing credit eligibility eligible application which types offer existing personal car unsecured'),
  ('home-loans',2,'main','Home loans and property financing','The fictional DemoBank offers property financing to its private clients to buy a residential property or refinance an existing mortgage. The mortgage is secured on the property. In this demo it is an uncommitted facility with a variable interest rate: extra repayments can be made and there is no early repayment penalty. Interest rates, fees, maximum amounts and loan-to-value limits are not supplied in this demo. Financing is subject to the bank''s credit approval, and the bank makes the final decision. This is general information, not advice.','home loan housing loan mortgage property condo condominium house flat residential buy buying refinance financing variable repayment eligibility'),
  ('home-loans',2,'steps','How property financing is arranged','Property financing is arranged in four steps: 1. Your relationship manager discusses your plans, timeline and how the purchase fits your wider finances. 2. A mortgage specialist prepares a borrowing plan with you and tells you which documents are needed. 3. Independent specialists value the property and handle the conveyancing. 4. The bank completes its credit checks and decides. You may also be able to fund part of a purchase from your own cash or by borrowing against your investments; your relationship manager can explain the options, and only the bank decides. To start, ask your relationship manager or request a meeting in the app. The assistant cannot confirm eligibility, approval, rates or completion dates.','apply application steps process mortgage specialist borrowing plan valuation conveyancing meeting how start'),
  ('home-loans',2,'documents','Documents for property financing','The mortgage specialist confirms the exact list for your case. It usually covers identification, evidence of income and assets, details of the property (for example the option to purchase or the sale agreement) and details of any existing mortgage you want to refinance. A document in another language needs a certified English translation. Upload documents only through the official DemoBank app when you are asked to, never by email or chat.','documents document checklist mortgage property income assets identification sale agreement'),
  ('lombard-financing',1,'main','Lombard loans: borrowing against your portfolio','A Lombard loan lets you borrow against investments you hold with the fictional DemoBank, such as cash deposits, shares, bonds or funds, without selling them. It can help with short or medium-term needs, for example part of a property purchase or a business opportunity. Because the loan is secured, the interest is usually lower than on unsecured borrowing. Rates, fees and lending values are not supplied in this demo. This is general information, not advice.','lombard borrow borrowing against portfolio securities secured credit line liquidity without selling'),
  ('lombard-financing',1,'facilities','Ways to borrow against your portfolio','In this demo there are two ways to borrow against investments: a variable loan facility, a credit line you draw on when you need it and pay interest only on the amount used; and a fixed term advance, a set amount for an agreed period at an interest rate fixed for that period. Your relationship manager can explain which suits your plans; the bank agrees the terms.','variable loan facility credit line revolving fixed term advance draw drawdown period'),
  ('lombard-financing',1,'collateral','When the value of your portfolio falls','The bank alone decides which investments count as collateral and how much can be lent against each one, and it can change this. If the value of your collateral falls below the agreed limit, the bank may ask you to add collateral or repay part of the loan, and if that does not happen it may sell some of the investments. Your relationship manager will contact you if this happens. Borrowing against investments carries this risk; borrow only what you could repay.','collateral value falls drop margin call limit add repay sell risk lending value'),
  ('lombard-financing',1,'apply','How to borrow against your portfolio','A Lombard loan is arranged through your relationship manager, not through the app or this chat. Ask your relationship manager or request a meeting in the app. The bank checks your investments and makes the final decision. The chatbot cannot submit an application or promise approval.','apply application how start arrange relationship manager meeting');
$source_0115$;
begin
  select * into previous from supabase_migrations.schema_migrations where version='0115';
  if found then
    if previous.name is distinct from 'private_client_financing' or previous.statements is distinct from array[script] then
      raise exception 'migration 0115 is recorded with different SQL; inspect history before proceeding';
    end if;
    raise notice 'migration 0115 already recorded';
  else
    if (select count(*) from kb.articles where status='published' and (article_id,version) in (('loans',3),('home-loans',2),('lombard-financing',1)))<>3
       or exists(select 1 from kb.articles where status='published' and article_id in ('personal-loans','car-loans')) then
      raise exception '0115 is not fully present; stop and send the inventory result';
    end if;
    -- the earlier run used the first text of 0115; bring the three articles' sections to the released text
    update kb.sections s set heading=v.heading,body=v.body,keywords=v.keywords
      from (values   ('loans',3,'main','Demo financing enquiries','The fictional DemoBank offers two kinds of financing to its private clients in this demo: property financing (a mortgage to buy or refinance a residential property) and borrowing against your investments (a Lombard loan secured on cash, shares, bonds or funds you hold with the bank). DemoBank does not offer personal loans or car loans in this demo. If it is not clear which kind of financing a question is about, ask which one before describing it. New financing is arranged through your relationship manager, not through the app or this chat. Only the bank can decide on an actual application, after its own credit checks. Sign in to see the outstanding amount and status of financing you already have. This chat cannot see applications in progress, submit an application or promise approval. This is general information, not advice or a contract.','loan loans financing borrow borrowing credit eligibility eligible application which types offer existing personal car unsecured'),
  ('home-loans',2,'main','Home loans and property financing','The fictional DemoBank offers property financing to its private clients to buy a residential property or refinance an existing mortgage. The mortgage is secured on the property. In this demo it is an uncommitted facility with a variable interest rate: extra repayments can be made and there is no early repayment penalty. Interest rates, fees, maximum amounts and loan-to-value limits are not supplied in this demo. Financing is subject to the bank''s credit approval, and the bank makes the final decision. This is general information, not advice.','home loan housing loan mortgage property condo condominium house flat residential buy buying refinance financing variable repayment eligibility'),
  ('home-loans',2,'steps','How property financing is arranged','Property financing is arranged in four steps: 1. Your relationship manager discusses your plans, timeline and how the purchase fits your wider finances. 2. A mortgage specialist prepares a borrowing plan with you and tells you which documents are needed. 3. Independent specialists value the property and handle the conveyancing. 4. The bank completes its credit checks and decides. You may also be able to fund part of a purchase from your own cash or by borrowing against your investments; your relationship manager can explain the options, and only the bank decides. To start, ask your relationship manager or request a meeting in the app. The assistant cannot confirm eligibility, approval, rates or completion dates.','apply application steps process mortgage specialist borrowing plan valuation conveyancing meeting how start'),
  ('home-loans',2,'documents','Documents for property financing','The mortgage specialist confirms the exact list for your case. It usually covers identification, evidence of income and assets, details of the property (for example the option to purchase or the sale agreement) and details of any existing mortgage you want to refinance. A document in another language needs a certified English translation. Upload documents only through the official DemoBank app when you are asked to, never by email or chat.','documents document checklist mortgage property income assets identification sale agreement'),
  ('lombard-financing',1,'main','Lombard loans: borrowing against your portfolio','A Lombard loan lets you borrow against investments you hold with the fictional DemoBank, such as cash deposits, shares, bonds or funds, without selling them. It can help with short or medium-term needs, for example part of a property purchase or a business opportunity. Because the loan is secured, the interest is usually lower than on unsecured borrowing. Rates, fees and lending values are not supplied in this demo. This is general information, not advice.','lombard borrow borrowing against portfolio securities secured credit line liquidity without selling'),
  ('lombard-financing',1,'facilities','Ways to borrow against your portfolio','In this demo there are two ways to borrow against investments: a variable loan facility, a credit line you draw on when you need it and pay interest only on the amount used; and a fixed term advance, a set amount for an agreed period at an interest rate fixed for that period. Your relationship manager can explain which suits your plans; the bank agrees the terms.','variable loan facility credit line revolving fixed term advance draw drawdown period'),
  ('lombard-financing',1,'collateral','When the value of your portfolio falls','The bank alone decides which investments count as collateral and how much can be lent against each one, and it can change this. If the value of your collateral falls below the agreed limit, the bank may ask you to add collateral or repay part of the loan, and if that does not happen it may sell some of the investments. Your relationship manager will contact you if this happens. Borrowing against investments carries this risk; borrow only what you could repay.','collateral value falls drop margin call limit add repay sell risk lending value'),
  ('lombard-financing',1,'apply','How to borrow against your portfolio','A Lombard loan is arranged through your relationship manager, not through the app or this chat. Ask your relationship manager or request a meeting in the app. The bank checks your investments and makes the final decision. The chatbot cannot submit an application or promise approval.','apply application how start arrange relationship manager meeting')) v(article_id,version,anchor,heading,body,keywords)
      where s.article_id=v.article_id and s.version=v.version and s.anchor=v.anchor;
    insert into supabase_migrations.schema_migrations(version,name,statements) values('0115','private_client_financing',array[script]);
    raise notice 'migration 0115 recorded (its objects were already present)';
  end if;
end $repair_0115$;

do $repair_0116$
declare previous supabase_migrations.schema_migrations; script text:=$source_0116$-- Released source SHA256: fef2c4662e046d7113ccaec43184f9de56c275e9f28fcdf45bfa7b86363a9797
-- Advised portfolio and Lombard collateral (storyline alignment, 7 Nov 2026; research: docs/research/ubs-practices.md §13).
--   1. banking.portfolio_holdings: a private client's holdings by asset class under an advised or managed mandate, with
--      the bank's lending value per holding (the share of its value the bank lends against). Synthetic values only.
--   2. customer_read_portfolio (her JWT, RLS + explicit predicate, audited like the other customer reads).
--   3. rm.client_data gains `portfolio` and `financing` (Lombard coverage), so the RM client page and brief show them; the
--      0089 body is kept as rm.client_data_base and wrapped, not copied.
--   4. rm.check_collateral: when Lombard borrowing exceeds the lending value of the collateral, a `collateral_alert`
--      milestone due today puts it on the RM's Today and client page; when covered again the alert is removed. Audited.
--      In production a revaluation job would call it; here the demo helper does (owner only).
--   5. Michelle's seed: an advised portfolio (her time deposit is its cash holding) and no Lombard borrowing yet (the storyline's meeting discusses it), plus
--      owner-only demo helpers: rm.demo_lombard_scenario (an advance drawn and a market fall) and banking.reset_portfolio_demo.

-- 1. Holdings ----------------------------------------------------------------------------------------------------------
create table banking.portfolio_holdings (
  holding_id bigint generated by default as identity primary key,
  customer_id bigint not null references banking.customer_profiles(customer_id) on delete cascade,
  mandate text not null check (mandate in ('advised','managed')),
  asset_class text not null check (asset_class in ('cash','bonds','equities','funds','alternatives')),
  description text not null check (length(btrim(description)) between 1 and 120),
  currency char(3) not null default 'SGD' check (currency ~ '^[A-Z]{3}$'),
  market_value numeric(14,2) not null check (market_value>=0),
  lending_value_pct numeric(5,2) not null default 0 check (lending_value_pct between 0 and 100),
  as_of date not null default current_date
);
create index portfolio_holdings_customer_idx on banking.portfolio_holdings(customer_id);
alter table banking.portfolio_holdings enable row level security;
create policy own_holdings on banking.portfolio_holdings for select to authenticated
  using (customer_id=(select banking.my_customer_id()));
revoke all on banking.portfolio_holdings from public,anon,authenticated;
grant select on banking.portfolio_holdings to authenticated;
create trigger audit_row after insert or update or delete on banking.portfolio_holdings
  for each row execute function audit.row_change('holding_id','asset_class','mandate');

-- One customer's portfolio and Lombard coverage as JSON (no access check: callers check the customer or the book).
create function banking.portfolio_json(p_customer bigint) returns jsonb language sql stable security definer set search_path='' as $$
  with h as (select * from banking.portfolio_holdings where customer_id=p_customer),
  totals as (select min(mandate) mandate,min(currency) currency,max(as_of) as_of,coalesce(sum(market_value),0) total,
      coalesce(sum(round(market_value*lending_value_pct/100,2)),0) lending from h),
  lombard as (select coalesce(sum(outstanding_amount),0) outstanding,count(*) facilities from banking.loans
      where customer_id=p_customer and loan_type='securities_backed' and status='active')
  select case when not exists(select 1 from h) then null else jsonb_build_object(
    'mandate',t.mandate,'currency',t.currency,'as_of',t.as_of,'total_value',t.total,
    'holdings',(select jsonb_agg(jsonb_build_object('holding_id','holding_'||h.holding_id,'asset_class',h.asset_class,'description',h.description,
        'currency',h.currency,'market_value',h.market_value) order by array_position(array['cash','bonds','equities','funds','alternatives'],h.asset_class),h.holding_id) from h),
    'lending_value',t.lending,'lombard_outstanding',l.outstanding,'lombard_facilities',l.facilities,
    'collateral_status',case when l.facilities=0 then 'no_borrowing' when l.outstanding<=t.lending then 'covered' else 'below_limit' end,
    'shortfall',greatest(l.outstanding-t.lending,0)) end
  from totals t cross join lombard l
$$;
revoke all on function banking.portfolio_json(bigint) from public,anon,authenticated;

-- 2. Her own read -----------------------------------------------------------------------------------------------------
create function public.customer_read_portfolio(p_customer_id bigint)
returns jsonb language plpgsql volatile security invoker set search_path='' as $$
declare cid bigint:=banking.require_read_customer(p_customer_id); data jsonb;
begin
  -- RLS limits the holdings to hers; the explicit predicate keeps the scope visible in the query too.
  select case when count(*)=0 then null else jsonb_build_object('mandate',min(mandate),'currency',min(currency),'as_of',max(as_of),
      'total_value',sum(market_value),
      'holdings',jsonb_agg(jsonb_build_object('holding_id','holding_'||holding_id,'asset_class',asset_class,'description',description,
        'currency',currency,'market_value',market_value) order by array_position(array['cash','bonds','equities','funds','alternatives'],asset_class),holding_id)) end
    into data from banking.portfolio_holdings where customer_id=cid;
  return banking.customer_result(cid,'customer.get_portfolio','banking.portfolio_holdings',data);
end $$;
revoke all on function public.customer_read_portfolio(bigint) from public,anon;
grant execute on function public.customer_read_portfolio(bigint) to authenticated;

-- 3. The RM's client data ----------------------------------------------------------------------------------------------
alter function rm.client_data(uuid,bigint) rename to client_data_base;
revoke all on function rm.client_data_base(uuid,bigint) from public,anon,authenticated;
create function rm.client_data(p_advisor uuid,p_customer bigint) returns jsonb language plpgsql stable security definer set search_path='' as $$
declare data jsonb:=rm.client_data_base(p_advisor,p_customer); p jsonb:=banking.portfolio_json(p_customer);
begin
  return data||jsonb_build_object('portfolio',case when p is null then null else p-'lending_value'-'lombard_outstanding'-'lombard_facilities'-'collateral_status'-'shortfall' end,
    'financing',case when p is null or (p->>'lombard_facilities')::int=0 then null else jsonb_build_object('lombard_outstanding',p->'lombard_outstanding',
      'lending_value',p->'lending_value','collateral_status',p->'collateral_status','shortfall',p->'shortfall','currency',p->'currency') end);
end $$;
revoke all on function rm.client_data(uuid,bigint) from public,anon,authenticated;

-- 4. Collateral alert --------------------------------------------------------------------------------------------------
alter table rm.client_milestones drop constraint client_milestones_kind_check;
alter table rm.client_milestones add constraint client_milestones_kind_check check (kind in
  ('fixed_deposit_maturity','investment_maturity','portfolio_review','insurance_renewal','account_review','collateral_alert'));

create function rm.check_collateral(p_customer bigint) returns text language plpgsql volatile security definer set search_path='' as $$
declare p jsonb:=banking.portfolio_json(p_customer); status text:=coalesce(p->>'collateral_status','no_borrowing'); had boolean;
begin
  had:=exists(select 1 from rm.client_milestones where customer_id=p_customer and kind='collateral_alert');
  if status='below_limit' then
    delete from rm.client_milestones where customer_id=p_customer and kind='collateral_alert';
    insert into rm.client_milestones(customer_id,kind,title,due_on,amount,currency,details)
      values(p_customer,'collateral_alert','Lombard collateral below its lending value',rm.today(),(p->>'shortfall')::numeric,p->>'currency',
        'Borrowing '||(p->>'currency')||' '||to_char((p->>'lombard_outstanding')::numeric,'FM999,999,999,990')||' against a lending value of '
        ||(p->>'currency')||' '||to_char((p->>'lending_value')::numeric,'FM999,999,999,990')||'. Contact the client: add collateral or repay part. The bank decides any sale.');
    if not had then perform audit.record('policy','policy.collateral_alert','success','banking.portfolio_holdings',p_customer::text,
      jsonb_build_object('status',status),p_actor_type=>'system',p_actor_id=>'collateral_check',p_customer_id=>p_customer); end if;
  elsif had then
    delete from rm.client_milestones where customer_id=p_customer and kind='collateral_alert';
    perform audit.record('policy','policy.collateral_alert_cleared','success','banking.portfolio_holdings',p_customer::text,
      jsonb_build_object('status',status),p_actor_type=>'system',p_actor_id=>'collateral_check',p_customer_id=>p_customer);
  end if;
  return status;
end $$;
revoke all on function rm.check_collateral(bigint) from public,anon,authenticated;

-- 5. Seed and demo helpers ---------------------------------------------------------------------------------------------
-- Michelle (1002): an advised SGD portfolio; no Lombard borrowing until the storyline's meeting.
create function banking.reset_portfolio_demo() returns jsonb language plpgsql volatile security definer set search_path='' as $$
declare loans int; matures date:=(select min(due_on) from rm.client_milestones where customer_id=1002 and kind='fixed_deposit_maturity');
begin
  delete from banking.portfolio_holdings where customer_id=1002;
  -- Her time deposit is a cash holding, so she sees it (and its maturity) on her Overview; the RM sees it as a milestone too.
  insert into banking.portfolio_holdings(customer_id,mandate,asset_class,description,currency,market_value,lending_value_pct,as_of) values
    (1002,'advised','cash','Time deposit ****4410'||coalesce(', matures '||to_char(matures,'FMDD Mon YYYY'),''),'SGD',150000,95,current_date),
    (1002,'advised','bonds','Investment-grade bonds (SGD and USD)','SGD',380000,80,current_date),
    (1002,'advised','equities','Global equities, advised selection','SGD',450000,60,current_date),
    (1002,'advised','funds','Asia multi-asset fund','SGD',200000,50,current_date);
  with gone as (delete from banking.loans where customer_id=1002 and loan_type='securities_backed' returning 1) select count(*) into loans from gone;
  perform rm.check_collateral(1002);
  return jsonb_build_object('holdings',4,'removed_lombard_loans',loans);
end $$;
revoke all on function banking.reset_portfolio_demo() from public,anon,authenticated;
select banking.reset_portfolio_demo();

-- Storyline scene 5: Michelle has drawn a fixed term advance against her portfolio and markets fall, so the collateral
-- no longer covers the borrowing and Sophie's Today shows the alert. Owner only (hosted 43_demo_lombard_alert.sql).
create function rm.demo_lombard_scenario(p_advance numeric default 720000,p_fall_pct numeric default 35) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
begin
  if p_advance<=0 or p_fall_pct<0 or p_fall_pct>=100 then raise exception 'advance must be positive and the fall below 100 percent' using errcode='22023'; end if;
  perform banking.reset_portfolio_demo();
  insert into banking.loans(customer_id,loan_type,original_amount,outstanding_amount,monthly_repayment,next_due_date,status,currency)
    values(1002,'securities_backed',p_advance,p_advance,0,null,'active','SGD');
  update banking.portfolio_holdings set market_value=round(market_value*(100-p_fall_pct)/100,2)
    where customer_id=1002 and asset_class in ('equities','funds');
  return jsonb_build_object('collateral_status',rm.check_collateral(1002),'portfolio',banking.portfolio_json(1002));
end $$;
revoke all on function rm.demo_lombard_scenario(numeric,numeric) from public,anon,authenticated;
$source_0116$;
begin
  select * into previous from supabase_migrations.schema_migrations where version='0116';
  if found then
    if previous.name is distinct from 'advised_portfolio' or previous.statements is distinct from array[script] then
      raise exception 'migration 0116 is recorded with different SQL; inspect history before proceeding';
    end if;
    raise notice 'migration 0116 already recorded';
  else
    if to_regclass('banking.portfolio_holdings') is null or to_regprocedure('rm.client_data_base(uuid,bigint)') is null
       or to_regprocedure('rm.demo_lombard_scenario(numeric,numeric)') is null or to_regprocedure('public.customer_read_portfolio(bigint)') is null then
      raise exception '0116 is not fully present; stop and send the inventory result';
    end if;

    insert into supabase_migrations.schema_migrations(version,name,statements) values('0116','advised_portfolio',array[script]);
    raise notice 'migration 0116 recorded (its objects were already present)';
  end if;
end $repair_0116$;


do $apply_0117$
declare previous supabase_migrations.schema_migrations; script text:=$source_0117$-- Released source SHA256: 940f69b92268ed33cfb97d32b1ff0e45f944e53439a7a381229f9e206e4f0223
-- Storyline demo data (7 Nov 2026; docs/workflows/storyline-michelle.md): private-bank wording and the financing hand-off.
--   1. "Time deposit" instead of "fixed deposit" in Michelle's seeded milestone, task and note (the code stays
--      fixed_deposit_maturity; the label is what people read).
--   2. Michelle's meeting is "Property financing options" with the topics home loan and investments (Lombard borrowing is
--      discussed with the mortgage), in the current seed and in every later rm.reset_meeting_demo (wrapped, not copied).
--   3. financing_specialist: a request type the RM raises after a meeting to hand the client to the mortgage and Lombard
--      specialists with her goal, timeline and the meeting context, so the specialist does not ask her again.

-- 1. Wording ----------------------------------------------------------------------------------------------------------
create function rm.time_deposit_wording() returns void language sql volatile security definer set search_path='' as $$
  update rm.client_milestones set title=replace(title,'Fixed deposit','Time deposit'),details=replace(details,'fixed deposit','time deposit')
    where customer_id=1002 and kind='fixed_deposit_maturity';
  update rm.tasks set title=replace(title,'fixed deposit','time deposit') where customer_id=1002 and title like '%fixed deposit%';
  update rm.appointments set purpose=replace(purpose,'fixed deposit','time deposit') where customer_id=1002 and purpose like '%fixed deposit%';
  update rm.meeting_notes set summary=replace(summary,'fixed deposit','time deposit'),
      commitments=(select coalesce(array_agg(replace(c,'fixed deposit','time deposit') order by o),'{}') from unnest(commitments) with ordinality x(c,o)),
      client_requests=(select coalesce(array_agg(replace(c,'fixed deposit','time deposit') order by o),'{}') from unnest(client_requests) with ordinality x(c,o))
    where customer_id=1002 and (summary like '%fixed deposit%' or array_to_string(commitments,'|') like '%fixed deposit%'
      or array_to_string(client_requests,'|') like '%fixed deposit%');
$$;
revoke all on function rm.time_deposit_wording() from public,anon,authenticated;
select rm.time_deposit_wording();

-- 2. Michelle's meeting ------------------------------------------------------------------------------------------------
create function rm.storyline_meeting() returns int language plpgsql volatile security definer set search_path='' as $$
declare n int;
begin
  update rm.meeting_requests set purpose='Property financing options',topics=array['home_loan','investments'],updated_at=clock_timestamp()
    where request_id='00000000-0000-4000-8000-100200000071';
  get diagnostics n=row_count;
  update rm.appointments a set purpose='Property financing options',topics=array['home_loan','investments']
    from rm.meeting_requests r where r.request_id='00000000-0000-4000-8000-100200000071' and a.appointment_id=r.appointment_id;
  -- the queued meeting.scheduled event carries the topic codes too (ids and codes only); a processed event is history
  update meetings.events set payload=jsonb_set(payload,'{topics}','["home_loan","investments"]')
    where status='pending' and payload->>'request_id'='00000000-0000-4000-8000-100200000071';
  return n;
end $$;
revoke all on function rm.storyline_meeting() from public,anon,authenticated;
select rm.storyline_meeting();

alter function rm.reset_meeting_demo(bigint[]) rename to reset_meeting_demo_before_storyline;
revoke all on function rm.reset_meeting_demo_before_storyline(bigint[]) from public,anon,authenticated;
create function rm.reset_meeting_demo(p_customers bigint[] default array[1002,1007]::bigint[]) returns jsonb
language plpgsql volatile set search_path='' as $$
declare result jsonb:=rm.reset_meeting_demo_before_storyline(p_customers);
begin
  if 1002=any(p_customers) then
    perform rm.storyline_meeting();
    perform rm.time_deposit_wording();
    result:=result||jsonb_build_object('portfolio',banking.reset_portfolio_demo());
  end if;
  return result;
end $$;
revoke all on function rm.reset_meeting_demo(bigint[]) from public,anon,authenticated;

-- 3. Financing specialist hand-off -------------------------------------------------------------------------------------
insert into support.request_categories(category,version,label,description,owner_team,decided_by,target_hours,fields,documents,kb_article,customer_raisable)
values('financing_specialist',1,'Financing specialist referral',
  'Hand the client to the mortgage and Lombard specialists to prepare a borrowing plan: property financing or borrowing against investments.',
  'Mortgage and Lombard specialists','The bank''s credit team decides on any financing after its checks.',72,
  '[{"key":"financing","kind":"choice","label":"Financing to discuss","choices":["Property financing","Borrowing against investments","Both","Not sure yet"],"required":true},
    {"key":"goal","kind":"text","label":"Client goal and timeline","required":true},
    {"key":"context","kind":"text","label":"What was discussed with the client","required":false}]'::jsonb,
  array['Identification','Evidence of income and assets','Property details, for example the option to purchase'],'home-loans',true)
on conflict (category) do nothing;
$source_0117$;
begin
  select * into previous from supabase_migrations.schema_migrations where version='0117';
  if found then
    if previous.name is distinct from 'storyline_demo' or previous.statements is distinct from array[script] then
      raise exception 'migration 0117 is recorded with different SQL; inspect history before proceeding';
    end if;
    raise notice 'migration 0117 already applied with identical SQL';
  else
    execute script;
    insert into supabase_migrations.schema_migrations(version,name,statements)
      values('0117','storyline_demo',array[script]);
  end if;
end $apply_0117$;

do $apply_0118$
declare previous supabase_migrations.schema_migrations; script text:=$source_0118$-- Released source SHA256: 0c8ca7b7be8519fb940ff53aa2e3c9195cabf47037a70390452e3e55a840defc
-- Brief Writer (architecture §15.4, 7 Nov 2026): one saved, versioned pre-meeting brief per confirmed meeting.
--   * Prepared with the RM's JWT (services/meetings/brief.py runs the copilot's meeting brief for that appointment); the
--     restricted worker still reads no client records. The workspace prepares missing briefs for the next 7 days when
--     the RM opens it, so a brief is ready before she asks.
--   * Stored: the record sections, the model's checked points and the source titles. Not stored: anything from a
--     consent-gated read (topics from her chats, transaction insights, large movements); those items are re-read under
--     her consent each time the brief is opened. grants_used records which grants were active and which topics were shown.
--   * On open, rm_meeting_brief re-checks consent and deletions: a withdrawn grant or a removed topic is returned with
--     its date, and the brief renders it as "Removed by client on <date>". `stale` says notes or tasks changed since.
--   * Approval: the RM approves an exact version (rm_meeting_brief_approve); a newer version needs a new approval.
-- Owned by the meeting side. Numbered 0118 (a free number) because 0125 is the meeting team's zoom_my_notes on hosted, and
-- named meetings.copilot_briefs because the meeting team's own meetings.briefs (a different shape) already exists there.

create table meetings.copilot_briefs (
  brief_id uuid primary key default gen_random_uuid(),
  meeting_id uuid not null references rm.appointments(appointment_id) on delete cascade,
  customer_id bigint not null references banking.customer_profiles(customer_id) on delete cascade,
  advisor_id uuid not null,
  version int not null check (version>=1),
  status text not null default 'draft' check (status in ('draft','approved')),
  content jsonb not null check (jsonb_typeof(content)='object' and jsonb_typeof(content->'sections')='array' and pg_column_size(content)<=262144),
  grants_used jsonb not null default '{}' check (jsonb_typeof(grants_used)='object'),
  point_ids uuid[] not null default '{}' check (cardinality(point_ids)<=50),
  command_id uuid not null unique,
  created_at timestamptz not null default clock_timestamp(),
  approved_at timestamptz,
  unique (meeting_id,version)
);
alter table meetings.copilot_briefs enable row level security;  -- no policies: read and written only by the functions below
revoke all on meetings.copilot_briefs from public,anon,authenticated;

-- The signed-in RM's upcoming or recent confirmed meeting with a client in her current book, or a refusal.
create function rm.brief_meeting(p_meeting uuid) returns rm.appointments language plpgsql stable security definer set search_path='' as $$
declare advisor uuid:=rm.current_advisor(); a rm.appointments;
begin
  select * into a from rm.appointments where appointment_id=p_meeting;
  if not found or a.advisor_id is distinct from advisor or not rm.in_book(advisor,a.customer_id) then
    raise exception 'this meeting is not one of yours' using errcode='42501'; end if;
  if a.status<>'confirmed' then raise exception 'only a confirmed meeting has a brief' using errcode='P1001'; end if;
  return a;
end $$;

-- Re-check of one brief: grants it used that are no longer active, topics it showed that the client removed since.
create function rm.brief_recheck(b meetings.copilot_briefs) returns jsonb language plpgsql stable security definer set search_path='' as $$
declare withdrawn jsonb:='[]'; removed jsonb:='[]'; cat text; g consent.grants;
begin
  for cat in select jsonb_object_keys(b.grants_used) loop
    continue when b.grants_used->>cat is null;
    select * into g from consent.grants where grant_id=(b.grants_used->>cat)::uuid;
    if not found or g.revoked_at is not null or g.expires_at<=clock_timestamp() then
      withdrawn:=withdrawn||jsonb_build_object('category',cat,'on',rm.day((coalesce(g.revoked_at,g.expires_at,clock_timestamp()) at time zone 'Asia/Singapore')::date));
    end if;
  end loop;
  select coalesce(jsonb_agg(jsonb_build_object('point_id',i.id,'on',rm.day((coalesce(p.deleted_at,t.deleted_at) at time zone 'Asia/Singapore')::date))),'[]') into removed
    from unnest(b.point_ids) i(id)
    left join insights.talking_points p on p.point_id=i.id and p.customer_id=b.customer_id
    left join insights.point_tombstones t on t.point_id=i.id and t.customer_id=b.customer_id
    where p.deleted_at is not null or (p.point_id is null and t.point_id is not null);
  return jsonb_build_object('withdrawn',withdrawn,'removed_points',removed,
    'stale',exists(select 1 from rm.meeting_notes n where n.customer_id=b.customer_id and n.created_at>b.created_at)
      or exists(select 1 from rm.tasks k where k.customer_id=b.customer_id and k.advisor_id=b.advisor_id and k.created_at>b.created_at));
end $$;

create function rm.brief_json(b meetings.copilot_briefs) returns jsonb language sql stable security definer set search_path='' as $$
  select jsonb_build_object('brief_id',b.brief_id,'meeting_id',b.meeting_id,'customer_id',b.customer_id,'version',b.version,'status',b.status,
    'content',b.content,'grants_used',b.grants_used,'created_at',b.created_at,'created_text',rm.sgt(b.created_at),
    'approved_at',b.approved_at)||rm.brief_recheck(b)
$$;

-- The next 14 days of the RM's confirmed meetings and where each brief stands (Today and Schedule; ids, dates, codes).
create function public.rm_meeting_briefs() returns jsonb language plpgsql volatile security definer set search_path='' as $$
declare advisor uuid:=rm.current_advisor();
begin
  if advisor is null then raise exception 'relationship managers only' using errcode='42501'; end if;
  return rm.result(null,'rm.meeting_briefs','meetings.copilot_briefs',(select coalesce(jsonb_agg(jsonb_build_object(
      'meeting_id',a.appointment_id,'customer_id',a.customer_id,'client',rm.display_name(p.full_name),'starts_at',a.starts_at,'when',rm.sgt(a.starts_at),
      'format',a.format,'purpose',a.purpose,'brief_version',b.version,'brief_status',b.status,'brief_created',rm.sgt(b.created_at),
      'stale',case when b.brief_id is null then null else (rm.brief_recheck(b)->>'stale')::boolean end) order by a.starts_at),'[]')
    from rm.appointments a join banking.customer_profiles p using(customer_id)
    left join lateral (select * from meetings.copilot_briefs x where x.meeting_id=a.appointment_id order by x.version desc limit 1) b on true
    where a.advisor_id=advisor and a.status='confirmed' and a.starts_at>clock_timestamp() and a.starts_at<clock_timestamp()+interval '14 days'
      and rm.in_book(advisor,a.customer_id)));
end $$;

create function public.rm_meeting_brief(p_meeting_id uuid) returns jsonb language plpgsql volatile security definer set search_path='' as $$
declare a rm.appointments:=rm.brief_meeting(p_meeting_id); b meetings.copilot_briefs;
begin
  select * into b from meetings.copilot_briefs where meeting_id=a.appointment_id order by version desc limit 1;
  return rm.result(a.customer_id,'rm.meeting_brief','meetings.copilot_briefs',jsonb_build_object('meeting',jsonb_build_object('meeting_id',a.appointment_id,
      'customer_id',a.customer_id,'when',rm.sgt(a.starts_at),'starts_at',a.starts_at,'format',a.format,'purpose',a.purpose,'topics',to_jsonb(coalesce(a.topics,'{}'))),
    'brief',case when b.brief_id is null then null else rm.brief_json(b) end));
end $$;

-- Save a new version. grants_used comes from the active grants here, never from the caller; point ids must be hers.
create function public.rm_meeting_brief_save(p_meeting_id uuid,p_content jsonb,p_point_ids uuid[],p_command_id uuid) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
declare a rm.appointments:=rm.brief_meeting(p_meeting_id); advisor uuid:=rm.current_advisor(); b meetings.copilot_briefs; used jsonb:='{}'; cat text;
begin
  select * into b from meetings.copilot_briefs where command_id=p_command_id;
  if found then
    if b.meeting_id<>p_meeting_id then raise exception 'command id already used for another meeting' using errcode='23505'; end if;
    return jsonb_build_object('brief_id',b.brief_id,'version',b.version,'replayed',true);
  end if;
  if p_content is null or jsonb_typeof(p_content->'sections')<>'array' or jsonb_array_length(p_content->'sections') not between 1 and 12 then
    raise exception 'a brief has 1 to 12 sections' using errcode='22023'; end if;
  if exists(select 1 from unnest(coalesce(p_point_ids,'{}')) i(id) where not exists(
      select 1 from insights.talking_points p where p.point_id=i.id and p.customer_id=a.customer_id)) then
    raise exception 'a topic does not belong to this client' using errcode='22023'; end if;
  foreach cat in array array['transaction_insights','chat_topics'] loop
    used:=used||jsonb_build_object(cat,(select g.grant_id from rm.active_consent(advisor,a.customer_id) g where cat=any(g.categories) limit 1));
  end loop;
  perform pg_advisory_xact_lock(hashtext('meetings.copilot_briefs'),hashtext(p_meeting_id::text));
  insert into meetings.copilot_briefs(meeting_id,customer_id,advisor_id,version,content,grants_used,point_ids,command_id)
    values(a.appointment_id,a.customer_id,advisor,coalesce((select max(version) from meetings.copilot_briefs where meeting_id=a.appointment_id),0)+1,
      p_content,used,coalesce(p_point_ids,'{}'),p_command_id) returning * into b;
  perform audit.record('action','action.meeting_brief_saved','success','meetings.copilot_briefs',b.brief_id::text,
    jsonb_build_object('meeting_id',a.appointment_id,'version',b.version,'grants_used',used,'topics',cardinality(b.point_ids)),
    p_actor_type=>'staff',p_actor_id=>auth.uid()::text,p_customer_id=>a.customer_id);
  return jsonb_build_object('brief_id',b.brief_id,'version',b.version,'replayed',false);
end $$;

-- The RM approves the exact version she read; approving an older version than the latest is refused.
create function public.rm_meeting_brief_approve(p_meeting_id uuid,p_brief_id uuid,p_version int,p_command_id uuid) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
declare a rm.appointments:=rm.brief_meeting(p_meeting_id); b meetings.copilot_briefs;
begin
  select * into b from meetings.copilot_briefs where meeting_id=a.appointment_id order by version desc limit 1 for update;
  if not found or b.brief_id<>p_brief_id or b.version<>p_version then
    raise exception 'approval references a replaced brief; read the latest version first' using errcode='P1001'; end if;
  if b.status='approved' then return jsonb_build_object('brief_id',b.brief_id,'version',b.version,'status','approved','replayed',true); end if;
  update meetings.copilot_briefs set status='approved',approved_at=clock_timestamp() where brief_id=b.brief_id;
  perform audit.record('action','action.meeting_brief_approved','success','meetings.copilot_briefs',b.brief_id::text,
    jsonb_build_object('meeting_id',a.appointment_id,'version',b.version,'command_id',p_command_id),
    p_actor_type=>'staff',p_actor_id=>auth.uid()::text,p_customer_id=>a.customer_id);
  return jsonb_build_object('brief_id',b.brief_id,'version',b.version,'status','approved','replayed',false);
end $$;

revoke all on function rm.brief_meeting(uuid),rm.brief_recheck(meetings.copilot_briefs),rm.brief_json(meetings.copilot_briefs) from public,anon,authenticated;
revoke all on function public.rm_meeting_briefs(),public.rm_meeting_brief(uuid),public.rm_meeting_brief_save(uuid,jsonb,uuid[],uuid),
  public.rm_meeting_brief_approve(uuid,uuid,int,uuid) from public,anon;
grant execute on function public.rm_meeting_briefs(),public.rm_meeting_brief(uuid),public.rm_meeting_brief_save(uuid,jsonb,uuid[],uuid),
  public.rm_meeting_brief_approve(uuid,uuid,int,uuid) to authenticated;
$source_0118$;
begin
  select * into previous from supabase_migrations.schema_migrations where version='0118';
  if found then
    if previous.name is distinct from 'meeting_briefs' or previous.statements is distinct from array[script] then
      raise exception 'migration 0118 is recorded with different SQL; inspect history before proceeding';
    end if;
    raise notice 'migration 0118 already applied with identical SQL';
  else
    execute script;
    insert into supabase_migrations.schema_migrations(version,name,statements)
      values('0118','meeting_briefs',array[script]);
  end if;
end $apply_0118$;

commit;
select version,name from supabase_migrations.schema_migrations order by version;
