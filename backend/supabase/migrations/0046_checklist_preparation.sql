-- Customer preparation is never document submission, acceptance or a case decision.
create function actions.normalise_checklist(p_target jsonb)
returns jsonb language plpgsql immutable set search_path='' set timezone='UTC'
as $$
declare marked_at timestamptz;
begin
  if jsonb_typeof(p_target->'case_id') is distinct from 'string' or p_target->>'case_id' !~* '^[0-9a-f]{8}-([0-9a-f]{4}-){3}[0-9a-f]{12}$'
      or jsonb_typeof(p_target->'item_id') is distinct from 'string' or p_target->>'item_id' !~* '^[0-9a-f]{8}-([0-9a-f]{4}-){3}[0-9a-f]{12}$'
      or p_target->>'client_mark' is null or p_target->>'client_mark' not in ('prepared','not_prepared')
      or p_target->>'expected_client_mark' is null or p_target->>'expected_client_mark' not in ('prepared','not_prepared')
      or not (p_target ? 'expected_client_marked_at') then
    raise exception 'checklist preparation requires owned case/item UUIDs, supported marks and the previous marked timestamp' using errcode='22023';
  end if;
  if p_target->'expected_client_marked_at'<>'null'::jsonb then
    if jsonb_typeof(p_target->'expected_client_marked_at') is distinct from 'string'
        or p_target->>'expected_client_marked_at' !~ '^\d{4}-\d\d-\d\dT.*(Z|[+-]\d\d:\d\d)$' then
      raise exception 'expected_client_marked_at must be null or an aware timestamp' using errcode='22023';
    end if;
    begin marked_at:=(p_target->>'expected_client_marked_at')::timestamptz;
    exception when invalid_datetime_format or datetime_field_overflow then
      raise exception 'expected_client_marked_at is not a valid timestamp' using errcode='22023';
    end;
    if not isfinite(marked_at) then raise exception 'expected_client_marked_at must be finite' using errcode='22023'; end if;
  end if;
  if p_target->>'client_mark'=p_target->>'expected_client_mark' then
    raise exception 'select a different preparation mark before saving' using errcode='22023';
  end if;
  return jsonb_build_object('case_id',(p_target->>'case_id')::uuid,'item_id',(p_target->>'item_id')::uuid,
    'client_mark',p_target->>'client_mark','expected_client_mark',p_target->>'expected_client_mark','expected_client_marked_at',marked_at);
end $$;

create function actions.checklist_target(p_target jsonb,p_customer_id bigint)
returns jsonb language plpgsql volatile security definer set search_path=''
as $$
declare target jsonb:=actions.normalise_checklist(p_target); review compliance.cases; item compliance.requested_items;
begin
  select * into review from compliance.cases where case_id=(target->>'case_id')::uuid and customer_id=p_customer_id;
  select * into item from compliance.requested_items where item_id=(target->>'item_id')::uuid
    and case_id=(target->>'case_id')::uuid and customer_id=p_customer_id;
  if review.case_id is null or item.item_id is null then
    raise exception 'the checklist case and item are outside this customer scope' using errcode='42501';
  end if;
  if length(review.title) not between 1 and 2000 or review.title !~ '[^[:space:]]'
      or length(item.label) not between 1 and 2000 or item.label !~ '[^[:space:]]' then
    raise exception 'the checklist title or item label exceeds supported bounds' using errcode='22023';
  end if;
  return target||jsonb_build_object('case_title',review.title,'item_label',item.label,'case_status',review.status,'submission_status',item.submission_status);
end $$;

create function actions.execute_checklist(p_command actions.commands)
returns jsonb language plpgsql volatile security definer set search_path=''
as $$
declare review compliance.cases; item compliance.requested_items; marked_at timestamptz;
begin
  select * into review from compliance.cases where case_id=(p_command.target->>'case_id')::uuid
    and customer_id=p_command.customer_id for share;
  select * into item from compliance.requested_items where item_id=(p_command.target->>'item_id')::uuid
    and case_id=review.case_id and customer_id=p_command.customer_id for update;
  if review.case_id is null or item.item_id is null or review.status in ('completed','closed')
      or item.client_mark is distinct from p_command.target->>'expected_client_mark'
      or item.client_marked_at is distinct from (p_command.target->>'expected_client_marked_at')::timestamptz
      or actions.checklist_target(p_command.target,p_command.customer_id)<>p_command.target then
    raise exception 'the checklist or case changed; reload before changing its preparation mark' using errcode='P1001';
  end if;
  marked_at:=clock_timestamp();
  update compliance.requested_items set client_mark=p_command.target->>'client_mark',client_marked_at=marked_at,updated_at=marked_at
    where item_id=item.item_id and case_id=review.case_id and customer_id=p_command.customer_id;
  perform audit.record('action','action.checklist_preparation_saved','success','compliance.requested_items',item.item_id::text,
    jsonb_build_object('client_mark',p_command.target->>'client_mark'),p_command.case_id);
  return jsonb_build_object('result',p_command.target||jsonb_build_object('client_marked_at',marked_at),'event_type','compliance.checklist_prepared');
end $$;

insert into actions.executors(type,validator,executor,confirmation_required,normaliser,event_types) values
  ('compliance.update_checklist_item','actions.checklist_target(jsonb,bigint)'::regprocedure,'actions.execute_checklist(actions.commands)'::regprocedure,
    false,'actions.normalise_checklist(jsonb)'::regprocedure,array['compliance.checklist_prepared']);

revoke all on function actions.normalise_checklist(jsonb),actions.checklist_target(jsonb,bigint),actions.execute_checklist(actions.commands)
  from public,anon,authenticated;
