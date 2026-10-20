-- Storage owns blob writes. Business registration uses the existing atomic Action Service.
-- Only the new bucket is affected; restrictive policies prevent other permissive policies
-- from widening its scope. No customer overwrite or delete operation is provided.
do $$ begin
  if not exists(select 1 from pg_attribute where attrelid='storage.objects'::regclass and attname='owner_id' and not attisdropped)
      or not (select relrowsecurity from pg_class where oid='storage.objects'::regclass) then
    raise exception 'private document uploads require the platform Storage owner_id column and enabled object RLS';
  end if;
end $$;
insert into storage.buckets(id,name,public,file_size_limit,allowed_mime_types) values
  ('demobank-compliance-private','demobank-compliance-private',false,10485760,
    array['application/pdf','image/jpeg','image/png','application/vnd.openxmlformats-officedocument.wordprocessingml.document']);

alter table compliance.documents drop constraint documents_mime_type_check;
alter table compliance.documents add constraint documents_mime_type_check check(mime_type in
  ('application/pdf','image/jpeg','image/png','application/vnd.openxmlformats-officedocument.wordprocessingml.document'));
alter table compliance.documents add column storage_object_id uuid unique;
alter table compliance.documents add column command_id uuid unique references actions.commands(command_id);

create function compliance.owns_document_object(p_name text,p_owner_id text)
returns boolean language plpgsql stable security invoker set search_path=''
as $$
declare cid bigint:=banking.my_customer_id(); parts text[]:=string_to_array(p_name,'/');
begin
  if cid is null or auth.uid() is null or p_owner_id is distinct from auth.uid()::text
      or array_length(parts,1) is distinct from 3 or parts[1] is distinct from cid::text
      or parts[2] !~ '^[0-9a-f]{8}-([0-9a-f]{4}-){3}[0-9a-f]{12}$'
      or parts[3] !~ '^[0-9a-f]{8}-([0-9a-f]{4}-){3}[0-9a-f]{12}$' then return false; end if;
  return exists(select 1 from compliance.cases where customer_id=cid and case_id::text=parts[2]);
end $$;
create function compliance.can_upload_document_object(p_name text,p_owner_id text)
returns boolean language sql stable security invoker set search_path=''
as $$ select compliance.owns_document_object(p_name,p_owner_id)
  and exists(select 1 from compliance.cases where customer_id=banking.my_customer_id()
    and case_id::text=split_part(p_name,'/',2) and status not in ('completed','closed'))
  and not exists(select 1 from compliance.documents where customer_id=banking.my_customer_id() and storage_path=p_name) $$;
revoke all on function compliance.owns_document_object(text,text),compliance.can_upload_document_object(text,text) from public,anon;
grant execute on function compliance.owns_document_object(text,text),compliance.can_upload_document_object(text,text) to authenticated;

create policy demobank_document_read on storage.objects for select to authenticated
  using(bucket_id='demobank-compliance-private' and compliance.owns_document_object(name,owner_id));
create policy demobank_document_read_scope on storage.objects as restrictive for select to authenticated
  using(bucket_id<>'demobank-compliance-private' or compliance.owns_document_object(name,owner_id));
create policy demobank_document_insert on storage.objects for insert to authenticated
  with check(bucket_id='demobank-compliance-private' and compliance.can_upload_document_object(name,owner_id));
create policy demobank_document_insert_scope on storage.objects as restrictive for insert to authenticated
  with check(bucket_id<>'demobank-compliance-private' or compliance.can_upload_document_object(name,owner_id));
create policy demobank_document_no_update on storage.objects as restrictive for update to authenticated
  using(bucket_id<>'demobank-compliance-private') with check(bucket_id<>'demobank-compliance-private');
create policy demobank_document_no_delete on storage.objects as restrictive for delete to authenticated
  using(bucket_id<>'demobank-compliance-private');
create policy demobank_document_no_anon on storage.objects as restrictive for all to anon
  using(bucket_id<>'demobank-compliance-private') with check(bucket_id<>'demobank-compliance-private');

