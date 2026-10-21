create function public.notification_list_customer(p_customer_id bigint)
returns jsonb language plpgsql volatile security invoker set search_path=''
as $$
declare cid bigint:=banking.my_customer_id(); result jsonb;
begin
  if auth.uid() is null or cid is null or cid is distinct from p_customer_id then
    raise exception 'notification scope must match the signed-in customer' using errcode='42501';
  end if;
  select coalesce(jsonb_agg(to_jsonb(n) order by n.created_at desc,n.notification_id desc),'[]') into result
    from(select notification_id,customer_id,event_id,template_key,template_version,locale,priority,title,body,is_service,read_at,created_at
      from notify.inbox where customer_id=cid order by created_at desc,notification_id desc limit 50)n;
  perform public.audit_record_batch(jsonb_build_array(jsonb_build_object('category','data','action','data.read','outcome','success',
    'resource_type','notify.inbox','details',jsonb_build_object('tool','notifications.list','result_count',jsonb_array_length(result)))));
  return result;
end $$;

create function public.notification_mark_read(p_notification_id uuid,p_customer_id bigint)
returns jsonb language plpgsql volatile security definer set search_path=''
as $$
declare cid bigint:=banking.my_customer_id(); item notify.inbox;
begin
  if auth.uid() is null or cid is null or cid is distinct from p_customer_id then
    raise exception 'notification scope must match the signed-in customer' using errcode='42501';
  end if;
  select * into item from notify.inbox where notification_id=p_notification_id and customer_id=cid for update;
  if not found then raise exception 'notification was not found in this customer scope' using errcode='P0002'; end if;
  if item.read_at is null then
    update notify.inbox set read_at=clock_timestamp() where notification_id=p_notification_id and customer_id=cid returning * into item;
    perform audit.record('notification','notification.read','success','notify.inbox',item.notification_id::text,'{}',p_customer_id=>cid);
  end if;
  return to_jsonb(item)-'dedup_key';
end $$;
revoke all on function public.notification_list_customer(bigint),public.notification_mark_read(uuid,bigint) from public,anon;
grant execute on function public.notification_list_customer(bigint),public.notification_mark_read(uuid,bigint) to authenticated;

-- The hosted Realtime publication exists on Supabase. Local PostgreSQL tests do not claim Realtime coverage.
do $$ begin
  if exists(select 1 from pg_publication where pubname='supabase_realtime') then
    if not exists(select 1 from pg_publication_tables where pubname='supabase_realtime' and schemaname='notify' and tablename='inbox') then
      alter publication supabase_realtime add table notify.inbox;
    end if;
  else raise notice 'supabase_realtime publication is absent; hosted Realtime remains unconfigured on this database'; end if;
end $$;
