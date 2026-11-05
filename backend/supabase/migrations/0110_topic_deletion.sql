-- Consent v2, part 2: topic deletion with undo (RM copilot build plan 6b; architecture §15.2). Synthetic data only.
--   1. insights.talking_points gains deleted_at, purge_after (7 days later) and deleted_reason: client (her Delete),
--      muted, chat_topics_revoked or scope_narrowed. A removed point is gone from every RM read at once and purged by the
--      worker after purge_after (events.purge_removed_topics, audited data.insight_purged with ids only; audit actions are
--      category-prefixed (0005), so the plan's insight.purged is recorded under data). A tombstone keeps the
--      point id, the dates and the reason, never the topic.
--   2. insight.delete removes softly (her quotes stay for the undo, never shown to the RM); insight.restore (automatic,
--      with a receipt) brings back a point she deleted herself within 7 days. Mute removes softly too.
--   3. Withdrawing chat_topics removes her inferred points softly and deletes their quotes at once (0108 deleted them);
--      narrowing her topic areas removes the inferred points outside them the same way. Recording stops: a point she
--      deleted is not inferred again before it is purged, and a topic outside her areas is not recorded.
--   4. Sensitive areas are never stored: a check on the topic's area as well as the topic list.
--   5. rm_point_status: for the brief re-check on the meeting side, whether a talking point an earlier brief cited is still
--      shown, was removed (and on which day) or is no longer shared. Ids and codes only.

-- 1. Columns, tombstones -----------------------------------------------------------------------------------------------
alter table insights.talking_points
  add column deleted_at     timestamptz,
  add column purge_after    timestamptz,
  add column deleted_reason text check (deleted_reason in ('client','muted','chat_topics_revoked','scope_narrowed')),
  add constraint talking_points_removed check ((deleted_at is null)=(purge_after is null) and (deleted_at is null)=(deleted_reason is null)),
  add constraint talking_points_area check (consent.topic_area(topic) is not null and not (consent.topic_area(topic)=any(consent.sensitive_areas())));
create index talking_points_purge_idx on insights.talking_points(purge_after) where deleted_at is not null;
create table insights.point_tombstones (
  point_id    uuid primary key,
  customer_id bigint not null references banking.customer_profiles(customer_id) on delete cascade,
  deleted_at  timestamptz not null,
  purged_at   timestamptz not null default clock_timestamp(),
  reason      text not null check (reason in ('client','muted','chat_topics_revoked','scope_narrowed'))
);
create index on insights.point_tombstones(customer_id);
alter table insights.point_tombstones enable row level security;
revoke all on insights.point_tombstones from public,anon,authenticated;

-- Removes her inferred points softly (all, or those outside p_areas) and deletes their quotes at once.
create function insights.remove_inferred(p_customer bigint,p_reason text,p_areas jsonb) returns int
language plpgsql volatile security definer set search_path='' as $$
declare quotes int; points int;
begin
  delete from insights.talking_point_evidence e using insights.talking_points p where e.point_id=p.point_id and p.customer_id=p_customer
    and p.origin='inferred' and (p_areas is null or not (p_areas ? consent.topic_area(p.topic)));
  get diagnostics quotes=row_count;
  update insights.talking_points set deleted_at=clock_timestamp(),purge_after=clock_timestamp()+interval '7 days',deleted_reason=p_reason
    where customer_id=p_customer and origin='inferred' and deleted_at is null and (p_areas is null or not (p_areas ? consent.topic_area(topic)));
  get diagnostics points=row_count;
  if quotes+points>0 then
    perform audit.record('data','data.talking_points_forgotten','success','insights.talking_points',p_customer::text,
      jsonb_build_object('points',points,'evidence',quotes)||case when p_areas is null then '{}'::jsonb else jsonb_build_object('reason',p_reason) end,
      p_customer_id=>p_customer);
  end if;
  return points;
end $$;
-- 0108's withdrawal, now soft: the points wait 7 days for the purge, the quotes (and every other quote of hers) go now.
create or replace function insights.forget_inferred(p_customer bigint) returns void language plpgsql volatile security definer set search_path='' as $$
declare quotes int; points int;
begin
  delete from insights.talking_point_evidence where customer_id=p_customer;
  get diagnostics quotes=row_count;
  update insights.talking_points set deleted_at=clock_timestamp(),purge_after=clock_timestamp()+interval '7 days',deleted_reason='chat_topics_revoked'
    where customer_id=p_customer and origin='inferred' and deleted_at is null;
  get diagnostics points=row_count;
  if quotes+points>0 then
    perform audit.record('data','data.talking_points_forgotten','success','insights.talking_points',p_customer::text,
      jsonb_build_object('points',points,'evidence',quotes),p_customer_id=>p_customer);
  end if;
end $$;
-- After any change to a chat_topics grant: none active any more forgets her inferred points; narrower topic areas remove
-- the inferred points outside them.
create or replace function insights.grant_changed() returns trigger language plpgsql security definer set search_path='' as $$
declare g consent.grants:=coalesce(new,old); areas jsonb;
begin
  if g.purpose='rm_brief' and ('chat_topics'=any(g.categories) or (tg_op='UPDATE' and 'chat_topics'=any(old.categories))) then
    if not insights.chat_topics_active(g.customer_id) then perform insights.forget_inferred(g.customer_id);
    else
      areas:=rm.consent_scope(g.customer_id,'chat_topics',insights.rm_of(g.customer_id))->'topic_areas';
      if areas is not null then perform insights.remove_inferred(g.customer_id,'scope_narrowed',areas); end if;
    end if;
  end if;
  return null;
end $$;
drop trigger forget_chat_topics on consent.grants;
create trigger forget_chat_topics after insert or update or delete on consent.grants for each row execute function insights.grant_changed();

-- 2. Recording -------------------------------------------------------------------------------------------------------
-- 0108's lane 1 with two more reasons: removed (she deleted this topic; nothing is inferred again before it is purged)
-- and out_of_scope (the topic's area is not one she chose). A point removed for any other reason is replaced.
create or replace function public.customer_record_talking_point(p_turn_id uuid,p_topic text,p_stage text,p_timeframe text,p_concerns text[],p_quote text)
returns jsonb language plpgsql volatile security definer set search_path='' as $$
declare t conversation.turns:=insights.require_turn(p_turn_id); cid bigint:=t.customer_id; message text; words text; reason text;
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
  words:=insights.mask_amounts(btrim(p_quote));
  if message is null or position(words in insights.mask_amounts(message))=0 then
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
    elsif p.origin='confirmed' then reason:='confirmed';  -- her own share stands as she gave it; inference never changes it
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
  insert into insights.talking_point_evidence(point_id,customer_id,turn_id,quote) values(p.point_id,cid,p_turn_id,words);
  update insights.talking_point_evidence e set quote=null where e.point_id=p.point_id and e.quote is not null and e.evidence_id not in (
    select x.evidence_id from insights.talking_point_evidence x where x.point_id=p.point_id and x.quote is not null order by x.recorded_at desc limit 3);
  perform audit.record('data','data.talking_point_recorded','success','insights.talking_points',p.point_id::text,
    jsonb_build_object('topic',p.topic,'stage',p.stage,'origin','inferred','created',created,'turn_id',p_turn_id),t.case_id,t.session_id);
  return jsonb_build_object('recorded',true,'reason',null,'point_id',p.point_id,'created',created,'stage',p.stage,'visible_from',p.visible_from);
end $$;

-- 3. Her controls ----------------------------------------------------------------------------------------------------
create or replace function actions.insight_delete_target(p_target jsonb,p_customer_id bigint) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
declare target jsonb:=actions.normalise_insight_point(p_target); p insights.talking_points;
begin
  select * into p from insights.talking_points where point_id=(target->>'point_id')::uuid and customer_id=p_customer_id;
  if not found then raise exception 'talking point is not available in this customer scope' using errcode='42501'; end if;
  if p.deleted_at is not null then raise exception 'this topic was already removed' using errcode='22023'; end if;
  return target||jsonb_build_object('topic',p.topic);
end $$;
create or replace function actions.execute_insight_delete(p_command actions.commands) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
declare until_ timestamptz;
begin
  update insights.talking_points set deleted_at=clock_timestamp(),purge_after=clock_timestamp()+interval '7 days',deleted_reason='client'
    where point_id=(p_command.target->>'point_id')::uuid and customer_id=p_command.customer_id and deleted_at is null returning purge_after into until_;
  if until_ is null then raise exception 'the talking point was already removed' using errcode='P1001'; end if;
  return jsonb_build_object('result',jsonb_build_object('point_id',p_command.target->>'point_id','topic',p_command.target->>'topic',
    'status','deleted','restore_until',until_),'event_type','insight.deleted');
end $$;

-- Undo within 7 days of her own Delete. An inferred point comes back only while chat_topics covers its area and the topic
-- is not muted.
create function actions.insight_restore_target(p_target jsonb,p_customer_id bigint) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
declare target jsonb:=actions.normalise_insight_point(p_target); p insights.talking_points; areas jsonb;
begin
  select * into p from insights.talking_points where point_id=(target->>'point_id')::uuid and customer_id=p_customer_id;
  if not found then raise exception 'talking point is not available in this customer scope' using errcode='42501'; end if;
  if p.deleted_reason is distinct from 'client' or p.purge_after<=clock_timestamp() then
    raise exception 'only a topic you deleted in the last 7 days can be brought back' using errcode='22023';
  end if;
  areas:=rm.consent_scope(p_customer_id,'chat_topics',insights.rm_of(p_customer_id))->'topic_areas';
  if p.origin='inferred' and (not insights.chat_topics_active(p_customer_id) or (areas is not null and not (areas ? consent.topic_area(p.topic)))
      or exists(select 1 from insights.topic_mutes m where m.customer_id=p_customer_id and m.topic=p.topic)) then
    raise exception 'this topic is no longer shared from your chats' using errcode='22023';
  end if;
  return target||jsonb_build_object('topic',p.topic);
end $$;
create function actions.execute_insight_restore(p_command actions.commands) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
declare restored uuid;
begin
  if actions.insight_restore_target(p_command.target,p_command.customer_id)<>p_command.target then
    raise exception 'the topic changed before it was restored' using errcode='P1001';
  end if;
  update insights.talking_points set deleted_at=null,purge_after=null,deleted_reason=null
    where point_id=(p_command.target->>'point_id')::uuid and customer_id=p_command.customer_id and deleted_reason='client' returning point_id into restored;
  if restored is null then raise exception 'the topic was purged' using errcode='P1001'; end if;
  return jsonb_build_object('result',jsonb_build_object('point_id',restored,'topic',p_command.target->>'topic','status','restored'),'event_type','insight.restored');
end $$;
insert into actions.executors(type,validator,executor,confirmation_required,normaliser,event_types) values
  ('insight.restore','actions.insight_restore_target(jsonb,bigint)'::regprocedure,'actions.execute_insight_restore(actions.commands)'::regprocedure,false,
    'actions.normalise_insight_point(jsonb)'::regprocedure,array['insight.restored']);

-- Mute: her inferred point for the topic is removed softly (purged with the others), never restored by Undo.
create or replace function actions.execute_insight_mute(p_command actions.commands) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
declare removed int;
begin
  insert into insights.topic_mutes(customer_id,topic,command_id) values(p_command.customer_id,p_command.target->>'topic',p_command.command_id)
    on conflict (customer_id,topic) do nothing;
  delete from insights.talking_point_evidence e using insights.talking_points p where e.point_id=p.point_id and p.customer_id=p_command.customer_id
    and p.topic=p_command.target->>'topic' and p.origin='inferred';
  update insights.talking_points set deleted_at=clock_timestamp(),purge_after=clock_timestamp()+interval '7 days',deleted_reason='muted'
    where customer_id=p_command.customer_id and topic=p_command.target->>'topic' and origin='inferred' and deleted_at is null;
  get diagnostics removed=row_count;
  return jsonb_build_object('result',jsonb_build_object('topic',p_command.target->>'topic','status','muted','removed',removed),'event_type','insight.muted');
end $$;

-- Sharing a topic she removed earlier replaces the removed point with what she confirms now.
create or replace function actions.execute_insight_share(p_command actions.commands) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
declare cid bigint:=p_command.customer_id; advisor uuid:=(p_command.target->>'advisor_id')::uuid; t jsonb:=p_command.target;
  p insights.talking_points; at timestamptz:=clock_timestamp(); notify_rm boolean;
begin
  perform pg_advisory_xact_lock(hashtext('insights.talking_points'),hashtext(cid::text));
  if not rm.in_book(advisor,cid) then raise exception 'the relationship manager assignment changed' using errcode='P1001'; end if;
  if insights.paused(cid) or exists(select 1 from insights.topic_mutes m where m.customer_id=cid and m.topic=t->>'topic') then
    raise exception 'suggestions for this topic were turned off' using errcode='P1001';
  end if;
  select * into p from insights.talking_points where customer_id=cid and topic=t->>'topic' for update;
  if p.point_id is not null and (p.expires_at<=at or p.deleted_at is not null) then delete from insights.talking_points where point_id=p.point_id; p:=null; end if;
  if p.point_id is null then
    insert into insights.talking_points(customer_id,topic,stage,timeframe,origin,first_seen,last_seen,visible_from,expires_at,share_command_id)
      values(cid,t->>'topic',t->>'stage',t->>'timeframe','confirmed',at,at,at,at+interval '90 days',p_command.command_id) returning * into p;
  else
    update insights.talking_points set
      stage=case when origin='confirmed' and insights.stage_rank(stage)>insights.stage_rank(t->>'stage') then stage else t->>'stage' end,
      timeframe=case when origin='confirmed' and t->>'timeframe'='unknown' then timeframe else t->>'timeframe' end,
      concerns=case when origin='confirmed' then concerns else '{}' end,first_seen=case when origin='confirmed' then first_seen else at end,
      origin='confirmed',last_seen=at,visible_from=at,expires_at=at+interval '90 days',share_command_id=p_command.command_id
      where point_id=p.point_id returning * into p;
  end if;
  perform pg_advisory_xact_lock(hashtext('rm.topic_shares'),hashtext(advisor::text));
  notify_rm:=(select count(*) from rm.topic_shares s where s.advisor_id=advisor and s.notice
    and s.created_at>=(rm.today()::timestamp at time zone 'Asia/Singapore'))<20;
  insert into rm.topic_shares(advisor_id,customer_id,point_id,command_id,notice) values(advisor,cid,p.point_id,p_command.command_id,notify_rm);
  return jsonb_build_object('result',jsonb_build_object('point_id',p.point_id,'topic',p.topic,'stage',p.stage,'timeframe',p.timeframe,
    'origin','confirmed','notified',notify_rm),'event_type','insight.shared');
end $$;

alter function notify.deliver(events.outbox) rename to deliver_before_topic_restore;
revoke all on function notify.deliver_before_topic_restore(events.outbox) from public,anon,authenticated;
create function notify.deliver(p_event events.outbox) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
begin
  if p_event.event_type='insight.restored' then return jsonb_build_object('disposition','suppressed','notification_id',null,'available_at',null); end if;
  return notify.deliver_before_topic_restore(p_event);
end $$;
revoke all on function notify.deliver(events.outbox) from public,anon,authenticated;

-- 4. Purge -------------------------------------------------------------------------------------------------------------
-- The worker's job (app/worker.py, every minute): removed points past purge_after go for good with their quotes and
-- Today shares; a tombstone keeps the id, the dates and the reason for rm_point_status. Audited per point, ids only.
create function events.purge_removed_topics() returns int language plpgsql volatile security definer set search_path='' as $$
declare p insights.talking_points; n int:=0;
begin
  for p in delete from insights.talking_points where point_id in (select point_id from insights.talking_points
      where deleted_at is not null and purge_after<=clock_timestamp() order by purge_after limit 200 for update skip locked) returning * loop
    insert into insights.point_tombstones(point_id,customer_id,deleted_at,reason) values(p.point_id,p.customer_id,p.deleted_at,p.deleted_reason)
      on conflict (point_id) do nothing;
    perform audit.record('data','data.insight_purged','success','insights.talking_points',p.point_id::text,jsonb_build_object('reason',p.deleted_reason),
      p_customer_id=>p.customer_id,p_actor_type=>'system',p_actor_id=>'purge_worker');
    n:=n+1;
  end loop;
  return n;
end $$;
revoke all on function events.purge_removed_topics() from public,anon,authenticated;
grant execute on function events.purge_removed_topics() to demobank_worker;

-- 5. Reads -------------------------------------------------------------------------------------------------------------
-- 0109's reader without removed points.
create or replace function public.rm_talking_points(p_customer_id bigint,p_purpose text) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
declare advisor uuid:=rm.current_advisor(); g uuid; paused boolean; points jsonb; commands jsonb; pending boolean; r jsonb; areas jsonb;
begin
  if p_purpose is null or p_purpose not in ('meeting_prep','client_view') then raise exception 'purpose must be meeting_prep or client_view' using errcode='22023'; end if;
  if not rm.in_book(advisor,p_customer_id) then return rm.deny(p_customer_id,'rm.talking_points'); end if;
  g:=rm.grant_for(p_customer_id,'chat_topics',advisor);
  areas:=rm.consent_scope(p_customer_id,'chat_topics',advisor)->'topic_areas';
  paused:=insights.paused(p_customer_id);
  select coalesce(jsonb_agg(insights.point_json(p) order by p.origin desc,p.last_seen desc),'[]'),
      coalesce(jsonb_agg(p.share_command_id) filter (where p.share_command_id is not null),'[]')
    into points,commands from insights.talking_points p
    where p.customer_id=p_customer_id and p.expires_at>clock_timestamp() and p.deleted_at is null
      and (p.origin='confirmed' or (g is not null and not paused and p.visible_from<=clock_timestamp() and (areas is null or areas ? consent.topic_area(p.topic))));
  if g is null then
    r:=rm.gated(p_customer_id,'rm.talking_points','rm.talking_points','chat_topics',null,p_purpose,null);
    if jsonb_array_length(points)>0 then
      perform audit.record('data','data.read','success','insights.talking_points',r->>'call_id',
        jsonb_build_object('tool','rm.talking_points','result_count',jsonb_array_length(points),'basis','client_shared',
          'command_ids',commands,'purpose',p_purpose),p_actor_type=>'staff',p_actor_id=>auth.uid()::text,p_customer_id=>p_customer_id);
    end if;
    return r||jsonb_build_object('data',points,'pending',false);
  end if;
  pending:=exists(select 1 from insights.talking_points p where p.customer_id=p_customer_id and p.origin='inferred' and p.deleted_at is null
    and p.expires_at>clock_timestamp() and (paused or p.visible_from>clock_timestamp()) and (areas is null or areas ? consent.topic_area(p.topic)));
  return rm.gated(p_customer_id,'rm.talking_points','rm.talking_points','chat_topics',g,p_purpose,points)||jsonb_build_object('pending',pending);
end $$;

-- 0108's Today list without removed points.
create or replace function public.rm_shared_topics() returns jsonb language plpgsql volatile security definer set search_path='' as $$
declare advisor uuid:=rm.current_advisor(); call_id uuid:=gen_random_uuid(); data jsonb; commands jsonb;
begin
  with latest as (
    select distinct on (s.point_id) s.*,p as point from rm.topic_shares s join insights.talking_points p using(point_id)
    where s.advisor_id=advisor and rm.in_book(advisor,s.customer_id) and p.origin='confirmed' and p.deleted_at is null and p.expires_at>clock_timestamp()
      and s.created_at>clock_timestamp()-interval '30 days' order by s.point_id,s.created_at desc),
  shown as (select * from latest order by created_at desc limit 20)
  select coalesce(jsonb_agg(jsonb_build_object('share_id',x.share_id,'customer_id',x.customer_id,'client',rm.display_name(c.full_name),
      'notice',case when x.notice then rm.display_name(c.full_name)||' shared a topic with you. Open your workspace.' end,
      'shared_at',x.created_at,'shared_text',rm.sgt(x.created_at),'point',insights.point_json(x.point)) order by x.created_at desc),'[]'),
    coalesce(jsonb_agg(x.command_id) filter (where x.command_id is not null),'[]')
    into data,commands from shown x join banking.customer_profiles c using(customer_id);
  perform audit.record('data','data.read','success','rm.topic_shares',call_id::text,jsonb_build_object('tool','rm.shared_topics',
    'result_count',jsonb_array_length(data),'basis','client_shared','command_ids',commands),p_actor_type=>'staff',p_actor_id=>auth.uid()::text);
  return jsonb_build_object('denied',false,'customer_id',null,'call_id',call_id,'as_of',clock_timestamp(),'data',data);
end $$;

-- 0108's own view, with the points she removed in the last 7 days (and whether Undo can bring each back).
create or replace function public.customer_read_talking_points(p_customer_id bigint) returns jsonb
language plpgsql volatile security invoker set search_path='' as $$
declare cid bigint:=banking.require_read_customer(p_customer_id); data jsonb;
begin
  perform insights.tidy();
  select jsonb_build_object(
    'points',(select coalesce(jsonb_agg(insights.point_json(p)||jsonb_build_object('visible_from',p.visible_from,
        'pending',p.origin='inferred' and p.visible_from>clock_timestamp(),'evidence',insights.my_evidence(p.point_id)) order by p.last_seen desc),'[]')
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

-- For the meeting side's brief re-check: for each point id an earlier brief cited (1-50), shown (still in this RM's
-- reads), removed (with the day; also after the purge), not_shared (exists, no longer in her reads) or unknown. One client
-- per call, her book only, audited with counts.
create function public.rm_point_status(p_customer_id bigint,p_point_ids uuid[]) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
declare advisor uuid:=rm.current_advisor(); g uuid; areas jsonb; paused boolean; data jsonb;
begin
  if p_point_ids is null or cardinality(p_point_ids) not between 1 and 50 then raise exception 'one to 50 point ids' using errcode='22023'; end if;
  if not rm.in_book(advisor,p_customer_id) then return rm.deny(p_customer_id,'rm.point_status'); end if;
  g:=rm.grant_for(p_customer_id,'chat_topics',advisor);
  areas:=rm.consent_scope(p_customer_id,'chat_topics',advisor)->'topic_areas';
  paused:=insights.paused(p_customer_id);
  select coalesce(jsonb_agg(jsonb_build_object('point_id',i.id,'status',case
      when p.point_id is not null and p.deleted_at is not null then 'removed'
      when p.point_id is null and t.point_id is not null then 'removed'
      when p.point_id is null or p.expires_at<=clock_timestamp() then 'unknown'
      when p.origin='confirmed' or (g is not null and not paused and p.visible_from<=clock_timestamp() and (areas is null or areas ? consent.topic_area(p.topic))) then 'shown'
      else 'not_shared' end,
    'removed_on',((coalesce(p.deleted_at,t.deleted_at)) at time zone 'Asia/Singapore')::date) order by i.n),'[]') into data
  from unnest(p_point_ids) with ordinality i(id,n)
    left join insights.talking_points p on p.point_id=i.id and p.customer_id=p_customer_id
    left join insights.point_tombstones t on t.point_id=i.id and t.customer_id=p_customer_id;
  return rm.result(p_customer_id,'rm.point_status','insights.talking_points',data);
end $$;
revoke all on function public.rm_point_status(bigint,uuid[]) from public,anon;
grant execute on function public.rm_point_status(bigint,uuid[]) to authenticated;

-- Grants --------------------------------------------------------------------------------------------------------------
revoke all on function insights.remove_inferred(bigint,text,jsonb) from public,anon,authenticated;
revoke all on function actions.insight_restore_target(jsonb,bigint),actions.execute_insight_restore(actions.commands) from public,anon,authenticated;
