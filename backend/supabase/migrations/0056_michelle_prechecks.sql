-- Synthetic template checks only. Owner provisions DOCUMENT_PRECHECK_SIGNING_KEY from
-- compliance.precheck_keys into the backend's private environment; app roles cannot read it.
create table compliance.precheck_keys(key_id text primary key,secret text not null check(length(secret)>=43));
insert into compliance.precheck_keys values('v1',replace(gen_random_uuid()::text||gen_random_uuid()::text,'-',''));
alter table compliance.precheck_keys enable row level security;
revoke all on compliance.precheck_keys from public,anon,authenticated;

-- Standard HMAC-SHA256 using PostgreSQL's built-in sha256; no extension namespace assumptions.
create function compliance.precheck_hmac(p_message bytea,p_key bytea) returns text
language plpgsql immutable set search_path='' as $$
declare key bytea:=p_key; inner_pad bytea:=decode(repeat('36',64),'hex'); outer_pad bytea:=decode(repeat('5c',64),'hex'); i int;
begin
 if length(key)>64 then key:=sha256(key); end if;
 key:=key||decode(repeat('00',64-length(key)),'hex');
 for i in 0..63 loop
  inner_pad:=set_byte(inner_pad,i,get_byte(inner_pad,i)#get_byte(key,i));
  outer_pad:=set_byte(outer_pad,i,get_byte(outer_pad,i)#get_byte(key,i));
 end loop;
 return encode(sha256(outer_pad||sha256(inner_pad||p_message)),'hex');
end $$;
revoke all on function compliance.precheck_hmac(bytea,bytea) from public,anon,authenticated;

create table compliance.precheck_rules(
  rule_id text primary key,version int not null check(version=1),title text not null,
  fields text[] not null,expected_pages int not null check(expected_pages between 1 and 20),
  recent_date boolean not null default false,
  scope text not null default 'Fictional English template checks only. Field entries and PDF page count; no authenticity, signature or bank approval assessment.'
);
insert into compliance.precheck_rules(rule_id,version,title,fields,expected_pages,recent_date) values
 ('proof_address',1,'Demo proof of address',array['Full name','Address','Document date'],1,true),
 ('wealth_declaration',1,'Demo wealth declaration',array['Full name','Source of wealth','Declaration date','Signature entry'],1,false),
 ('bank_statements',1,'Demo statement package',array['Full name','Statement period','Bank name'],3,false),
 ('tax_residency',1,'Demo tax residency form',array['Full name','Country of tax residence','Declaration date','Signature entry'],1,false);
alter table compliance.precheck_rules enable row level security;
revoke all on compliance.precheck_rules from public,anon,authenticated;
grant select on compliance.precheck_rules to authenticated;
create policy published_rules on compliance.precheck_rules for select to authenticated using(true);
alter table compliance.requested_items add column precheck_rule_id text references compliance.precheck_rules;
alter table compliance.requested_items add column current_document_id uuid references compliance.documents;
update compliance.requested_items set precheck_rule_id=case item_id::text
 when '00000000-0000-4000-8000-100200000011' then 'proof_address'
 when '00000000-0000-4000-8000-100200000012' then 'wealth_declaration'
 when '00000000-0000-4000-8000-100200000013' then 'bank_statements'
 when '00000000-0000-4000-8000-100200000014' then 'tax_residency' end
 where customer_id=1002 and case_id='00000000-0000-4000-8000-100200000001';

create table compliance.prechecks(
 check_id uuid primary key,document_id uuid not null references compliance.documents,
 customer_id bigint not null references banking.customer_profiles,case_id uuid not null references compliance.cases,
 item_id uuid not null references compliance.requested_items,rule_id text not null references compliance.precheck_rules,
 rule_version int not null,sha256 text not null check(sha256 ~ '^[a-f0-9]{64}$'),
 status text not null check(status in ('no_issues','issues_found','unable_to_assess')),
 report jsonb not null,created_at timestamptz not null default clock_timestamp(),unique(document_id,rule_id,rule_version)
);
alter table compliance.prechecks enable row level security;
revoke all on compliance.prechecks from public,anon,authenticated;
grant select on compliance.prechecks to authenticated;
create policy own_prechecks on compliance.prechecks for select to authenticated using(customer_id=banking.my_customer_id());

-- Each immutable replacement becomes current at registration. Accepted bank records are never reset.
create function compliance.track_document_revision() returns trigger language plpgsql security definer set search_path='' as $$
begin
 if new.item_id is not null then
  perform 1 from compliance.cases where case_id=new.case_id and customer_id=new.customer_id for share;
  update compliance.requested_items set current_document_id=new.document_id
   where item_id=new.item_id and case_id=new.case_id and customer_id=new.customer_id;
 end if;
 return new;
end $$;
revoke all on function compliance.track_document_revision() from public,anon,authenticated;
create trigger track_document_revision after insert on compliance.documents for each row execute function compliance.track_document_revision();

create function public.customer_precheck_context(p_customer_id bigint,p_document_id uuid)
returns jsonb language plpgsql volatile security invoker set search_path='' as $$
declare cid bigint:=banking.require_read_customer(p_customer_id); data jsonb;
begin
 select jsonb_build_object('document_id',d.document_id,'case_id',d.case_id,'item_id',d.item_id,
  'current_document_id',i.current_document_id,'rule',case when r.rule_id is null then null else to_jsonb(r) end) into data
 from compliance.documents d join compliance.requested_items i on i.item_id=d.item_id and i.case_id=d.case_id and i.customer_id=cid
 left join compliance.precheck_rules r on r.rule_id=i.precheck_rule_id
 where d.document_id=p_document_id and d.customer_id=cid;
 return banking.customer_result(cid,'documents.precheck','compliance.documents',data);
end $$;

create function public.customer_precheck_read(p_customer_id bigint,p_document_id uuid)
returns jsonb language plpgsql volatile security invoker set search_path='' as $$
declare cid bigint:=banking.require_read_customer(p_customer_id); data jsonb;
begin
 select p.report into data from compliance.prechecks p join compliance.requested_items i
 on i.item_id=p.item_id and i.customer_id=cid and i.precheck_rule_id=p.rule_id
 where p.customer_id=cid and p.document_id=p_document_id order by p.created_at desc limit 1;
 return banking.customer_result(cid,'documents.precheck','compliance.prechecks',data);
end $$;

create function public.document_precheck_record(p_payload text,p_signature text)
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
  or (payload->>'checked_at')::timestamptz>clock_timestamp()+interval '1 minute'
  or (payload->>'checked_at')::timestamptz<clock_timestamp()-interval '10 minutes' then
  raise exception 'precheck source or rule changed' using errcode='22023';
 end if;
 select * into previous from compliance.prechecks where document_id=document.document_id and rule_id=rule.rule_id and rule_version=rule.version;
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

create function actions.normalise_document_submit(p_target jsonb) returns jsonb language plpgsql immutable set search_path='' as $$
begin
 if jsonb_typeof(p_target->'document_id') is distinct from 'string' or jsonb_typeof(p_target->'check_id') is distinct from 'string' then
  raise exception 'document and check ids required' using errcode='22023';
 end if;
 return jsonb_build_object('document_id',(p_target->>'document_id')::uuid,'check_id',(p_target->>'check_id')::uuid);
end $$;
create function actions.document_submit_target(p_target jsonb,p_customer_id bigint) returns jsonb language plpgsql volatile security definer set search_path='' as $$
declare target jsonb:=actions.normalise_document_submit(p_target); document compliance.documents; report compliance.prechecks; item compliance.requested_items;
begin
 select * into document from compliance.documents where document_id=(target->>'document_id')::uuid and customer_id=p_customer_id;
 select * into report from compliance.prechecks where check_id=(target->>'check_id')::uuid and customer_id=p_customer_id and document_id=document.document_id;
 select * into item from compliance.requested_items where item_id=document.item_id and case_id=document.case_id and customer_id=p_customer_id;
 if document.document_id is null or report.check_id is null or item.item_id is null then raise exception 'owned document and check required' using errcode='42501'; end if;
 return target||jsonb_build_object('case_id',document.case_id,'item_id',item.item_id,'item_label',item.label,'file_name',document.file_name,'sha256',report.sha256);
end $$;
create function actions.execute_document_submit(p_command actions.commands) returns jsonb language plpgsql volatile security definer set search_path='' as $$
declare review compliance.cases; item compliance.requested_items; report compliance.prechecks; document compliance.documents;
begin
 select * into review from compliance.cases where case_id=(p_command.target->>'case_id')::uuid and customer_id=p_command.customer_id for share;
 select * into item from compliance.requested_items where item_id=(p_command.target->>'item_id')::uuid and case_id=review.case_id and customer_id=p_command.customer_id for update;
 select * into report from compliance.prechecks where check_id=(p_command.target->>'check_id')::uuid and document_id=item.current_document_id and customer_id=p_command.customer_id;
 select * into document from compliance.documents where document_id=item.current_document_id and customer_id=p_command.customer_id;
 if review.case_id is null or review.status in ('closed','completed') or item.submission_status='accepted'
  or report.check_id is null or report.status<>'no_issues' or report.rule_id is distinct from item.precheck_rule_id
  or document.document_id is distinct from (p_command.target->>'document_id')::uuid
  or not exists(select 1 from storage.objects o where o.id=document.storage_object_id and o.name=document.storage_path
    and o.bucket_id='demobank-compliance-private' and o.owner_id=auth.uid()::text and o.created_at=document.uploaded_at
    and o.metadata->>'size'=document.size_bytes::text and o.metadata->>'mimetype'=document.mime_type)
  or actions.document_submit_target(p_command.target,p_command.customer_id)<>p_command.target then
  raise exception 'document, check or bank status changed' using errcode='P1001';
 end if;
 update compliance.requested_items set submission_status='submitted_awaiting_review',updated_at=clock_timestamp()
 where item_id=item.item_id and case_id=review.case_id and customer_id=p_command.customer_id;
 perform audit.record('document','document.submitted','success','compliance.documents',document.document_id::text,
  jsonb_build_object('check_id',report.check_id,'rule_id',report.rule_id),p_command.case_id);
 return jsonb_build_object('result',p_command.target||jsonb_build_object('status','submitted_awaiting_review'),'event_type','document.submitted');
end $$;
insert into actions.executors(type,validator,executor,confirmation_required,normaliser,event_types) values
 ('document.submit','actions.document_submit_target(jsonb,bigint)'::regprocedure,'actions.execute_document_submit(actions.commands)'::regprocedure,
 false,'actions.normalise_document_submit(jsonb)'::regprocedure,array['document.submitted']);
revoke all on function actions.normalise_document_submit(jsonb),actions.document_submit_target(jsonb,bigint),actions.execute_document_submit(actions.commands) from public,anon,authenticated;
revoke all on function public.customer_precheck_context(bigint,uuid),public.customer_precheck_read(bigint,uuid),public.document_precheck_record(text,text) from public,anon;
grant execute on function public.customer_precheck_context(bigint,uuid),public.customer_precheck_read(bigint,uuid),public.document_precheck_record(text,text) to authenticated;

create function public.customer_compliance_workspace(p_customer_id bigint)
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
  'checks',coalesce((select jsonb_agg(p.report order by p.created_at desc) from compliance.prechecks p
    join compliance.requested_items i on i.current_document_id=p.document_id and i.customer_id=cid and i.precheck_rule_id=p.rule_id
    where p.customer_id=cid and p.case_id::text=review->>'case_id'),'[]'));
 return banking.customer_result(cid,'compliance.workspace','compliance.cases',data);
end $$;
revoke all on function public.customer_compliance_workspace(bigint) from public,anon;
grant execute on function public.customer_compliance_workspace(bigint) to authenticated;