create function public.customer_document_upload_context(p_customer_id bigint,p_case_id uuid,p_item_id uuid)
returns jsonb language plpgsql volatile security invoker set search_path=''
as $$
declare cid bigint:=banking.require_read_customer(p_customer_id); review compliance.cases; item compliance.requested_items;
begin
  select * into review from compliance.cases where customer_id=cid and case_id=p_case_id and status not in ('completed','closed');
  if review.case_id is null then raise exception 'upload requires an owned open review case' using errcode='42501'; end if;
  if p_item_id is not null then
    select * into item from compliance.requested_items where customer_id=cid and case_id=p_case_id and item_id=p_item_id;
    if item.item_id is null then raise exception 'upload item must belong to the same owned review case' using errcode='42501'; end if;
  end if;
  return banking.customer_result(cid,'documents.prepare_upload','compliance.cases',jsonb_build_object(
    'case_id',review.case_id,'case_title',review.title,'item_id',item.item_id,'item_label',item.label));
end $$;

create function public.customer_document_location(p_customer_id bigint,p_document_id uuid)
returns jsonb language plpgsql volatile security invoker set search_path=''
as $$
declare cid bigint:=banking.require_read_customer(p_customer_id); data jsonb;
begin
  select jsonb_build_object('document_id',d.document_id,'case_id',d.case_id,'file_name',d.file_name,
    'storage_path',d.storage_path,'bucket','demobank-compliance-private') into data
  from compliance.documents d join compliance.cases c on c.case_id=d.case_id and c.customer_id=cid
    join storage.objects o on o.id=d.storage_object_id and o.bucket_id='demobank-compliance-private' and o.name=d.storage_path
      and o.owner_id=auth.uid()::text and o.created_at=d.uploaded_at
  where d.customer_id=cid and d.document_id=p_document_id
    and d.storage_path=cid::text||'/'||d.case_id::text||'/'||d.document_id::text;
  return banking.customer_result(cid,'documents.download','compliance.documents',data);
end $$;
revoke all on function public.customer_document_upload_context(bigint,uuid,uuid),public.customer_document_location(bigint,uuid) from public,anon;
grant execute on function public.customer_document_upload_context(bigint,uuid,uuid),public.customer_document_location(bigint,uuid) to authenticated;

create function actions.normalise_document_upload(p_target jsonb)
returns jsonb language plpgsql immutable set search_path=''
as $$
declare mime text:=p_target->>'mime_type'; file_name text:=p_target->>'file_name'; size text:=p_target->>'size_bytes';
begin
  if jsonb_typeof(p_target->'document_id') is distinct from 'string' or p_target->>'document_id' !~* '^[0-9a-f]{8}-([0-9a-f]{4}-){3}[0-9a-f]{12}$'
      or jsonb_typeof(p_target->'case_id') is distinct from 'string' or p_target->>'case_id' !~* '^[0-9a-f]{8}-([0-9a-f]{4}-){3}[0-9a-f]{12}$'
      or not(p_target?'item_id') or (p_target->'item_id'<>'null'::jsonb and
        (jsonb_typeof(p_target->'item_id') is distinct from 'string' or p_target->>'item_id' !~* '^[0-9a-f]{8}-([0-9a-f]{4}-){3}[0-9a-f]{12}$'))
      or jsonb_typeof(p_target->'file_name') is distinct from 'string' or length(file_name) not between 1 and 200
      or file_name<>btrim(file_name) or file_name~'[/\\[:cntrl:]]'
      or jsonb_typeof(p_target->'size_bytes') is distinct from 'number' or size !~ '^[1-9][0-9]{0,7}$'
      or jsonb_typeof(p_target->'mime_type') is distinct from 'string' then
    raise exception 'document registration requires case/document UUIDs, an optional item UUID, a safe filename, MIME type and integer byte count' using errcode='22023';
  end if;
  if size::int>10485760 or not coalesce(case mime
      when 'application/pdf' then length(file_name)>4 and lower(file_name) like '%.pdf'
      when 'image/jpeg' then (length(file_name)>4 and lower(file_name) like '%.jpg') or (length(file_name)>5 and lower(file_name) like '%.jpeg')
      when 'image/png' then length(file_name)>4 and lower(file_name) like '%.png'
      when 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' then length(file_name)>5 and lower(file_name) like '%.docx'
      else false end,false) then
    raise exception 'document type and extension must match PDF, DOCX, JPEG or PNG with at most 10485760 bytes' using errcode='22023';
  end if;
  return jsonb_build_object('document_id',(p_target->>'document_id')::uuid,'case_id',(p_target->>'case_id')::uuid,
    'item_id',(p_target->>'item_id')::uuid,'file_name',file_name,'mime_type',mime,'size_bytes',size::int);
end $$;

