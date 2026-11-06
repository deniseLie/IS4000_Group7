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
