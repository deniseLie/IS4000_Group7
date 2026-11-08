-- Reset Michelle's (1002) periodic review to the storyline (Scene 1): two documents requested, the proof of address
-- already with the bank, and the sale agreement for her Bangkok property still to send. Removes her uploads and their
-- quick checks for this review, the bank's open questions on it and the support notes that pointed at them.
-- Synthetic demo data only. Files already in storage are left there (no row points to them any more).
-- One block (the Supabase SQL editor runs each statement on its own, so no temporary table).
do $$
declare c uuid; i11 uuid:='00000000-0000-4000-8000-100200000011'; i12 uuid:='00000000-0000-4000-8000-100200000012';
begin
  select case_id into c from compliance.cases where customer_id=1002 order by created_at desc limit 1;
  if c is null then raise exception 'No periodic review found for customer 1002'; end if;
  -- Uploads and checks (and anything that pointed at them).
  update compliance.requested_items set current_document_id=null where case_id =c;
  delete from compliance.support_context where review_case_id =c;
  delete from support.ticket_attachments where document_id in (select document_id from compliance.documents where case_id =c);
  delete from compliance.prechecks where case_id =c;
  delete from compliance.info_requests where case_id =c;
  delete from compliance.documents where case_id =c;

  -- Anything still pointing at the two documents from an older review is cleared too.
  delete from compliance.support_context where item_id in (i11,i12);
  delete from compliance.prechecks where item_id in (i11,i12);
  delete from compliance.info_requests where item_id in (i11,i12);
  update compliance.documents set item_id=null where item_id in (i11,i12);

  -- Exactly two documents: the sale agreement (to send) and the proof of address (sent 5 Nov, with the bank).
  -- Inserted if missing, moved onto this review if they sat on another one; every other request is removed.
  delete from compliance.requested_items where case_id=c and item_id not in (i11,i12);
  insert into compliance.requested_items(item_id,case_id,customer_id,label,instructions,check_guidance,submission_status,client_mark,client_marked_at,sort_order,updated_at)
  values
   (i12,c,1002,'Sale agreement for your Bangkok property',
    'The signed sale agreement for your Bangkok flat. We ask because the sale money was paid into your account: it shows where the money came from. Include every page, with both names, the sale price, the date and the signatures. If it is in Thai, add an English translation in the same file.',
    'Check that the agreement is signed by both sides, shows the seller and buyer names, the property address, the sale price with its currency and the sale date, and that every page is included. If it is in Thai, an English translation must be included.',
    'not_submitted','not_prepared',null,1,now()),
   (i11,c,1002,'Proof of address',
    'A utility bill or bank statement from the last 3 months that shows your name and home address.',
    'Check the name matches the client, the address is a home address and the document is dated within the last 3 months.',
    'submitted_awaiting_review','prepared',timestamptz '2026-11-05 10:00+08',2,timestamptz '2026-11-05 10:00+08')
  on conflict (item_id) do update set case_id=excluded.case_id,customer_id=excluded.customer_id,label=excluded.label,instructions=excluded.instructions,
    check_guidance=excluded.check_guidance,submission_status=excluded.submission_status,client_mark=excluded.client_mark,
    client_marked_at=excluded.client_marked_at,sort_order=excluded.sort_order,current_document_id=null,updated_at=excluded.updated_at;

  update compliance.cases set title='Periodic review 2026',
    notice_text='As part of our regular review, please send the documents listed by 30 November 2026: the signed sale agreement for your Bangkok property (to show where the sale money came from) and a recent proof of address.',
    due_date='2026-11-30',status='documents_partially_received',status_updated_at=now()
    where case_id =c;
  insert into compliance.status_history(case_id,customer_id,status,note,changed_at)
    values(c,1002,'documents_partially_received','Demo reset: sale agreement requested; proof of address with the bank',now());

end $$;

-- Check: should list the sale agreement (not_submitted) then proof of address (submitted_awaiting_review).
select i.label,i.submission_status from compliance.requested_items i
where i.case_id=(select case_id from compliance.cases where customer_id=1002 order by created_at desc limit 1) order by i.sort_order;
