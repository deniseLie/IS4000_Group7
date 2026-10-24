-- Each report is valid only on its Singapore assessment date. Keep historical reports.
alter table compliance.prechecks drop constraint prechecks_document_id_rule_id_rule_version_key;
alter table compliance.prechecks add column checked_on date;
update compliance.prechecks set checked_on=((report->>'checked_at')::timestamptz at time zone 'Asia/Singapore')::date;
alter table compliance.prechecks alter column checked_on set not null;
alter table compliance.prechecks add constraint prechecks_daily_unique unique(document_id,rule_id,rule_version,checked_on);
create function compliance.precheck_assessment_date() returns trigger
language plpgsql set search_path='' as $$ begin
 new.checked_on:=((new.report->>'checked_at')::timestamptz at time zone 'Asia/Singapore')::date;
 return new;
end $$;
revoke all on function compliance.precheck_assessment_date() from public,anon,authenticated;
create trigger precheck_assessment_date before insert or update on compliance.prechecks
for each row execute function compliance.precheck_assessment_date();

create or replace function public.document_precheck_record(p_payload text,p_signature text)
returns jsonb language plpgsql volatile security definer set search_path='' as $$
declare cid bigint:=banking.my_customer_id(); payload jsonb; document compliance.documents; item compliance.requested_items;
 rule compliance.precheck_rules; previous compliance.prechecks; signing_secret text;
begin
 if auth.uid() is null or cid is null then raise exception 'linked customer required' using errcode='42501'; end if;
 if p_payload is null or octet_length(p_payload)>16384 or p_signature is null then raise exception 'invalid precheck envelope' using errcode='22023'; end if;
 select secret into strict signing_secret from compliance.precheck_keys where key_id='v1';
 if compliance.precheck_hmac(convert_to(p_payload,'UTF8'),convert_to(signing_secret,'UTF8'))<>p_signature then
  raise exception 'precheck signature invalid' using errcode='42501';
 end if;
 payload:=p_payload::jsonb;
 select * into document from compliance.documents where document_id=(payload->>'document_id')::uuid and customer_id=cid;
 perform 1 from compliance.cases where case_id=document.case_id and customer_id=cid and status not in ('completed','closed') for share;
 if not found then raise exception 'owned open case required' using errcode='42501'; end if;
 select * into item from compliance.requested_items where item_id=document.item_id and case_id=document.case_id and customer_id=cid for update;
 select * into rule from compliance.precheck_rules where rule_id=item.precheck_rule_id;
 if document.document_id is null or item.item_id is null or rule.rule_id is null
  or (payload->>'customer_id')::bigint is distinct from cid or payload->>'case_id' is distinct from document.case_id::text
  or payload->>'item_id' is distinct from item.item_id::text or payload->>'rule_id' is distinct from rule.rule_id
  or (payload->>'rule_version')::int is distinct from rule.version or payload->>'scope' is distinct from 'synthetic_template_only'
  or ((payload->>'checked_at')::timestamptz at time zone 'Asia/Singapore')::date<>(clock_timestamp() at time zone 'Asia/Singapore')::date
  or (payload->>'checked_at')::timestamptz>clock_timestamp()+interval '1 minute'
  or (payload->>'checked_at')::timestamptz<clock_timestamp()-interval '10 minutes' then
  raise exception 'precheck source or rule changed' using errcode='22023';
 end if;
 select * into previous from compliance.prechecks where document_id=document.document_id and rule_id=rule.rule_id and rule_version=rule.version
  and checked_on=((payload->>'checked_at')::timestamptz at time zone 'Asia/Singapore')::date;
 if found then
  if previous.sha256 is distinct from payload->>'sha256' then raise exception 'document bytes changed' using errcode='23514'; end if;
  return banking.customer_result(cid,'documents.precheck','compliance.prechecks',previous.report);
 end if;
 insert into compliance.prechecks(check_id,document_id,customer_id,case_id,item_id,rule_id,rule_version,sha256,status,report)
 values((payload->>'check_id')::uuid,document.document_id,cid,document.case_id,item.item_id,rule.rule_id,rule.version,payload->>'sha256',payload->>'status',payload);
 update compliance.documents set precheck_status=case payload->>'status' when 'no_issues' then 'passed' when 'issues_found' then 'issues_found' else 'not_checked' end
 where document_id=document.document_id and customer_id=cid;
 perform audit.record('document','document.prechecked','success','compliance.documents',document.document_id::text,
  jsonb_build_object('status',payload->>'status','rule_id',rule.rule_id,'rule_version',rule.version));
 return banking.customer_result(cid,'documents.precheck','compliance.prechecks',payload);
end $$;


create or replace function public.customer_compliance_workspace(p_customer_id bigint)
returns jsonb language plpgsql volatile security invoker set search_path='' as $$
declare cid bigint:=banking.require_read_customer(p_customer_id); review jsonb; data jsonb;
begin
 review:=public.customer_read_compliance(cid)->'data';
 data:=jsonb_build_object('review',review,'documents',public.customer_read_documents(cid)->'data',
  'rules',coalesce((select jsonb_object_agg(i.item_id::text,to_jsonb(r)) from compliance.requested_items i
    join compliance.precheck_rules r on r.rule_id=i.precheck_rule_id
    where i.customer_id=cid and i.case_id::text=review->>'case_id'),'{}'),
  'current_documents',coalesce((select jsonb_object_agg(i.item_id::text,i.current_document_id) from compliance.requested_items i
    where i.customer_id=cid and i.case_id::text=review->>'case_id' and i.current_document_id is not null),'{}'),
  'checks',coalesce((select jsonb_agg(p.report order by p.created_at desc) from (select distinct on (document_id) * from compliance.prechecks
      where customer_id=cid order by document_id,checked_on desc,created_at desc) p
    join compliance.requested_items i on i.current_document_id=p.document_id and i.customer_id=cid and i.precheck_rule_id=p.rule_id
    where p.customer_id=cid and p.case_id::text=review->>'case_id'),'[]'));
 return banking.customer_result(cid,'compliance.workspace','compliance.cases',data);
end $$;

-- The action executor is the final enforcement point, including direct Data API commands.
alter function actions.execute_document_submit(actions.commands) rename to execute_document_submit_before_freshness;
revoke all on function actions.execute_document_submit_before_freshness(actions.commands) from public,anon,authenticated;
create function actions.execute_document_submit(p_command actions.commands) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
begin
 perform 1 from compliance.cases where case_id=(p_command.target->>'case_id')::uuid and customer_id=p_command.customer_id for share;
 perform 1 from compliance.requested_items where item_id=(p_command.target->>'item_id')::uuid
  and case_id=(p_command.target->>'case_id')::uuid and customer_id=p_command.customer_id for update;
 if not exists(select 1 from compliance.prechecks p
  where p.check_id=(p_command.target->>'check_id')::uuid and p.customer_id=p_command.customer_id
   and p.checked_on=(clock_timestamp() at time zone 'Asia/Singapore')::date) then
  raise exception 'preliminary check expired; check this upload again' using errcode='P1001';
 end if;
 return actions.execute_document_submit_before_freshness(p_command);
end $$;
revoke all on function actions.execute_document_submit(actions.commands) from public,anon,authenticated;
update actions.executors set executor='actions.execute_document_submit(actions.commands)'::regprocedure where type='document.submit';
