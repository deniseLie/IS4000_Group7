-- Sharing records an immutable copy; later draft edits and consent revocations do not erase it.
alter table rm.shared_summaries add column agenda_id uuid references rm.agendas(agenda_id);
alter table rm.shared_summaries add column agenda_revision integer check(agenda_revision>0);
alter table rm.shared_summaries add column command_id uuid unique references actions.commands(command_id);
alter table rm.shared_summaries add constraint shared_agenda_provenance check(
  command_id is null or (agenda_id is not null and agenda_revision is not null));

create function actions.normalise_agenda_share(p_target jsonb)
returns jsonb language plpgsql immutable set search_path=''
as $$
declare key text;
begin
  foreach key in array array['agenda_id','advisor_id','consent_grant_id'] loop
    if jsonb_typeof(p_target->key) is distinct from 'string' or p_target->>key !~* '^[0-9a-f]{8}-([0-9a-f]{4}-){3}[0-9a-f]{12}$' then
      raise exception 'rm.share_summary requires agenda, advisor and consent grant UUIDs' using errcode='22023';
    end if;
  end loop;
  if jsonb_typeof(p_target->'agenda_revision') is distinct from 'number' or p_target->>'agenda_revision' !~ '^[0-9]{1,10}$'
      or (p_target->>'agenda_revision')::bigint not between 1 and 2147483647 then
    raise exception 'rm.share_summary requires a saved positive integer agenda_revision' using errcode='22023';
  end if;
  return jsonb_build_object('agenda_id',(p_target->>'agenda_id')::uuid,'advisor_id',(p_target->>'advisor_id')::uuid,
    'consent_grant_id',(p_target->>'consent_grant_id')::uuid,'agenda_revision',(p_target->>'agenda_revision')::int);
end $$;

create function actions.agenda_share_target(p_target jsonb,p_customer_id bigint)
returns jsonb language plpgsql volatile security definer set search_path=''
as $$
declare target jsonb:=actions.normalise_agenda_share(p_target); agenda rm.agendas;
  source actions.commands; v_grant consent.grants; recipient_name text;
begin
  select * into agenda from rm.agendas where agenda_id=(target->>'agenda_id')::uuid and customer_id=p_customer_id;
  if not found or agenda.advisor_id<>(target->>'advisor_id')::uuid then
    raise exception 'the saved agenda is outside this customer and recipient scope' using errcode='42501';
  end if;
  if agenda.revision<>(target->>'agenda_revision')::int then
    raise exception 'the agenda revision changed; reload before proposing sharing' using errcode='22023';
  end if;
  select * into source from actions.commands where command_id=agenda.last_command_id and customer_id=p_customer_id
    and type='agenda.save' and state='succeeded';
  if not found or source.target->>'agenda_id' is distinct from agenda.agenda_id::text
      or source.target->>'advisor_id' is distinct from agenda.advisor_id::text
      or source.target->'topics' is distinct from agenda.topics
      or (source.target->>'expected_revision')::int+1 is distinct from agenda.revision then
    raise exception 'the agenda has no consistent action-created preview; save it again before sharing' using errcode='22023';
  end if;
  select a.full_name into recipient_name from rm.assignments r join rm.advisors a using(advisor_id)
    where r.customer_id=p_customer_id and r.advisor_id=agenda.advisor_id and r.ended_on is null
      and r.started_on<=(clock_timestamp() at time zone 'Asia/Singapore')::date and a.is_active;
  if not found then raise exception 'the agenda recipient is no longer assigned' using errcode='42501'; end if;
  select * into v_grant from consent.grants where grant_id=(target->>'consent_grant_id')::uuid and customer_id=p_customer_id
    and purpose='rm_brief' and recipient=agenda.advisor_id::text;
  if not found then raise exception 'the consent grant is outside this customer, purpose and recipient scope' using errcode='42501'; end if;
  return target||jsonb_build_object('agenda_command_id',source.command_id,'recipient_name',recipient_name,
    'content',source.target->>'content','profile_fields',source.target->'profile_fields','categories',source.target->'categories',
    'consent_categories',(select jsonb_agg(c order by c) from unnest(v_grant.categories)c),'grant_expires_at',v_grant.expires_at);
end $$;

