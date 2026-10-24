-- Bind preliminary checks to the current owned notice and checklist instructions.
-- Existing reports/receipts remain historical; old template verdicts cannot authorize new submissions.
create function compliance.notice_text(p_case_id uuid,p_item_id uuid) returns text
language sql stable security invoker set search_path='' as $$
 select c.notice_text||E'\n'||i.label||E'\n'||coalesce(i.instructions,'')
 from compliance.cases c join compliance.requested_items i on i.case_id=c.case_id and i.customer_id=c.customer_id
 where c.case_id=p_case_id and i.item_id=p_item_id and c.customer_id=banking.my_customer_id()
$$;
create function compliance.notice_digest(p_case_id uuid,p_item_id uuid) returns text
language sql stable security invoker set search_path='' as $$
 select encode(sha256(convert_to(compliance.notice_text(p_case_id,p_item_id),'UTF8')),'hex')
$$;
revoke all on function compliance.notice_text(uuid,uuid),compliance.notice_digest(uuid,uuid) from public,anon;
grant execute on function compliance.notice_text(uuid,uuid),compliance.notice_digest(uuid,uuid) to authenticated;
alter table compliance.prechecks add column requirements_hash text not null default '';
alter table compliance.prechecks drop constraint prechecks_daily_unique;
create unique index prechecks_notice_daily_unique on compliance.prechecks(document_id,rule_id,rule_version,checked_on,requirements_hash) where status<>'unable_to_assess';
create or replace function compliance.precheck_assessment_date() returns trigger
language plpgsql set search_path='' as $$ begin
 new.checked_on:=((new.report->>'checked_at')::timestamptz at time zone 'Asia/Singapore')::date;
 new.requirements_hash:=coalesce(new.report->>'requirements_hash','');
 return new;
end $$;

create or replace function public.customer_precheck_context(p_customer_id bigint,p_document_id uuid)
returns jsonb language plpgsql volatile security invoker set search_path='' as $$
declare cid bigint:=banking.require_read_customer(p_customer_id); data jsonb;
begin
 select jsonb_build_object('document_id',d.document_id,'case_id',d.case_id,'item_id',d.item_id,
  'current_document_id',i.current_document_id,'notice',compliance.notice_text(d.case_id,d.item_id),'requirements_hash',compliance.notice_digest(d.case_id,d.item_id),'rule',case when r.rule_id is null then null else to_jsonb(r) end) into data
 from compliance.documents d join compliance.requested_items i on i.item_id=d.item_id and i.case_id=d.case_id and i.customer_id=cid
 left join compliance.precheck_rules r on r.rule_id=i.precheck_rule_id
 where d.document_id=p_document_id and d.customer_id=cid;
 return banking.customer_result(cid,'documents.precheck','compliance.documents',data);
end $$;

create or replace function public.customer_precheck_read(p_customer_id bigint,p_document_id uuid)
returns jsonb language plpgsql volatile security invoker set search_path='' as $$
declare cid bigint:=banking.require_read_customer(p_customer_id); data jsonb;
begin
 select p.report into data from compliance.prechecks p join compliance.requested_items i
 on i.item_id=p.item_id and i.customer_id=cid and i.precheck_rule_id=p.rule_id
 where p.customer_id=cid and p.document_id=p_document_id and p.report->>'scope'='notice_based' and p.requirements_hash=compliance.notice_digest(p.case_id,p.item_id) order by p.created_at desc limit 1;
 return banking.customer_result(cid,'documents.precheck','compliance.prechecks',data);
end $$;

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
  or (payload->>'rule_version')::int is distinct from rule.version or payload->>'scope' is distinct from 'notice_based'
  or payload->>'requirements_hash' is distinct from compliance.notice_digest(document.case_id,item.item_id)
  or ((payload->>'checked_at')::timestamptz at time zone 'Asia/Singapore')::date<>(clock_timestamp() at time zone 'Asia/Singapore')::date
  or (payload->>'checked_at')::timestamptz>clock_timestamp()+interval '1 minute'
  or (payload->>'checked_at')::timestamptz<clock_timestamp()-interval '10 minutes' then
  raise exception 'precheck source or rule changed' using errcode='22023';
 end if;
 select * into previous from compliance.prechecks where document_id=document.document_id and rule_id=rule.rule_id and rule_version=rule.version
  and checked_on=((payload->>'checked_at')::timestamptz at time zone 'Asia/Singapore')::date
  and requirements_hash=payload->>'requirements_hash' and status<>'unable_to_assess';
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
      where customer_id=cid and report->>'scope'='notice_based' and requirements_hash=compliance.notice_digest(case_id,item_id) order by document_id,checked_on desc,created_at desc) p
    join compliance.requested_items i on i.current_document_id=p.document_id and i.customer_id=cid and i.precheck_rule_id=p.rule_id
    where p.customer_id=cid and p.case_id::text=review->>'case_id'),'[]'));
 return banking.customer_result(cid,'compliance.workspace','compliance.cases',data);
end $$;

-- Check notice provenance again under the same locks as the final submission.
alter function actions.execute_document_submit(actions.commands) rename to execute_document_submit_before_notice;
revoke all on function actions.execute_document_submit_before_notice(actions.commands) from public,anon,authenticated;
create function actions.execute_document_submit(p_command actions.commands) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
begin
 perform 1 from compliance.cases where case_id=(p_command.target->>'case_id')::uuid and customer_id=p_command.customer_id for share;
 perform 1 from compliance.requested_items where item_id=(p_command.target->>'item_id')::uuid
  and case_id=(p_command.target->>'case_id')::uuid and customer_id=p_command.customer_id for update;
 if not exists(select 1 from compliance.prechecks p where p.check_id=(p_command.target->>'check_id')::uuid
  and p.customer_id=p_command.customer_id and p.report->>'scope'='notice_based'
  and p.requirements_hash=compliance.notice_digest(p.case_id,p.item_id)) then
  raise exception 'notice requirements changed; check this upload again' using errcode='P1001';
 end if;
 return actions.execute_document_submit_before_notice(p_command);
end $$;
revoke all on function actions.execute_document_submit(actions.commands) from public,anon,authenticated;
update actions.executors set executor='actions.execute_document_submit(actions.commands)'::regprocedure where type='document.submit';
