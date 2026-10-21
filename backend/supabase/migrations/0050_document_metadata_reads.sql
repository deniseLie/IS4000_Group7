-- Metadata only: no object paths, download credentials, file contents or free-form precheck notes.
create function public.customer_read_documents(p_customer_id bigint)
returns jsonb language plpgsql volatile security invoker set search_path=''
as $$
declare cid bigint:=banking.require_read_customer(p_customer_id); data jsonb;
begin
  if exists(select 1 from compliance.documents d where d.customer_id=cid and (
    not exists(select 1 from compliance.cases c where c.customer_id=cid and c.case_id=d.case_id) or
    (d.item_id is not null and not exists(select 1 from compliance.requested_items i
      where i.customer_id=cid and i.case_id=d.case_id and i.item_id=d.item_id)))) then
    raise exception 'a document references an unavailable owned case or checklist item' using errcode='23514';
  end if;
  select coalesce(jsonb_agg(to_jsonb(r) order by r.uploaded_at desc,r.document_id),'[]') into data from(
    select d.document_id,d.case_id,c.title case_title,d.item_id,i.label item_label,d.file_name,d.mime_type,
      d.size_bytes,d.precheck_status,d.uploaded_at
    from compliance.documents d join compliance.cases c on c.customer_id=cid and c.case_id=d.case_id
      left join compliance.requested_items i on i.customer_id=cid and i.case_id=d.case_id and i.item_id=d.item_id
    where d.customer_id=cid order by d.uploaded_at desc,d.document_id limit 200)r;
  return banking.customer_result(cid,'documents.list','compliance.documents',data);
end $$;
revoke all on function public.customer_read_documents(bigint) from public,anon;
grant execute on function public.customer_read_documents(bigint) to authenticated;
