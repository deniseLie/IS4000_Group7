-- Extend the existing owned-location snapshot with metadata needed to verify downloaded bytes.
-- Existing invoker semantics and execute grants are preserved; no new privileges are granted.
create or replace function public.customer_document_location(p_customer_id bigint,p_document_id uuid)
returns jsonb language plpgsql volatile security invoker set search_path=''
as $$
declare cid bigint:=banking.require_read_customer(p_customer_id); data jsonb;
begin
  select jsonb_build_object('document_id',d.document_id,'case_id',d.case_id,'file_name',d.file_name,
    'storage_path',d.storage_path,'bucket','demobank-compliance-private','object_id',o.id,
    'mime_type',d.mime_type,'size_bytes',d.size_bytes,'uploaded_at',d.uploaded_at) into data
  from compliance.documents d join compliance.cases c on c.case_id=d.case_id and c.customer_id=cid
    join storage.objects o on o.id=d.storage_object_id and o.bucket_id='demobank-compliance-private' and o.name=d.storage_path
      and o.owner_id=auth.uid()::text and o.created_at=d.uploaded_at
      and o.metadata->>'size'=d.size_bytes::text and o.metadata->>'mimetype'=d.mime_type
  where d.customer_id=cid and d.document_id=p_document_id
    and d.storage_path=cid::text||'/'||d.case_id::text||'/'||d.document_id::text;
  return banking.customer_result(cid,'documents.download','compliance.documents',data);
end $$;
