-- Existing DemoBank project: apply only after reviewing hosted schema and RLS.
begin;

set local lock_timeout='10s';
set local statement_timeout='120s';

select pg_advisory_xact_lock(hashtext('demobank.hosted_migrations'));

do $apply_0142$
declare previous supabase_migrations.schema_migrations; script text:=$source_0142$-- Released source SHA256: 5cde10b16fd13117775400080efc83011a9b6776ebb82867f69104ab23673e04
-- Talking points keep no quotes (Data sharing redesign, 8 Nov 2026). Synthetic data only.
-- Since 0108 each inferred point kept up to three of her messages' exact words as evidence, shown only to her under
-- "Why we think this". That panel is gone: the client sees each topic in plain words and the RM sees list values only,
-- so nobody reads the words and they are no longer kept.
--   1. customer_record_talking_point still requires the quote to be her own words from this turn (0108's check), then
--      keeps only the turn and the time: the evidence row stays for idempotency (one point per turn and topic).
--   2. customer_read_talking_points no longer returns evidence; insights.my_evidence is dropped.
--   3. The demo seed keeps Michelle's evidence row without words.
--   4. The quote column is dropped, with every quote already stored.

-- 1. Recording ---------------------------------------------------------------------------------------------------------
-- 0110's lane 1, keeping no words.
create or replace function public.customer_record_talking_point(p_turn_id uuid,p_topic text,p_stage text,p_timeframe text,p_concerns text[],p_quote text)
returns jsonb language plpgsql volatile security definer set search_path='' as $$
declare t conversation.turns:=insights.require_turn(p_turn_id); cid bigint:=t.customer_id; message text; reason text;
  p insights.talking_points; created boolean:=false; at timestamptz:=clock_timestamp(); areas jsonb;
begin
  if p_topic is null or not (p_topic=any(insights.topics())) or insights.stage_rank(p_stage) is null
      or p_timeframe is null or p_timeframe not in ('now','within_3m','within_12m','later','unknown') or not insights.valid_concerns(coalesce(p_concerns,'{}'))
      or p_quote is null or length(btrim(p_quote)) not between 1 and 200 then
    raise exception 'a talking point needs a listed topic, stage, timeframe and concerns and a quote of 1-200 characters' using errcode='22023';
  end if;
  if consent.topic_area(p_topic) is null or consent.topic_area(p_topic)=any(consent.sensitive_areas()) then
    raise exception 'this topic is never kept' using errcode='22023';
  end if;
  select m.redacted_text into message from conversation.messages m where m.message_id=t.user_message_id and m.customer_id=cid;
  if message is null or position(insights.mask_amounts(btrim(p_quote)) in insights.mask_amounts(message))=0 then
    raise exception 'the quote must be the client''s own words from this turn' using errcode='22023';
  end if;
  perform pg_advisory_xact_lock(hashtext('insights.talking_points'),hashtext(cid::text));
  areas:=rm.consent_scope(cid,'chat_topics',insights.rm_of(cid))->'topic_areas';
  reason:=case when not insights.chat_topics_active(cid) then 'no_consent' when insights.paused(cid) then 'paused'
    when exists(select 1 from insights.topic_mutes where customer_id=cid and topic=p_topic) then 'muted'
    when areas is not null and not (areas ? consent.topic_area(p_topic)) then 'out_of_scope' end;
  if reason is null then
    select * into p from insights.talking_points where customer_id=cid and topic=p_topic for update;
    if p.point_id is not null and (p.expires_at<=at or (p.deleted_at is not null and p.deleted_reason<>'client')) then
      delete from insights.talking_points where point_id=p.point_id; p:=null;
    end if;
    if p.deleted_at is not null then reason:='removed';
    elsif p.origin='confirmed' then reason:='confirmed';
    elsif exists(select 1 from insights.talking_point_evidence where point_id=p.point_id and turn_id=p_turn_id) then reason:='duplicate';
    end if;
  end if;
  if reason is not null then
    perform audit.record('data','data.talking_point_skipped','success','conversation.turns',p_turn_id::text,
      jsonb_build_object('topic',p_topic,'reason',reason),t.case_id,t.session_id);
    return jsonb_build_object('recorded',false,'reason',reason,'point_id',p.point_id);
  end if;
  if p.point_id is null then
    insert into insights.talking_points(customer_id,topic,stage,timeframe,concerns,origin,first_seen,last_seen,visible_from,expires_at)
      values(cid,p_topic,p_stage,p_timeframe,(select coalesce(array_agg(c order by c),'{}') from unnest(coalesce(p_concerns,'{}')) c),'inferred',
        at,at,at+interval '24 hours',at+interval '90 days') returning * into p;
    created:=true;
  else
    update insights.talking_points set stage=case when insights.stage_rank(p_stage)>insights.stage_rank(stage) then p_stage else stage end,
      timeframe=case when p_timeframe='unknown' then timeframe else p_timeframe end,
      concerns=(select coalesce(array_agg(distinct c order by c),'{}') from unnest(concerns||coalesce(p_concerns,'{}')) c),
      last_seen=at,expires_at=at+interval '90 days' where point_id=p.point_id returning * into p;
  end if;
  insert into insights.talking_point_evidence(point_id,customer_id,turn_id) values(p.point_id,cid,p_turn_id);
  perform audit.record('data','data.talking_point_recorded','success','insights.talking_points',p.point_id::text,
    jsonb_build_object('topic',p.topic,'stage',p.stage,'origin','inferred','created',created,'turn_id',p_turn_id),t.case_id,t.session_id);
  return jsonb_build_object('recorded',true,'reason',null,'point_id',p.point_id,'created',created,'stage',p.stage,'visible_from',p.visible_from);
end $$;

-- 2. Her view -----------------------------------------------------------------------------------------------------------
-- 0110's view without evidence.
create or replace function public.customer_read_talking_points(p_customer_id bigint) returns jsonb
language plpgsql volatile security invoker set search_path='' as $$
declare cid bigint:=banking.require_read_customer(p_customer_id); data jsonb;
begin
  perform insights.tidy();
  select jsonb_build_object(
    'points',(select coalesce(jsonb_agg(insights.point_json(p)||jsonb_build_object('visible_from',p.visible_from,
        'pending',p.origin='inferred' and p.visible_from>clock_timestamp()) order by p.last_seen desc),'[]')
      from insights.talking_points p where p.customer_id=cid and p.deleted_at is null),
    'removed',(select coalesce(jsonb_agg(insights.point_json(p)||jsonb_build_object('removed_at',p.deleted_at,'reason',p.deleted_reason,
        'restore_until',case when p.deleted_reason='client' then p.purge_after end,'can_restore',p.deleted_reason='client' and p.purge_after>clock_timestamp())
        order by p.deleted_at desc),'[]')
      from insights.talking_points p where p.customer_id=cid and p.deleted_at is not null),
    'muted',(select coalesce(jsonb_agg(m.topic order by m.topic),'[]') from insights.topic_mutes m where m.customer_id=cid),
    'pause',(select jsonb_build_object('pause_id',x.pause_id,'until',x.until) from insights.opportunity_pauses x
      where x.customer_id=cid and x.ended_at is null and x.until>clock_timestamp() order by x.until desc limit 1),
    'chat_topics',insights.my_chat_topics(),'rm_first_name',insights.my_rm_first_name()) into data;
  return banking.customer_result(cid,'insights.talking_points','insights.talking_points',data);
end $$;
drop function insights.my_evidence(uuid);

-- 3. Demo seed ----------------------------------------------------------------------------------------------------------
-- 0108's seed: Michelle's inferred home loan point past its hold (its evidence row has no words now), Mei Ling's
-- confirmed investments point on Sophie's Today.
create or replace function insights.seed_demo_points(p_customers bigint[]) returns int language plpgsql volatile set search_path='' as $$
declare sophie uuid:='00000000-0000-4000-8000-100200000002'; at timestamptz:=clock_timestamp(); n int:=0;
begin
  if 1002=any(p_customers) then
    insert into insights.talking_points(point_id,customer_id,topic,stage,timeframe,origin,first_seen,last_seen,visible_from,expires_at)
      values('00000000-0000-4000-8000-100200000091',1002,'home_loan','planning','within_12m','inferred',at-interval '3 days',at-interval '3 days',
        at-interval '2 days',at+interval '87 days');
    insert into insights.talking_point_evidence(point_id,customer_id,recorded_at)
      values('00000000-0000-4000-8000-100200000091',1002,at-interval '3 days');
    n:=n+1;
  end if;
  if 1007=any(p_customers) and rm.in_book(sophie,1007) then
    insert into insights.talking_points(point_id,customer_id,topic,stage,timeframe,origin,first_seen,last_seen,visible_from,expires_at)
      values('00000000-0000-4000-8000-100700000091',1007,'investments','exploring','within_3m','confirmed',at-interval '1 day',at-interval '1 day',
        at-interval '1 day',at+interval '89 days');
    insert into rm.topic_shares(advisor_id,customer_id,point_id,notice,created_at)
      values(sophie,1007,'00000000-0000-4000-8000-100700000091',true,at-interval '1 day');
    n:=n+1;
  end if;
  return n;
end $$;
revoke all on function insights.seed_demo_points(bigint[]) from public,anon,authenticated;

-- 4. No words kept ------------------------------------------------------------------------------------------------------
alter table insights.talking_point_evidence drop column quote;
comment on table insights.talking_point_evidence is
  'The saved chat turns a point was inferred from (turn and time only; since 0142 no words). No RM role reads it.';
$source_0142$;
begin
  select * into previous from supabase_migrations.schema_migrations where version='0142';
  if found then
    if previous.name is distinct from 'no_topic_quotes' or previous.statements is distinct from array[script] then
      raise exception 'migration 0142 is recorded with different SQL; inspect history before proceeding';
    end if;
    raise notice 'migration 0142 already applied with identical SQL';
  else
    execute script;
    insert into supabase_migrations.schema_migrations(version,name,statements)
      values('0142','no_topic_quotes',array[script]);
  end if;
end $apply_0142$;

commit;
select version,name from supabase_migrations.schema_migrations order by version;
