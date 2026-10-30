-- Existing DemoBank project: apply only after reviewing hosted schema and RLS.
begin;

set local lock_timeout='10s';
set local statement_timeout='120s';

select pg_advisory_xact_lock(hashtext('demobank.hosted_migrations'));

do $apply_0085$
declare previous supabase_migrations.schema_migrations; script text:=$source_0085$-- Released source SHA256: 933cb7d8db7940dcc9389dec2ac87882240c544f2365edcc07b32b258b388995
-- A different question for the relationship manager in the SAME chat opens its own ticket (code review follow-up).
-- One open support handoff per chat and reason (0003) meant a second, unrelated RM question in a chat was folded into
-- the first ticket. The assistant now decides the topic by rules: the same topic is added to the open ticket as before
-- (customer_ticket_follow_up), and a different topic opens a new rm_handover ticket here, linked to the same chat.
-- Retries are safe: an open ticket with the same summary in the same chat is returned instead of a second one.
create function public.customer_open_rm_ticket(p_customer_id bigint,p_case_id uuid,p_summary text)
returns jsonb language plpgsql volatile security definer set search_path='' as $$
declare cid bigint:=banking.require_read_customer(p_customer_id); ticket support.tickets; created boolean:=false;
begin
  if p_case_id is null or p_summary is null or length(btrim(p_summary)) not between 1 and 500 then
    raise exception 'an RM ticket needs this chat and a summary of at most 500 characters' using errcode='22023';
  end if;
  perform conversation.require_redacted(p_summary);
  if not exists(select 1 from conversation.cases c where c.case_id=p_case_id and c.customer_id=cid) then
    raise exception 'this chat does not belong to the signed-in client' using errcode='42501';
  end if;
  select * into ticket from support.tickets where customer_id=cid and case_id=p_case_id and category='rm_handover'
    and summary=p_summary and status in ('submitted','in_review','waiting_on_client') order by created_at desc limit 1;
  if not found then
    -- Source 'handoff' like every other RM ticket; 'form'/'chat' tickets are limited to one open per category (0071).
    ticket:=support.open_ticket(cid,p_case_id,'rm_handover',p_summary,'{}','normal','handoff');
    created:=true;
  end if;
  return jsonb_build_object('reference',ticket.reference,'created',created);
end $$;
revoke all on function public.customer_open_rm_ticket(bigint,uuid,text) from public,anon;
grant execute on function public.customer_open_rm_ticket(bigint,uuid,text) to authenticated;

-- Approved how-to for sending money overseas (CR2): without it the assistant could only say it had no information.
insert into kb.articles(article_id,version,title,categories,publisher,scope,kind,status,reference_url,checked_on,review_due) values
  ('overseas-transfers',1,'Sending money overseas',array['retail'],'DemoBank coursework project','Fictional demo guidance; not a live bank policy.','demo','published',null,null,null)
on conflict do nothing;
insert into kb.sections(article_id,version,anchor,heading,body,keywords) values
  ('overseas-transfers',1,'main','Sending money overseas','In this demo, you send money overseas from the official DemoBank app or website: choose Pay and transfer, then Overseas transfer, add the recipient (name, bank, SWIFT/BIC code and account number or IBAN, and their address), choose the currency and amount, and check the fees and exchange rate shown before you confirm. Overseas (telegraphic) transfers usually arrive within one to three business days, depending on the countries and banks involved. Send only to people you know, and never because someone in a call or message tells you to. Large transfers, transfers to a trust or company, or moving funds between countries can be discussed with your relationship manager. The assistant cannot send or confirm a transfer. These are synthetic demo terms, not a live bank service standard.','overseas transfer international transfer send money abroad telegraphic swift wire remittance foreign')
on conflict do nothing;
$source_0085$;
begin
  select * into previous from supabase_migrations.schema_migrations where version='0085';
  if found then
    if previous.name is distinct from 'separate_rm_requests_in_one_chat' or previous.statements is distinct from array[script] then
      raise exception 'migration 0085 is recorded with different SQL; inspect history before proceeding';
    end if;
    raise notice 'migration 0085 already applied with identical SQL';
  else
    execute script;
    insert into supabase_migrations.schema_migrations(version,name,statements)
      values('0085','separate_rm_requests_in_one_chat',array[script]);
  end if;
end $apply_0085$;

commit;
select version,name from supabase_migrations.schema_migrations order by version;
