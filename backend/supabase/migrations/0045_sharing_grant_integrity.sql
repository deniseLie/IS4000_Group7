-- Reject incompatible legacy grant metadata before a proposal can enter the durable ledger.
create or replace function actions.agenda_share_target(p_target jsonb,p_customer_id bigint)
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
  if cardinality(v_grant.categories) not between 1 and 7
      or (select count(distinct c) from unnest(v_grant.categories)c)<>cardinality(v_grant.categories)
      or exists(select 1 from unnest(v_grant.categories)c where c is null or c not in
        ('profile','agenda','accounts','transactions','cards','loans','compliance')) then
    raise exception 'the consent grant has invalid category metadata; record a new supported consent request' using errcode='22023';
  end if;
  return target||jsonb_build_object('agenda_command_id',source.command_id,'recipient_name',recipient_name,
    'content',source.target->>'content','profile_fields',source.target->'profile_fields','categories',source.target->'categories',
    'consent_categories',(select jsonb_agg(c order by c) from unnest(v_grant.categories)c),'grant_expires_at',v_grant.expires_at);
end $$;