create function actions.document_upload_target(p_target jsonb,p_customer_id bigint)
returns jsonb language plpgsql volatile security definer set search_path='' set timezone='UTC'
as $$
declare target jsonb:=actions.normalise_document_upload(p_target); review compliance.cases; item compliance.requested_items;
  object storage.objects; auth_owner uuid; path text:=p_customer_id::text||'/'||(target->>'case_id')||'/'||(target->>'document_id');
begin
  select auth_user_id into auth_owner from banking.customer_profiles where customer_id=p_customer_id;
  select * into review from compliance.cases where case_id=(target->>'case_id')::uuid and customer_id=p_customer_id;
  if target->>'item_id' is not null then
    select * into item from compliance.requested_items where item_id=(target->>'item_id')::uuid
      and case_id=review.case_id and customer_id=p_customer_id;
  end if;
  select * into object from storage.objects o where o.bucket_id='demobank-compliance-private' and o.name=path and o.owner_id=auth_owner::text;
  if review.case_id is null or auth_owner is null or object.id is null or (target->>'item_id' is not null and item.item_id is null) then
    raise exception 'document registration requires an existing owned Storage object, review case and matching optional item' using errcode='42501';
  end if;
  if object.created_at is null or not isfinite(object.created_at)
      or length(review.title) not between 1 and 2000 or review.title !~ '[^[:space:]]'
      or (item.item_id is not null and (length(item.label) not between 1 and 2000 or item.label !~ '[^[:space:]]'))
      or jsonb_typeof(object.metadata->'size') is distinct from 'number' or object.metadata->>'size' is distinct from target->>'size_bytes'
      or object.metadata->>'mimetype' is distinct from target->>'mime_type' then
    raise exception 'uploaded object byte count or MIME type differs from the selected file' using errcode='22023';
  end if;
  return target||jsonb_build_object('case_title',review.title,'item_label',item.label,'object_id',object.id,
    'storage_path',path,'uploaded_at',object.created_at);
end $$;

create function actions.execute_document_upload(p_command actions.commands)
returns jsonb language plpgsql volatile security definer set search_path=''
as $$
declare review compliance.cases; object storage.objects;
begin
  select * into review from compliance.cases where case_id=(p_command.target->>'case_id')::uuid
    and customer_id=p_command.customer_id for share;
  select * into object from storage.objects where id=(p_command.target->>'object_id')::uuid
    and bucket_id='demobank-compliance-private' and name=p_command.target->>'storage_path' for share;
  if p_command.target->>'item_id' is not null then
    perform 1 from compliance.requested_items where customer_id=p_command.customer_id and case_id=review.case_id
      and item_id=(p_command.target->>'item_id')::uuid for share;
    if not found then raise exception 'upload checklist item changed' using errcode='P1001'; end if;
  end if;
  if review.case_id is null or review.status in ('completed','closed') or object.id is null
      or actions.document_upload_target(p_command.target,p_command.customer_id)<>p_command.target
      or exists(select 1 from compliance.documents where document_id=(p_command.target->>'document_id')::uuid) then
    raise exception 'upload case, object or registration changed; reload records before retrying' using errcode='P1001';
  end if;
  insert into compliance.documents(document_id,case_id,item_id,customer_id,storage_path,file_name,mime_type,size_bytes,uploaded_at,storage_object_id,command_id)
    values((p_command.target->>'document_id')::uuid,review.case_id,(p_command.target->>'item_id')::uuid,p_command.customer_id,
      p_command.target->>'storage_path',p_command.target->>'file_name',p_command.target->>'mime_type',
      (p_command.target->>'size_bytes')::int,object.created_at,object.id,p_command.command_id);
  perform audit.record('document','document.uploaded','success','compliance.documents',p_command.target->>'document_id',
    jsonb_build_object('object_id',object.id),p_command.case_id);
  return jsonb_build_object('result',p_command.target||jsonb_build_object('status','uploaded'),'event_type','document.uploaded');
end $$;
insert into actions.executors(type,validator,executor,confirmation_required,normaliser,event_types) values
  ('document.register','actions.document_upload_target(jsonb,bigint)'::regprocedure,'actions.execute_document_upload(actions.commands)'::regprocedure,
    false,'actions.normalise_document_upload(jsonb)'::regprocedure,array['document.uploaded']);
revoke all on function actions.normalise_document_upload(jsonb),actions.document_upload_target(jsonb,bigint),actions.execute_document_upload(actions.commands)
  from public,anon,authenticated;
