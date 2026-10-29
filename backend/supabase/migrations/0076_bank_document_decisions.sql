-- The bank's decision on a submitted document (CR3, CR6). Until the relationship-manager view exists (Workflow 2), the
-- bank's reviewer records it with one call in the SQL editor:
--   select compliance.record_bank_decision('<item_id>','accepted');
--   select compliance.record_bank_decision('<item_id>','rejected','The bill is older than 3 months.');
-- Accepting marks the item "Accepted by bank". Asking for a correction marks it "Correction requested by bank" and opens a
-- "The bank asked you" request on the client's review, which closes by itself when she submits a new file for that item.
-- Only the database owner can call it: no client, guest or assistant path can approve or reject a document.

alter table compliance.info_requests add column item_id uuid references compliance.requested_items(item_id) on delete cascade;

create function compliance.record_bank_decision(p_item_id uuid,p_decision text,p_note text default null,p_due date default null)
returns text language plpgsql security definer set search_path='' as $$
declare item compliance.requested_items; review compliance.cases;
begin
  if p_decision not in ('accepted','rejected') then raise exception 'decision must be accepted or rejected' using errcode='22023'; end if;
  if p_decision='rejected' and nullif(btrim(coalesce(p_note,'')),'') is null then
    raise exception 'say what the client needs to correct' using errcode='22023';
  end if;
  select * into item from compliance.requested_items where item_id=p_item_id for update;
  if not found then raise exception 'requested item not found' using errcode='P0002'; end if;
  select * into review from compliance.cases where case_id=item.case_id for share;
  if review.status in ('completed','closed') then raise exception 'the review is closed' using errcode='22023'; end if;
  if item.submission_status<>'submitted_awaiting_review' then
    raise exception 'only a submitted document can be decided (current: %)',item.submission_status using errcode='22023';
  end if;
  update compliance.requested_items set submission_status=p_decision,updated_at=clock_timestamp() where item_id=item.item_id;
  if p_decision='rejected' then
    insert into compliance.info_requests(case_id,customer_id,item_id,question,instructions,due_date)
    values(item.case_id,item.customer_id,item.item_id,format('Please send a new %s. %s',item.label,btrim(p_note)),
      'Upload the corrected file under Compliance review.',coalesce(p_due,review.due_date));
  end if;
  update compliance.cases set status_updated_at=clock_timestamp() where case_id=item.case_id;
  perform audit.record('document','document.bank_decided','success','compliance.requested_items',item.item_id::text,
    jsonb_build_object('decision',p_decision),p_customer_id=>item.customer_id);
  return case p_decision when 'accepted' then format('%s accepted',item.label) else format('%s: correction requested',item.label) end;
end $$;
revoke all on function compliance.record_bank_decision(uuid,text,text,date) from public,anon,authenticated;

-- A correction request is answered once the client submits a new file for that item.
create function compliance.close_answered_corrections() returns trigger language plpgsql security definer set search_path='' as $$
begin
  update compliance.info_requests set status='responded',responded_at=clock_timestamp()
  where item_id=new.item_id and customer_id=new.customer_id and status='open';
  return null;
end $$;
revoke all on function compliance.close_answered_corrections() from public,anon,authenticated;
create trigger close_answered_corrections after update of submission_status on compliance.requested_items
  for each row when (new.submission_status='submitted_awaiting_review' and old.submission_status='rejected')
  execute function compliance.close_answered_corrections();
