-- Saving a versioned draft does not share it. All writes use the existing action transaction.
alter table rm.agendas add column revision integer not null default 0 check(revision>=0);
alter table rm.agendas add column last_command_id uuid unique references actions.commands(command_id);

create function actions.normalise_agenda_save(p_target jsonb)
returns jsonb language plpgsql immutable set search_path=''
as $$
declare topic jsonb;
begin
  if jsonb_typeof(p_target->'agenda_id') is distinct from 'string' or p_target->>'agenda_id' !~* '^[0-9a-f]{8}-([0-9a-f]{4}-){3}[0-9a-f]{12}$'
      or jsonb_typeof(p_target->'advisor_id') is distinct from 'string' or p_target->>'advisor_id' !~* '^[0-9a-f]{8}-([0-9a-f]{4}-){3}[0-9a-f]{12}$'
      or jsonb_typeof(p_target->'expected_revision') is distinct from 'number' or p_target->>'expected_revision' !~ '^[0-9]{1,10}$'
      or (p_target->>'expected_revision')::bigint>2147483646
      or jsonb_typeof(p_target->'topics') is distinct from 'array' or jsonb_typeof(p_target->'profile_fields') is distinct from 'array' then
    raise exception 'agenda.save requires agenda/advisor UUIDs, an integer expected_revision, topics and profile_fields' using errcode='22023';
  end if;
  if jsonb_array_length(p_target->'topics') not between 1 and 12 then
    raise exception 'an agenda requires 1-12 nonblank topics of at most 300 characters each' using errcode='22023';
  end if;
  for topic in select value from jsonb_array_elements(p_target->'topics') loop
    if jsonb_typeof(topic)<>'string' or length(topic#>>'{}') not between 1 and 300 or topic#>>'{}' !~ '[^[:space:]]' then
      raise exception 'an agenda requires 1-12 nonblank topics of at most 300 characters each' using errcode='22023';
    end if;
    perform conversation.require_redacted(topic#>>'{}');
    if topic#>>'{}' ~ '(eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+|sb_secret_[A-Za-z0-9_-]+|sk-or-v1-[A-Za-z0-9_-]+)' then
      raise exception 'remove credentials from agenda topics' using errcode='22023';
    end if;
  end loop;
  if jsonb_array_length(p_target->'profile_fields')>3 or exists(select 1 from jsonb_array_elements(p_target->'profile_fields') f
      where jsonb_typeof(f)<>'string' or f#>>'{}' not in ('name','preferred_language','employment_status'))
      or (select count(distinct f) from jsonb_array_elements(p_target->'profile_fields') f)<>jsonb_array_length(p_target->'profile_fields') then
    raise exception 'profile_fields must be unique selections of name, preferred_language and employment_status' using errcode='22023';
  end if;
  return jsonb_build_object('agenda_id',(p_target->>'agenda_id')::uuid,'advisor_id',(p_target->>'advisor_id')::uuid,
    'expected_revision',(p_target->>'expected_revision')::int,'topics',p_target->'topics',
    'profile_fields',(select coalesce(jsonb_agg(f order by f),'[]') from jsonb_array_elements(p_target->'profile_fields')f));
end $$;

create function actions.agenda_save_target(p_target jsonb,p_customer_id bigint)
returns jsonb language plpgsql volatile security definer set search_path=''
as $$
declare target jsonb:=actions.normalise_agenda_save(p_target); profile banking.customer_profiles;
  recipient_name text; snapshot jsonb; content text; fields jsonb:=target->'profile_fields';
begin
  if exists(select 1 from rm.agendas where agenda_id=(target->>'agenda_id')::uuid and customer_id<>p_customer_id) then
    raise exception 'agenda is outside the customer scope' using errcode='42501';
  end if;
  if not exists(select 1 from rm.assignments where customer_id=p_customer_id and advisor_id=(target->>'advisor_id')::uuid and ended_on is null) then
    raise exception 'agenda recipient is outside the current customer assignment' using errcode='42501';
  end if;
  select full_name into recipient_name from rm.advisors where advisor_id=(target->>'advisor_id')::uuid;
  select * into profile from banking.customer_profiles where customer_id=p_customer_id;
  if not found then raise exception 'agenda customer profile is unavailable' using errcode='42501'; end if;
  snapshot:=jsonb_build_object('name',case when fields ? 'name' then profile.full_name end,
    'preferred_language',case when fields ? 'preferred_language' then profile.preferred_language end,
    'employment_status',case when fields ? 'employment_status' then profile.employment_status end);
  if exists(select 1 from jsonb_each_text(snapshot) f where f.value is not null and (length(f.value) not between 1 and 2000 or f.value !~ '[^[:space:]]')) then
    raise exception 'selected profile fields exceed the supported agenda bounds' using errcode='22023';
  end if;
  content:=concat_ws(E'\n',case when fields ? 'name' then 'Name: '||profile.full_name end,
    case when fields ? 'preferred_language' then 'Preferred language: '||profile.preferred_language end,
    case when fields ? 'employment_status' then 'Employment status: '||profile.employment_status end,
    'Topics:',(select string_agg('- '||t.value,E'\n' order by t.ordinality) from jsonb_array_elements_text(target->'topics') with ordinality t));
  perform conversation.require_redacted(content);
  if length(content)>6000 then raise exception 'the agenda including selected profile fields exceeds 6000 characters' using errcode='22023'; end if;
  return target||jsonb_build_object('recipient_name',recipient_name,'profile_snapshot',snapshot,'content',content,
    'categories',case when jsonb_array_length(fields)>0 then jsonb_build_array('agenda','profile') else jsonb_build_array('agenda') end);
end $$;

create function actions.execute_agenda_save(p_command actions.commands)
returns jsonb language plpgsql volatile security definer set search_path=''
as $$
declare agenda rm.agendas; recipient_name text;
begin
  perform pg_advisory_xact_lock(hashtext('rm.agenda'),hashtext(p_command.target->>'agenda_id'));
  select * into agenda from rm.agendas where agenda_id=(p_command.target->>'agenda_id')::uuid for update;
  if found then
    if agenda.customer_id<>p_command.customer_id or agenda.revision<>(p_command.target->>'expected_revision')::int then
      raise exception 'the agenda revision changed; reload the saved draft before editing' using errcode='P1001';
    end if;
  elsif (p_command.target->>'expected_revision')::int<>0 then
    raise exception 'the agenda no longer exists; reload before editing' using errcode='P1001';
  end if;
  recipient_name:=consent.require_current_recipient(p_command.customer_id,(p_command.target->>'advisor_id')::uuid);
  if actions.agenda_save_target(p_command.target,p_command.customer_id)<>p_command.target then
    raise exception 'the selected profile or recipient changed while saving' using errcode='P1001';
  end if;
  insert into rm.agendas(agenda_id,customer_id,advisor_id,topics,status,revision,last_command_id,updated_at)
    values((p_command.target->>'agenda_id')::uuid,p_command.customer_id,(p_command.target->>'advisor_id')::uuid,
      p_command.target->'topics','draft',1,p_command.command_id,clock_timestamp())
    on conflict(agenda_id) do update set advisor_id=excluded.advisor_id,topics=excluded.topics,status='draft',shared_at=null,
      revision=rm.agendas.revision+1,last_command_id=excluded.last_command_id,updated_at=excluded.updated_at returning * into agenda;
  perform audit.record('action','action.agenda_saved','success','rm.agendas',agenda.agenda_id::text,
    jsonb_build_object('revision',agenda.revision,'topic_count',jsonb_array_length(agenda.topics)),p_command.case_id);
  return jsonb_build_object('result',p_command.target||jsonb_build_object('revision',agenda.revision,'status','draft','updated_at',agenda.updated_at),
    'event_type','agenda.saved');
end $$;

insert into actions.executors(type,validator,executor,confirmation_required,normaliser,event_types) values
  ('agenda.save','actions.agenda_save_target(jsonb,bigint)'::regprocedure,'actions.execute_agenda_save(actions.commands)'::regprocedure,
    false,'actions.normalise_agenda_save(jsonb)'::regprocedure,array['agenda.saved']);

create function public.customer_read_agendas(p_customer_id bigint)
returns jsonb language plpgsql volatile security invoker set search_path=''
as $$
declare cid bigint:=banking.require_read_customer(p_customer_id); agendas jsonb; total bigint;
begin
  with rows as materialized(select a.agenda_id,a.updated_at,c.target||jsonb_build_object('revision',a.revision,'status',a.status,'updated_at',a.updated_at) data
    from rm.agendas a join actions.commands c on c.command_id=a.last_command_id and c.customer_id=cid and c.type='agenda.save' and c.state='succeeded'
    where a.customer_id=cid)
  select (select count(*) from rows),(select coalesce(jsonb_agg(r.data order by r.updated_at desc,r.agenda_id),'[]') from(
    select * from rows order by updated_at desc,agenda_id limit 200)r) into total,agendas;
  return banking.customer_result(cid,'agenda.list','rm.agendas',jsonb_build_object('agendas',agendas,'agendas_total',total));
end $$;

revoke all on function actions.normalise_agenda_save(jsonb),actions.agenda_save_target(jsonb,bigint),actions.execute_agenda_save(actions.commands)
  from public,anon,authenticated;
revoke all on function public.customer_read_agendas(bigint) from public,anon;
grant execute on function public.customer_read_agendas(bigint) to authenticated;