create function actions.execute_agenda_share(p_command actions.commands)
returns jsonb language plpgsql volatile security definer set search_path=''
as $$
declare agenda rm.agendas; source actions.commands; v_grant consent.grants; summary rm.shared_summaries; recipient_name text; v_shared_at timestamptz;
begin
  perform pg_advisory_xact_lock(hashtext('consent.customer'),hashtext(p_command.customer_id::text));
  perform pg_advisory_xact_lock(hashtext('rm.agenda'),hashtext(p_command.target->>'agenda_id'));
  select * into agenda from rm.agendas where agenda_id=(p_command.target->>'agenda_id')::uuid and customer_id=p_command.customer_id for update;
  if not found or agenda.revision<>(p_command.target->>'agenda_revision')::int or agenda.advisor_id<>(p_command.target->>'advisor_id')::uuid
      or agenda.last_command_id<>(p_command.target->>'agenda_command_id')::uuid then
    raise exception 'the reviewed agenda revision or recipient changed; reload before sharing' using errcode='P1001';
  end if;
  select * into source from actions.commands where command_id=agenda.last_command_id and customer_id=p_command.customer_id
    and type='agenda.save' and state='succeeded';
  if not found or source.target->>'content' is distinct from p_command.target->>'content'
      or source.target->'topics' is distinct from agenda.topics or source.target->'categories' is distinct from p_command.target->'categories'
      or source.target->'profile_fields' is distinct from p_command.target->'profile_fields' then
    raise exception 'the reviewed saved content changed; save and review a new draft' using errcode='P1001';
  end if;
  recipient_name:=consent.require_current_recipient(p_command.customer_id,agenda.advisor_id);
  select * into v_grant from consent.grants where grant_id=(p_command.target->>'consent_grant_id')::uuid
    and customer_id=p_command.customer_id for share;
  if not found or v_grant.purpose<>'rm_brief' or v_grant.recipient<>agenda.advisor_id::text
      or v_grant.revoked_at is not null or v_grant.granted_at>clock_timestamp()
      or v_grant.expires_at is not null and v_grant.expires_at<=clock_timestamp()
      or not (v_grant.categories @> array(select jsonb_array_elements_text(p_command.target->'categories')))
      or recipient_name is distinct from p_command.target->>'recipient_name'
      or v_grant.expires_at is distinct from (p_command.target->>'grant_expires_at')::timestamptz
      or to_jsonb(array(select c from unnest(v_grant.categories)c order by c)) is distinct from p_command.target->'consent_categories' then
    raise exception 'one current consent grant must still cover all reviewed sharing categories and the exact recipient' using errcode='P1001';
  end if;
  perform conversation.require_redacted(p_command.target->>'content');
  v_shared_at:=clock_timestamp();
  if v_grant.expires_at is not null and v_grant.expires_at<=v_shared_at then
    raise exception 'the reviewed consent expired before sharing' using errcode='P1001';
  end if;
  insert into rm.shared_summaries(customer_id,advisor_id,content,consent_grant_id,agenda_id,agenda_revision,command_id,shared_at)
    values(p_command.customer_id,agenda.advisor_id,p_command.target->>'content',v_grant.grant_id,agenda.agenda_id,agenda.revision,
      p_command.command_id,v_shared_at) returning * into summary;
  perform audit.record('action','action.summary_shared','success','rm.shared_summaries',summary.summary_id::text,
    jsonb_build_object('agenda_revision',summary.agenda_revision,'category_count',jsonb_array_length(p_command.target->'categories')),p_command.case_id);
  return jsonb_build_object('result',p_command.target||jsonb_build_object('summary_id',summary.summary_id,'shared_at',summary.shared_at,'status','shared'),
    'event_type','rm.summary_shared');
end $$;

insert into actions.executors(type,validator,executor,confirmation_required,normaliser,event_types) values
  ('rm.share_summary','actions.agenda_share_target(jsonb,bigint)'::regprocedure,'actions.execute_agenda_share(actions.commands)'::regprocedure,
    true,'actions.normalise_agenda_share(jsonb)'::regprocedure,array['rm.summary_shared']);

revoke all on function actions.normalise_agenda_share(jsonb),actions.agenda_share_target(jsonb,bigint),actions.execute_agenda_share(actions.commands)
  from public,anon,authenticated;
