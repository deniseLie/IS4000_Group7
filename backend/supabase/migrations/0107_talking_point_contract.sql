-- Final talking-point contract and gated-read purpose (RM copilot build plan 1d). 0106 is applied on the hosted project
-- (5 Nov 2026), so the changes it needs come here:
--   1. rm_talking_points always returns data as a list ([] until build plan phase 4 fills it), also when shared is false:
--      topics the client shares herself will reach her RM without the chat_topics grant (phase 4, lane 2). shared keeps
--      its meaning (the chat_topics grant) and a read without it is still audited as a consent gap.
--   2. rm_transaction_insights takes the purpose of the read (meeting_prep for a meeting brief, client_view for the
--      client page) like rm_talking_points, so the audit records why each read happened. The signature changes, so the
--      0106 function is dropped; only the app's services/rm/records.py calls it.
--   3. rm.reset_meeting_demo(customers): the 0106 demo again from today (dates in a seed are fixed on the day it is
--      applied). Owner only; the hosted reset scripts call it.

-- 1. Talking points ---------------------------------------------------------------------------------------------------
create or replace function public.rm_talking_points(p_customer_id bigint,p_purpose text) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
declare advisor uuid:=rm.current_advisor(); g uuid;
begin
  if p_purpose is null or p_purpose not in ('meeting_prep','client_view') then raise exception 'purpose must be meeting_prep or client_view' using errcode='22023'; end if;
  if not rm.in_book(advisor,p_customer_id) then return rm.deny(p_customer_id,'rm.talking_points'); end if;
  g:=rm.grant_for(p_customer_id,'chat_topics',advisor);
  -- Phase 4: inferred points only with g, confirmed points either way. Until then both lists are empty.
  return rm.gated(p_customer_id,'rm.talking_points','rm.talking_points','chat_topics',g,p_purpose,'[]'::jsonb)||jsonb_build_object('data','[]'::jsonb);
end $$;

-- 2. Transaction insights with purpose --------------------------------------------------------------------------------
drop function public.rm_transaction_insights(bigint,int);

-- What the client's accounts and cards show over the last p_days (30-180): monthly money in and out, card spending,
-- overseas spending, movements of 10,000 or more, and recurring counterparties (3 or more times). Computed here; no
-- transaction descriptions and no list of ordinary transactions. Same result as 0106.
create function public.rm_transaction_insights(p_customer_id bigint,p_purpose text,p_days int default 90) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
declare advisor uuid:=rm.current_advisor(); g uuid; since timestamptz; days int:=coalesce(p_days,90);
begin
  if p_purpose is null or p_purpose not in ('meeting_prep','client_view') then raise exception 'purpose must be meeting_prep or client_view' using errcode='22023'; end if;
  if days not between 30 and 180 then raise exception 'choose 30 to 180 days' using errcode='22023'; end if;
  if not rm.in_book(advisor,p_customer_id) then return rm.deny(p_customer_id,'rm.transaction_insights'); end if;
  g:=rm.grant_for(p_customer_id,'transaction_insights',advisor);
  if g is null then return rm.gated(p_customer_id,'rm.transaction_insights','banking.transactions','transaction_insights',null,p_purpose,null); end if;
  since:=clock_timestamp()-make_interval(days=>days);
  return rm.gated(p_customer_id,'rm.transaction_insights','banking.transactions','transaction_insights',g,p_purpose,(
    with t as (select * from banking.transactions where customer_id=p_customer_id and txn_date>=since and status in ('posted','pending'))
    select jsonb_build_object('days',days,'from',(since at time zone 'Asia/Singapore')::date,'to',rm.today(),'transactions',(select count(*) from t),
      'monthly',(select coalesce(jsonb_agg(jsonb_build_object('month',m,'currency',c,'money_in',i,'money_out',o,'count',n) order by m,c),'[]') from (
          select to_char(txn_date at time zone 'Asia/Singapore','YYYY-MM') m,currency c,coalesce(sum(amount) filter (where amount>0),0) i,
            coalesce(abs(sum(amount) filter (where amount<0)),0) o,count(*) n from t group by 1,2) x),
      'card_spending',(select coalesce(jsonb_agg(jsonb_build_object('currency',c,'amount',s,'count',n) order by c),'[]') from (
          select currency c,abs(sum(amount)) s,count(*) n from t where card_id is not null and amount<0 group by 1) x),
      'overseas',jsonb_build_object('count',(select count(*) from t where is_overseas and amount<0),
        'amounts',(select coalesce(jsonb_object_agg(c,s),'{}') from (select currency c,abs(sum(amount)) s from t where is_overseas and amount<0 group by 1) x)),
      'large_movements',(select coalesce(jsonb_agg(jsonb_build_object('date',(txn_date at time zone 'Asia/Singapore')::date,
          'date_text',rm.day((txn_date at time zone 'Asia/Singapore')::date),'direction',case when amount>0 then 'incoming' else 'outgoing' end,
          'amount',abs(amount),'currency',currency,'counterparty',merchant,'status',status) order by txn_date desc),'[]') from t where abs(amount)>=10000),
      'recurring',(select coalesce(jsonb_agg(jsonb_build_object('counterparty',merchant,'direction',d,'count',n,'typical_amount',a,'currency',c,
          'last_date',l) order by n desc,merchant),'[]') from (
          select merchant,case when amount>0 then 'incoming' else 'outgoing' end d,currency c,count(*) n,round(avg(abs(amount)),2) a,
            max((txn_date at time zone 'Asia/Singapore')::date) l from t group by 1,2,3 having count(*)>=3) x))));
end $$;

