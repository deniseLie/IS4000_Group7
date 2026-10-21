-- Confirmed intake records a synthetic request, never a completed service or financial transaction.
alter table rm.requests add column command_id uuid unique references actions.commands(command_id);

create function actions.normalise_request(p_target jsonb)
returns jsonb language plpgsql immutable set search_path=''
as $$
declare deadline date; key text;
begin
  if jsonb_typeof(p_target->'advisor_id') is distinct from 'string' or p_target->>'advisor_id' !~* '^[0-9a-f]{8}-([0-9a-f]{4}-){3}[0-9a-f]{12}$'
      or jsonb_typeof(p_target->'what') is distinct from 'string' or length(p_target->>'what') not between 1 and 1000
      or p_target->>'what' !~ '[^[:space:]]' or p_target->>'urgency' is null or p_target->>'urgency' not in ('low','normal','high') then
    raise exception 'request intake requires an advisor UUID, 1-1000 nonblank characters and low/normal/high urgency' using errcode='22023';
  end if;
  foreach key in array array['deadline','country','currency'] loop
    if not (p_target ? key) then raise exception 'request intake requires explicit deadline, country and currency values or null' using errcode='22023'; end if;
    if p_target->key<>'null'::jsonb and jsonb_typeof(p_target->key) is distinct from 'string' then
      raise exception 'optional request fields must be strings or null' using errcode='22023';
    end if;
  end loop;
  if p_target->>'deadline' is not null then
    if p_target->>'deadline' !~ '^\d{4}-\d\d-\d\d$' then raise exception 'request deadline must be YYYY-MM-DD or null' using errcode='22023'; end if;
    begin deadline:=(p_target->>'deadline')::date;
    exception when invalid_datetime_format or datetime_field_overflow then
      raise exception 'request deadline is not a calendar date' using errcode='22023';
    end;
  end if;
  if p_target->>'country' is not null and p_target->>'country' !~ '^[A-Z]{2}$'
      or p_target->>'currency' is not null and p_target->>'currency' !~ '^[A-Z]{3}$' then
    raise exception 'country and currency require uppercase two/three-letter reference codes or null' using errcode='22023';
  end if;
  perform conversation.require_redacted(p_target->>'what');
  if p_target->>'what' ~ '(eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+|sb_secret_[A-Za-z0-9_-]+|sk-or-v1-[A-Za-z0-9_-]+)' then
    raise exception 'remove credentials from request details' using errcode='22023';
  end if;
  return jsonb_build_object('advisor_id',(p_target->>'advisor_id')::uuid,'what',p_target->>'what','urgency',p_target->>'urgency',
    'deadline',deadline,'country',p_target->>'country','currency',p_target->>'currency');
end $$;

create function actions.request_target(p_target jsonb,p_customer_id bigint)
returns jsonb language plpgsql volatile security definer set search_path=''
as $$
declare target jsonb:=actions.normalise_request(p_target); who text; recipient_name text;
begin
  select a.full_name into recipient_name from rm.assignments r join rm.advisors a using(advisor_id)
    where r.customer_id=p_customer_id and r.advisor_id=(target->>'advisor_id')::uuid and r.ended_on is null
      and r.started_on<=(clock_timestamp() at time zone 'Asia/Singapore')::date and a.is_active;
  if not found then raise exception 'request recipient is outside the active customer assignment' using errcode='42501'; end if;
  select full_name into who from banking.customer_profiles where customer_id=p_customer_id;
  if who is null then raise exception 'request customer profile is unavailable' using errcode='42501'; end if;
  if length(who) not between 1 and 2000 or who !~ '[^[:space:]]' or length(recipient_name) not between 1 and 200
      or recipient_name !~ '[^[:space:]]' then raise exception 'request customer or recipient name exceeds supported bounds' using errcode='22023'; end if;
  return target||jsonb_build_object('who',who,'recipient_name',recipient_name);
end $$;

create function actions.execute_request(p_command actions.commands)
returns jsonb language plpgsql volatile security definer set search_path=''
as $$
declare request rm.requests; today date:=(clock_timestamp() at time zone 'Asia/Singapore')::date;
begin
  perform consent.require_current_recipient(p_command.customer_id,(p_command.target->>'advisor_id')::uuid);
  perform 1 from banking.customer_profiles where customer_id=p_command.customer_id for share;
  if actions.request_target(p_command.target,p_command.customer_id)<>p_command.target
      or (p_command.target->>'deadline')::date<today then
    raise exception 'the request recipient, customer or deadline changed; review a new request' using errcode='P1001';
  end if;
  insert into rm.requests(customer_id,advisor_id,who,what,urgency,deadline,country,currency,status,command_id,created_at,updated_at)
    values(p_command.customer_id,(p_command.target->>'advisor_id')::uuid,p_command.target->>'who',p_command.target->>'what',
      p_command.target->>'urgency',(p_command.target->>'deadline')::date,p_command.target->>'country',p_command.target->>'currency',
      'sent',p_command.command_id,clock_timestamp(),clock_timestamp()) returning * into request;
  perform audit.record('action','action.relationship_request_recorded','success','rm.requests',request.request_id::text,
    jsonb_build_object('urgency',request.urgency),p_command.case_id);
  return jsonb_build_object('result',p_command.target||jsonb_build_object('request_id',request.request_id,'status','sent','created_at',request.created_at),
    'event_type','rm.request_recorded');
end $$;

insert into actions.executors(type,validator,executor,confirmation_required,normaliser,event_types) values
  ('requests.create','actions.request_target(jsonb,bigint)'::regprocedure,'actions.execute_request(actions.commands)'::regprocedure,
    true,'actions.normalise_request(jsonb)'::regprocedure,array['rm.request_recorded']);
revoke all on function actions.normalise_request(jsonb),actions.request_target(jsonb,bigint),actions.execute_request(actions.commands)
  from public,anon,authenticated;
