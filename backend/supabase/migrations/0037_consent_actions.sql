-- Confirmed consent writes share the existing ledger, receipt, audit and outbox transaction.
alter table consent.requests add column grant_expires_at timestamptz;
alter table consent.requests add column command_id uuid unique references actions.commands(command_id);
alter table consent.grants add constraint grants_command_fk foreign key(command_id) references actions.commands(command_id);
alter table consent.grants add column revocation_command_id uuid unique references actions.commands(command_id);
create unique index grants_one_per_request on consent.grants(request_id) where request_id is not null;

create function consent.require_current_recipient(p_customer_id bigint,p_recipient uuid)
returns text language plpgsql volatile security definer set search_path=''
as $$
declare recipient_name text;
begin
  select a.full_name into recipient_name from rm.assignments r join rm.advisors a using(advisor_id)
    where r.customer_id=p_customer_id and r.advisor_id=p_recipient and r.ended_on is null
      and r.started_on<=(clock_timestamp() at time zone 'Asia/Singapore')::date and a.is_active for share of r,a;
  if not found then raise exception 'the active relationship manager assignment changed' using errcode='P1001'; end if;
  return recipient_name;
end $$;

create function actions.normalise_consent_request(p_target jsonb)
returns jsonb language plpgsql immutable set search_path=''
as $$
begin
  if p_target->>'purpose' is distinct from 'rm_brief' or jsonb_typeof(p_target->'recipient') is distinct from 'string'
      or p_target->>'recipient' !~* '^[0-9a-f]{8}-([0-9a-f]{4}-){3}[0-9a-f]{12}$'
      or jsonb_typeof(p_target->'duration_days') is distinct from 'number' or p_target->>'duration_days' !~ '^[0-9]{1,3}$'
      or (p_target->>'duration_days')::int not between 1 and 365 or jsonb_typeof(p_target->'categories') is distinct from 'array' then
    raise exception 'consent request requires rm_brief, an assigned recipient UUID, categories and 1-365 duration_days' using errcode='22023';
  end if;
  if jsonb_array_length(p_target->'categories') not between 1 and 7 or exists(select 1 from jsonb_array_elements(p_target->'categories') c
      where jsonb_typeof(c)<>'string' or c#>>'{}' not in ('profile','agenda','accounts','transactions','cards','loans','compliance'))
      or (select count(distinct c) from jsonb_array_elements(p_target->'categories') c)<>jsonb_array_length(p_target->'categories') then
    raise exception 'consent categories must be unique supported data categories' using errcode='22023';
  end if;
  return jsonb_build_object('purpose','rm_brief','recipient',(p_target->>'recipient')::uuid,
    'categories',(select jsonb_agg(c order by c) from jsonb_array_elements(p_target->'categories') c),'duration_days',(p_target->>'duration_days')::int);
end $$;

create function actions.normalise_consent_grant(p_target jsonb)
returns jsonb language plpgsql immutable set search_path=''
as $$
begin
  if jsonb_typeof(p_target->'request_id') is distinct from 'string' or p_target->>'request_id' !~* '^[0-9a-f]{8}-([0-9a-f]{4}-){3}[0-9a-f]{12}$' then
    raise exception 'consent grant requires a recorded request UUID' using errcode='22023';
  end if;
  return jsonb_build_object('request_id',(p_target->>'request_id')::uuid);
end $$;

create function actions.normalise_consent_revoke(p_target jsonb)
returns jsonb language plpgsql immutable set search_path=''
as $$
begin
  if jsonb_typeof(p_target->'grant_id') is distinct from 'string' or p_target->>'grant_id' !~* '^[0-9a-f]{8}-([0-9a-f]{4}-){3}[0-9a-f]{12}$' then
    raise exception 'consent revocation requires a recorded grant UUID' using errcode='22023';
  end if;
  return jsonb_build_object('grant_id',(p_target->>'grant_id')::uuid);
end $$;

create function actions.consent_request_target(p_target jsonb,p_customer_id bigint)
returns jsonb language plpgsql volatile security definer set search_path=''
as $$
declare target jsonb:=actions.normalise_consent_request(p_target); recipient_name text;
begin
  if not exists(select 1 from rm.assignments where customer_id=p_customer_id and advisor_id=(target->>'recipient')::uuid and ended_on is null) then
    raise exception 'consent recipient is outside the current customer assignment' using errcode='42501';
  end if;
  select a.full_name into recipient_name from rm.advisors a where a.advisor_id=(target->>'recipient')::uuid;
  return target||jsonb_build_object('recipient_name',recipient_name);
end $$;

create function actions.consent_grant_target(p_target jsonb,p_customer_id bigint)
returns jsonb language plpgsql volatile security definer set search_path='' set timezone='UTC'
as $$
declare target jsonb:=actions.normalise_consent_grant(p_target); request consent.requests; recipient_name text;
begin
  select * into request from consent.requests where request_id=(target->>'request_id')::uuid and customer_id=p_customer_id;
  if not found then raise exception 'consent request is outside the customer scope' using errcode='42501'; end if;
  if request.grant_expires_at is null or request.command_id is null then
    raise exception 'this legacy request has no reviewed expiry; record a new scoped request' using errcode='22023';
  end if;
  select full_name into recipient_name from rm.advisors where advisor_id::text=request.recipient;
  return target||jsonb_build_object('purpose',request.purpose,'recipient',request.recipient,'recipient_name',recipient_name,
    'categories',(select jsonb_agg(c order by c) from unnest(request.categories)c),'expires_at',request.grant_expires_at);
end $$;

create function actions.consent_revoke_target(p_target jsonb,p_customer_id bigint)
returns jsonb language plpgsql volatile security definer set search_path='' set timezone='UTC'
as $$
declare target jsonb:=actions.normalise_consent_revoke(p_target); v_grant consent.grants; recipient_name text;
begin
  select * into v_grant from consent.grants where grant_id=(target->>'grant_id')::uuid and customer_id=p_customer_id;
  if not found then raise exception 'consent grant is outside the customer scope' using errcode='42501'; end if;
  select full_name into recipient_name from rm.advisors where advisor_id::text=v_grant.recipient;
  return target||jsonb_build_object('purpose',v_grant.purpose,'recipient',v_grant.recipient,'recipient_name',recipient_name,
    'categories',(select jsonb_agg(c order by c) from unnest(v_grant.categories)c),'expires_at',v_grant.expires_at);
end $$;

create function actions.execute_consent_request(p_command actions.commands)
returns jsonb language plpgsql volatile security definer set search_path=''
as $$
declare request consent.requests; recipient_name text;
begin
  perform pg_advisory_xact_lock(hashtext('consent.customer'),hashtext(p_command.customer_id::text));
  recipient_name:=consent.require_current_recipient(p_command.customer_id,(p_command.target->>'recipient')::uuid);
  if recipient_name is distinct from p_command.target->>'recipient_name' then
    raise exception 'the displayed consent recipient changed' using errcode='P1001';
  end if;
  insert into consent.requests(customer_id,purpose,recipient,categories,grant_expires_at,command_id)
    values(p_command.customer_id,p_command.target->>'purpose',p_command.target->>'recipient',
      array(select jsonb_array_elements_text(p_command.target->'categories')),
      clock_timestamp()+make_interval(days=>(p_command.target->>'duration_days')::int),p_command.command_id) returning * into request;
  perform audit.record('consent','consent.requested','success','consent.requests',request.request_id::text,
    jsonb_build_object('category_count',cardinality(request.categories)),p_command.case_id);
  return jsonb_build_object('result',p_command.target||jsonb_build_object('request_id',request.request_id,'expires_at',request.grant_expires_at,'status','pending'),
    'event_type','consent.requested');
end $$;

create function actions.execute_consent_grant(p_command actions.commands)
returns jsonb language plpgsql volatile security definer set search_path=''
as $$
declare request consent.requests; v_grant consent.grants;
begin
  perform pg_advisory_xact_lock(hashtext('consent.customer'),hashtext(p_command.customer_id::text));
  select * into request from consent.requests where request_id=(p_command.target->>'request_id')::uuid and customer_id=p_command.customer_id for update;
  if not found or request.status<>'pending' or request.grant_expires_at is null or request.grant_expires_at<=clock_timestamp()
      or actions.consent_grant_target(p_command.target,p_command.customer_id)<>p_command.target then
    raise exception 'the reviewed consent request expired or changed' using errcode='P1001';
  end if;
  perform consent.require_current_recipient(p_command.customer_id,(request.recipient)::uuid);
  insert into consent.grants(customer_id,purpose,recipient,categories,request_id,command_id,granted_at,expires_at)
    values(p_command.customer_id,request.purpose,request.recipient,request.categories,request.request_id,p_command.command_id,clock_timestamp(),request.grant_expires_at)
    returning * into v_grant;
  update consent.requests set status='granted' where request_id=request.request_id and customer_id=p_command.customer_id;
  perform audit.record('consent','consent.granted','success','consent.grants',v_grant.grant_id::text,
    jsonb_build_object('category_count',cardinality(v_grant.categories)),p_command.case_id);
  return jsonb_build_object('result',p_command.target||jsonb_build_object('grant_id',v_grant.grant_id,'status','active'),'event_type','consent.granted');
end $$;

create function actions.execute_consent_revoke(p_command actions.commands)
returns jsonb language plpgsql volatile security definer set search_path=''
as $$
declare v_grant consent.grants;
begin
  perform pg_advisory_xact_lock(hashtext('consent.customer'),hashtext(p_command.customer_id::text));
  select * into v_grant from consent.grants where grant_id=(p_command.target->>'grant_id')::uuid and customer_id=p_command.customer_id for update;
  if not found or v_grant.revoked_at is not null or actions.consent_revoke_target(p_command.target,p_command.customer_id)<>p_command.target then
    raise exception 'the reviewed consent grant was revoked or changed' using errcode='P1001';
  end if;
  update consent.grants set revoked_at=clock_timestamp(),revocation_command_id=p_command.command_id
    where grant_id=v_grant.grant_id and customer_id=p_command.customer_id returning * into v_grant;
  perform audit.record('consent','consent.revoked','success','consent.grants',v_grant.grant_id::text,'{}',p_command.case_id);
  return jsonb_build_object('result',p_command.target||jsonb_build_object('revoked_at',v_grant.revoked_at,'status','revoked'),'event_type','consent.revoked');
end $$;

insert into actions.executors(type,validator,executor,confirmation_required,normaliser,event_types) values
  ('consent.request','actions.consent_request_target(jsonb,bigint)'::regprocedure,'actions.execute_consent_request(actions.commands)'::regprocedure,false,
    'actions.normalise_consent_request(jsonb)'::regprocedure,array['consent.requested']),
  ('consent.grant','actions.consent_grant_target(jsonb,bigint)'::regprocedure,'actions.execute_consent_grant(actions.commands)'::regprocedure,true,
    'actions.normalise_consent_grant(jsonb)'::regprocedure,array['consent.granted']),
  ('consent.revoke','actions.consent_revoke_target(jsonb,bigint)'::regprocedure,'actions.execute_consent_revoke(actions.commands)'::regprocedure,true,
    'actions.normalise_consent_revoke(jsonb)'::regprocedure,array['consent.revoked']);

-- Direct customer forms create an owned action case without fabricating an AI/chat turn.
create function public.action_create_case(p_session_id uuid,p_case_id uuid)
returns jsonb language plpgsql volatile security definer set search_path=''
as $$
declare cid bigint:=banking.my_customer_id();
begin
  if auth.uid() is null or cid is null then raise exception 'a linked customer session is required' using errcode='42501'; end if;
  if p_session_id is null or p_case_id is null then raise exception 'stable session and case UUIDs are required' using errcode='22023'; end if;
  perform public.conversation_create(p_session_id,'personal','English',null);
  insert into conversation.cases(case_id,session_id,customer_id,topic,priority)
    values(p_case_id,p_session_id,cid,'customer_action','P3') on conflict do nothing;
  if not exists(select 1 from conversation.cases where case_id=p_case_id and session_id=p_session_id and customer_id=cid and topic='customer_action' and status='open') then
    raise exception 'this case is unavailable for a new customer action' using errcode='42501';
  end if;
  return jsonb_build_object('case_id',p_case_id,'session_id',p_session_id,'customer_id',cid);
end $$;

create function public.customer_read_consent_records(p_customer_id bigint)
returns jsonb language plpgsql volatile security invoker set search_path=''
as $$
declare cid bigint:=banking.require_read_customer(p_customer_id); requests jsonb; grants jsonb; checked_at timestamptz:=clock_timestamp();
begin
  select coalesce(jsonb_agg(to_jsonb(r) order by r.created_at desc,r.request_id),'[]') into requests from(
    select r.request_id,r.purpose,r.recipient,r.categories,r.grant_expires_at,r.created_at,a.full_name recipient_name,
      case when r.status='pending' and r.grant_expires_at<=checked_at then 'expired' else r.status end status,
      case when c.type='consent.request' then c.case_id end case_id
    from consent.requests r left join rm.advisors a on a.advisor_id::text=r.recipient
      left join actions.commands c on c.command_id=r.command_id and c.customer_id=cid
    where r.customer_id=cid order by r.created_at desc,r.request_id limit 200)r;
  select coalesce(jsonb_agg(to_jsonb(g) order by g.granted_at desc,g.grant_id),'[]') into grants from(
    select g.grant_id,g.purpose,g.recipient,g.categories,g.request_id,g.granted_at,g.expires_at,g.revoked_at,a.full_name recipient_name,
      g.revoked_at is null and g.granted_at<=checked_at and (g.expires_at is null or g.expires_at>checked_at) active
    from consent.grants g left join rm.advisors a on a.advisor_id::text=g.recipient
    where g.customer_id=cid order by g.granted_at desc,g.grant_id limit 200)g;
  return banking.customer_result(cid,'consent.records','consent.grants',jsonb_build_object('requests',requests,'grants',grants));
end $$;

revoke all on function consent.require_current_recipient(bigint,uuid),actions.normalise_consent_request(jsonb),actions.normalise_consent_grant(jsonb),
  actions.normalise_consent_revoke(jsonb),actions.consent_request_target(jsonb,bigint),actions.consent_grant_target(jsonb,bigint),
  actions.consent_revoke_target(jsonb,bigint),actions.execute_consent_request(actions.commands),actions.execute_consent_grant(actions.commands),
  actions.execute_consent_revoke(actions.commands) from public,anon,authenticated;
revoke all on function public.action_create_case(uuid,uuid),public.customer_read_consent_records(bigint) from public,anon;
grant execute on function public.action_create_case(uuid,uuid),public.customer_read_consent_records(bigint) to authenticated;