-- 3. Demo reset -------------------------------------------------------------------------------------------------------
-- Removes the meeting requests of the demo clients (Michelle 1002, Mei Ling 1007), the meetings and held times they
-- created (their meetings.events go with them, on delete cascade) and their meeting notices, then seeds the 0106 demo
-- again from today: Michelle's grant to Sophie for all three categories (restored if it was revoked or has expired),
-- her accepted "Home loan options" request with a confirmed video meeting at 15:00 on the first free Thursday of a week
-- in which she has no other meeting, and its meeting.scheduled event; Mei Ling's open fixed deposit request for the
-- first free Monday and Tuesday at 16:30. Other meetings and records stay. Safe to run more than once.
create function rm.reset_meeting_demo(p_customers bigint[] default array[1002,1007]::bigint[]) returns jsonb
language plpgsql volatile set search_path='' as $$
declare
  sophie uuid:='00000000-0000-4000-8000-100200000002'; today date:=rm.today(); at timestamptz; mon timestamptz; a rm.appointments;
  i int; meetings uuid[]; removed int; michelle timestamptz; mei_ling timestamptz[];
begin
  if p_customers is null or cardinality(p_customers)=0 or not p_customers<@array[1002,1007]::bigint[] then
    raise exception 'only the demo clients 1002 and 1007' using errcode='22023';
  end if;
  perform pg_advisory_xact_lock(hashtext('rm.appointments'),hashtext(sophie::text));
  select coalesce(array_agg(appointment_id),'{}') into meetings from rm.meeting_requests where customer_id=any(p_customers) and appointment_id is not null;
  delete from rm.meeting_requests where customer_id=any(p_customers);
  get diagnostics removed=row_count;
  delete from rm.appointments where appointment_id=any(meetings);
  delete from notify.inbox where customer_id=any(p_customers) and template_key like 'meeting.%';

  if 1002=any(p_customers) then
    insert into consent.grants(grant_id,customer_id,purpose,recipient,categories,granted_at,expires_at) values
      ('00000000-0000-4000-8000-100200000041',1002,'rm_brief',sophie::text,array['transaction_insights','chat_topics','meeting_recording'],
       clock_timestamp()-interval '1 day',clock_timestamp()+interval '180 days')
    on conflict (grant_id) do update set categories=excluded.categories,granted_at=excluded.granted_at,expires_at=excluded.expires_at,
      revoked_at=null,revocation_command_id=null;
    for i in 0..7 loop
      at:=((today+((4-extract(isodow from today)::int+6)%7)+1+7*i)+time '15:00') at time zone 'Asia/Singapore';
      if rm.slot_problem(sophie,1002,at) is null and rm.client_week_meetings(1002,sophie,at)=0 then michelle:=at; exit; end if;
    end loop;
    if michelle is null then raise exception 'no free Thursday 15:00 for Michelle in the next 8 weeks' using errcode='P0001'; end if;
    insert into rm.appointments(customer_id,advisor_id,starts_at,ends_at,format,status,joining_details,purpose,topics)
      values(1002,sophie,michelle,michelle+interval '30 minutes','video','confirmed',
        (select o.joining_details from rm.meeting_options o where o.advisor_id=sophie and o.format='video'),'Home loan options',
        array['home_loan','term_sheet','compliance_review']) returning * into a;
    insert into rm.meeting_requests(request_id,customer_id,advisor_id,purpose,topics,format,preferred_times,status,respond_by,appointment_id,
        decision_id,decided_at,created_at,updated_at)
      values('00000000-0000-4000-8000-100200000071',1002,sophie,'Home loan options',array['home_loan','term_sheet','compliance_review'],'video',
        array[michelle,michelle+interval '1 day'],'scheduled',rm.next_business_day(clock_timestamp()-interval '1 day'),a.appointment_id,
        '00000000-0000-4000-8000-100200000072',clock_timestamp()-interval '20 hours',clock_timestamp()-interval '1 day',clock_timestamp()-interval '20 hours');
    perform meetings.enqueue('meeting.scheduled',a.appointment_id,rm.meeting_payload(a,'00000000-0000-4000-8000-100200000071'::uuid,'rm'),
      'decision:00000000-0000-4000-8000-100200000072');
  end if;

  if 1007=any(p_customers) and exists(select 1 from rm.assignments where customer_id=1007 and advisor_id=sophie and ended_on is null) then
    for i in 0..7 loop
      mon:=((today+((1-extract(isodow from today)::int+6)%7)+1+7*i)+time '16:30') at time zone 'Asia/Singapore';
      if rm.slot_problem(sophie,1007,mon) is null and rm.slot_problem(sophie,1007,mon+interval '1 day') is null then
        mei_ling:=array[mon,mon+interval '1 day']; exit;
      end if;
    end loop;
    if mei_ling is null then raise exception 'no free Monday and Tuesday 16:30 for Mei Ling in the next 8 weeks' using errcode='P0001'; end if;
    insert into rm.meeting_requests(request_id,customer_id,advisor_id,purpose,topics,format,preferred_times,respond_by)
      values('00000000-0000-4000-8000-100700000071',1007,sophie,'Renewing my fixed deposit before it matures in December',array['fixed_deposit'],'video',
        mei_ling,rm.next_business_day(clock_timestamp()));
  end if;
  return jsonb_build_object('removed_requests',removed,'removed_meetings',cardinality(meetings),'michelle_meeting',michelle,'mei_ling_times',to_jsonb(mei_ling));
end $$;

-- 4. Grants -----------------------------------------------------------------------------------------------------------
revoke all on function public.rm_transaction_insights(bigint,text,int) from public,anon;
grant execute on function public.rm_transaction_insights(bigint,text,int) to authenticated;
revoke all on function rm.reset_meeting_demo(bigint[]) from public,anon,authenticated;
