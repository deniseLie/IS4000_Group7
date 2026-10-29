-- Reminders disappear from the Inbox once she has done what they asked (CR7). New reminders were already limited to
-- outstanding items (0060), and a queued one that went stale was already dropped at delivery; this clears the ones she
-- has already received:
--   - a deadline reminder, once no requested item on that review is still to send (none not submitted, rejected or unknown);
--   - a "the bank asked you" reminder, once that request is no longer open (for a correction: when she sends a new file, 0076).
-- Cleared reminders are hidden from the Inbox list, not deleted, so the audit trail and delivery records stay complete.

alter table notify.inbox add column cleared_at timestamptz;

create function notify.clear_answered_reminders(p_customer_id bigint,p_case_id uuid)
returns int language plpgsql volatile security definer set search_path='' as $$
declare cleared int;
begin
  update notify.inbox i set cleared_at=clock_timestamp(),read_at=coalesce(i.read_at,clock_timestamp())
  from events.outbox o
  where i.event_id=o.event_id and i.customer_id=p_customer_id and o.customer_id=p_customer_id and i.cleared_at is null
    and o.payload->>'case_id'=p_case_id::text
    and ((o.event_type='compliance.deadline_reminder'
          and not exists(select 1 from compliance.requested_items r where r.customer_id=p_customer_id and r.case_id=p_case_id
            and r.submission_status in ('not_submitted','rejected','unknown')))
      or (o.event_type='compliance.information_reminder'
          and not exists(select 1 from compliance.info_requests q where q.customer_id=p_customer_id and q.case_id=p_case_id
            and q.request_id::text=o.payload->>'request_id' and q.status='open')));
  get diagnostics cleared=row_count;
  if cleared>0 then
    perform audit.record('notification','notification.reminders_cleared','success','notify.inbox',null,
      jsonb_build_object('case_id',p_case_id,'cleared_count',cleared),p_actor_type=>'system',p_actor_id=>'notification_worker',p_customer_id=>p_customer_id);
  end if;
  return cleared;
end $$;
revoke all on function notify.clear_answered_reminders(bigint,uuid) from public,anon,authenticated;

create function notify.clear_reminders_after_item() returns trigger language plpgsql security definer set search_path='' as $$
begin perform notify.clear_answered_reminders(new.customer_id,new.case_id); return null; end $$;
revoke all on function notify.clear_reminders_after_item() from public,anon,authenticated;
create trigger clear_reminders_after_item after update of submission_status on compliance.requested_items
  for each row when (new.submission_status in ('submitted_awaiting_review','accepted') and old.submission_status is distinct from new.submission_status)
  execute function notify.clear_reminders_after_item();

create function notify.clear_reminders_after_request() returns trigger language plpgsql security definer set search_path='' as $$
begin perform notify.clear_answered_reminders(new.customer_id,new.case_id); return null; end $$;
revoke all on function notify.clear_reminders_after_request() from public,anon,authenticated;
create trigger clear_reminders_after_request after update of status on compliance.info_requests
  for each row when (old.status='open' and new.status<>'open')
  execute function notify.clear_reminders_after_request();

-- Same list as 0024, without cleared reminders.
create or replace function public.notification_list_customer(p_customer_id bigint)
returns jsonb language plpgsql volatile security invoker set search_path=''
as $$
declare cid bigint:=banking.my_customer_id(); result jsonb;
begin
  if auth.uid() is null or cid is null or cid is distinct from p_customer_id then
    raise exception 'notification scope must match the signed-in customer' using errcode='42501';
  end if;
  select coalesce(jsonb_agg(to_jsonb(n) order by n.created_at desc,n.notification_id desc),'[]') into result
    from(select notification_id,customer_id,event_id,template_key,template_version,locale,priority,title,body,is_service,read_at,created_at
      from notify.inbox where customer_id=cid and cleared_at is null order by created_at desc,notification_id desc limit 50)n;
  perform public.audit_record_batch(jsonb_build_array(jsonb_build_object('category','data','action','data.read','outcome','success',
    'resource_type','notify.inbox','details',jsonb_build_object('tool','notifications.list','result_count',jsonb_array_length(result)))));
  return result;
end $$;
