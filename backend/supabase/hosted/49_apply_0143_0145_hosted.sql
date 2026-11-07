-- Existing DemoBank project: apply only after reviewing hosted schema and RLS.
begin;

set local lock_timeout='10s';
set local statement_timeout='120s';

select pg_advisory_xact_lock(hashtext('demobank.hosted_migrations'));

do $apply_0143$
declare previous supabase_migrations.schema_migrations; script text:=$source_0143$-- Released source SHA256: 2bc7c23b517f8eb84fbf3dd26c6a82fa5f498f263bbe6c3953a5a3844f865fd1
-- Talking points that follow the conversation (lane 1 v2; docs/planning/stateful-talking-points.md). Synthetic data only.
-- The classifier now sees her current points and her last few messages in this chat, and proposes add, update or on
-- hold for each topic; services/talking_points_state.py checks every change and this file checks it again.
--   1. insights.talking_points gains kind (the topic's list in insights.kinds), on_hold_at and on_hold_turn_id; four more
--      concerns (lock_in, down_payment, tenure, risk), still at most 4 per point (the newest kept: insights.merge_concerns).
--   2. customer_topic_state: what customer_topic_status returns, plus her current points (list values only) and the
--      topics active in this chat (recorded from one of her last 4 turns before this one). Never her words.
--   3. customer_apply_talking_point: one change for one of her saved turns. Same checks as 0142's recording (listed
--      values, her own words from this turn, consent, pause, mute, topic area, removed, confirmed), plus the kind for the
--      topic, an update or on hold only for a point she has, and a carry-over (a change whose quote has no topic word)
--      only for a topic active in this chat. Stage only moves up; the latest timeframe and kind win (unknown and null
--      never overwrite); on hold hides the point until her next add or update for that topic. The model never deletes.
--   4. Reads with kind and the on-hold rule: an inferred point on hold is not shown to the RM, does not count as
--      pending and is not_shared in rm_point_status; she still sees it on her page, marked on hold.
--   5. 0142's customer_record_talking_point stays for v1 (switching back is one setting), now capping concerns at 4
--      and clearing on hold like any later mention; sharing a topic herself replaces an inferred kind and on hold.

-- 1. Columns and lists -------------------------------------------------------------------------------------------------
create function insights.kinds(p_topic text) returns text[] language sql immutable set search_path='' as $$
  select case p_topic when 'home_loan' then array['hdb_bto','hdb_resale','private_property','landed','refinance']
    when 'investments' then array['etf','unit_trust','stocks','bonds','robo'] when 'fixed_deposit' then array['new_deposit','renewal']
    when 'insurance' then array['life','home','travel'] else '{}'::text[] end
$$;  -- = schemas/meetings.KINDS; no health cover (a sensitive area)
create or replace function insights.valid_concerns(p text[]) returns boolean language sql immutable set search_path='' as $$
  select p is not null and cardinality(p)<=4 and p<@array['rates','fees','eligibility','timing','lock_in','down_payment','tenure','risk']
    and (select count(distinct c) from unnest(p) c)=cardinality(p)
$$;  -- = schemas/meetings.ConcernTag
-- Her newest concerns first, each once, at most 4, stored in order (services/talking_points_state.merge_concerns).
create function insights.merge_concerns(p_old text[],p_new text[]) returns text[] language sql immutable set search_path='' as $$
  select coalesce(array_agg(c order by c),'{}') from (
    select c from (select c,min(n) n from unnest(coalesce(p_new,'{}')||coalesce(p_old,'{}')) with ordinality x(c,n) group by c) d
    order by n limit 4) k
$$;
alter table insights.talking_points
  add column kind            text,
  add column on_hold_at      timestamptz,  -- she said the plan is off: hidden from the RM until her next mention
  add column on_hold_turn_id uuid references conversation.turns(turn_id) on delete set null,
  add constraint talking_points_kind check (kind is null or kind=any(insights.kinds(topic))),
  add constraint talking_points_on_hold check (on_hold_at is not null or on_hold_turn_id is null);

-- The one RM-side rendering of a point (0108), with the kind: exactly the schemas/meetings.TalkingPoint fields.
create or replace function insights.point_json(p insights.talking_points) returns jsonb language sql stable set search_path='' as $$
  select jsonb_build_object('point_id',p.point_id,'topic',p.topic,'stage',p.stage,'timeframe',p.timeframe,'kind',p.kind,'concerns',to_jsonb(p.concerns),
    'origin',p.origin,'first_seen',(p.first_seen at time zone 'Asia/Singapore')::date,'last_seen',(p.last_seen at time zone 'Asia/Singapore')::date)
$$;

-- 2. What the classifier reads ---------------------------------------------------------------------------------------
-- Topics recorded from one of her last 4 turns in this chat before this turn (the carry-over window). A new chat starts
-- with none; a removed or expired point is not active.
create function insights.active_topics(p_turn conversation.turns) returns text[] language sql stable security definer set search_path='' as $$
  select coalesce(array_agg(distinct p.topic order by p.topic),'{}')
  from (select t.turn_id from conversation.turns t where t.session_id=p_turn.session_id and t.customer_id=p_turn.customer_id
          and t.turn_id<>p_turn.turn_id and t.created_at<=p_turn.created_at order by t.created_at desc limit 4) recent
    join insights.talking_point_evidence e on e.turn_id=recent.turn_id
    join insights.talking_points p on p.point_id=e.point_id
  where p.customer_id=p_turn.customer_id and p.deleted_at is null and p.expires_at>clock_timestamp()
$$;
-- 0108's status fields, shared by customer_topic_status and customer_topic_state (one audited read each).
create function insights.status_json(p_customer bigint) returns jsonb language sql stable security definer set search_path='' as $$
  select jsonb_build_object('chat_topics',insights.chat_topics_active(p_customer),'paused',insights.paused(p_customer),
    'has_rm',insights.rm_of(p_customer) is not null,'rm_first_name',insights.first_name(insights.rm_of(p_customer)),
    'muted',(select coalesce(jsonb_agg(m.topic order by m.topic),'[]') from insights.topic_mutes m where m.customer_id=p_customer),
    'prompted',(select coalesce(jsonb_agg(s.topic order by s.topic),'[]') from insights.share_prompts s
      where s.customer_id=p_customer and s.offered_at>clock_timestamp()-interval '30 days'),
    'meeting_topics',(select coalesce(jsonb_agg(distinct x),'[]') from rm.meeting_requests r,unnest(r.topics) x
      where r.customer_id=p_customer and r.status in ('requested','countered')))
$$;
create or replace function public.customer_topic_status(p_customer_id bigint) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
declare cid bigint:=banking.require_read_customer(p_customer_id);
begin
  return banking.customer_result(cid,'insights.status','insights.talking_points',insights.status_json(cid));
end $$;
-- Lane 1 v2's one read before a turn is classified: the status, her points (list values only: no dates, ids or words)
-- and the topics active in this chat.
create function public.customer_topic_state(p_customer_id bigint,p_turn_id uuid) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
declare cid bigint:=banking.require_read_customer(p_customer_id); t conversation.turns:=insights.require_turn(p_turn_id);
begin
  return banking.customer_result(cid,'insights.status','insights.talking_points',insights.status_json(cid)||jsonb_build_object(
    'points',(select coalesce(jsonb_agg(jsonb_build_object('topic',p.topic,'stage',p.stage,'timeframe',p.timeframe,'kind',p.kind,
        'concerns',to_jsonb(p.concerns),'origin',p.origin,'on_hold',p.on_hold_at is not null) order by p.topic),'[]')
      from insights.talking_points p where p.customer_id=cid and p.deleted_at is null and p.expires_at>clock_timestamp()),
    'active_topics',to_jsonb(insights.active_topics(t))));
end $$;

-- 3. Applying one change ---------------------------------------------------------------------------------------------
create function public.customer_apply_talking_point(p_turn_id uuid,p_action text,p_topic text,p_stage text,p_timeframe text,p_kind text,
  p_concerns text[],p_quote text,p_carried boolean)
returns jsonb language plpgsql volatile security definer set search_path='' as $$
declare t conversation.turns:=insights.require_turn(p_turn_id); cid bigint:=t.customer_id; message text; reason text;
  p insights.talking_points; created boolean:=false; at timestamptz:=clock_timestamp(); areas jsonb;
begin
  if p_action is null or p_action not in ('add','update','on_hold') or p_topic is null or not (p_topic=any(insights.topics()))
      or insights.stage_rank(p_stage) is null or p_timeframe is null or p_timeframe not in ('now','within_3m','within_12m','later','unknown')
      or (p_kind is not null and not (p_kind=any(insights.kinds(p_topic)))) or not insights.valid_concerns(coalesce(p_concerns,'{}'))
      or p_quote is null or length(btrim(p_quote)) not between 1 and 200 or p_carried is null then
    raise exception 'a change needs add, update or on_hold, a listed topic, stage, timeframe, kind and concerns and a quote of 1-200 characters'
      using errcode='22023';
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
    when areas is not null and not (areas ? consent.topic_area(p_topic)) then 'out_of_scope'
    when p_carried and not (p_topic=any(insights.active_topics(t))) then 'not_active' end;
  if reason is null then
    select * into p from insights.talking_points where customer_id=cid and topic=p_topic for update;
    if p.point_id is not null and (p.expires_at<=at or (p.deleted_at is not null and p.deleted_reason<>'client')) then
      delete from insights.talking_points where point_id=p.point_id; p:=null;
    end if;
    if p.deleted_at is not null then reason:='removed';
    elsif p.origin='confirmed' then reason:='confirmed';  -- her own share stands as she gave it; inference never changes it
    elsif p.point_id is null and p_action<>'add' then reason:='no_point';
    elsif exists(select 1 from insights.talking_point_evidence where point_id=p.point_id and turn_id=p_turn_id) then reason:='duplicate';
    end if;
  end if;
  if reason is not null then
    perform audit.record('data','data.talking_point_skipped','success','conversation.turns',p_turn_id::text,
      jsonb_build_object('topic',p_topic,'reason',reason,'action',p_action),t.case_id,t.session_id);
    return jsonb_build_object('recorded',false,'reason',reason,'point_id',p.point_id);
  end if;
  if p.point_id is null then
    insert into insights.talking_points(customer_id,topic,stage,timeframe,kind,concerns,origin,first_seen,last_seen,visible_from,expires_at)
      values(cid,p_topic,p_stage,p_timeframe,p_kind,insights.merge_concerns('{}',p_concerns),'inferred',at,at,at+interval '24 hours',at+interval '90 days')
      returning * into p;
    created:=true;
  elsif p_action='on_hold' then
    update insights.talking_points set on_hold_at=at,on_hold_turn_id=p_turn_id,last_seen=at,expires_at=at+interval '90 days'
      where point_id=p.point_id returning * into p;
  else
    update insights.talking_points set stage=case when insights.stage_rank(p_stage)>insights.stage_rank(stage) then p_stage else stage end,
      timeframe=case when p_timeframe='unknown' then timeframe else p_timeframe end,kind=coalesce(p_kind,kind),
      concerns=insights.merge_concerns(concerns,p_concerns),on_hold_at=null,on_hold_turn_id=null,last_seen=at,expires_at=at+interval '90 days'
      where point_id=p.point_id returning * into p;
  end if;
  insert into insights.talking_point_evidence(point_id,customer_id,turn_id) values(p.point_id,cid,p_turn_id);
  if p_action='on_hold' then
    perform audit.record('data','data.talking_point_on_hold','success','insights.talking_points',p.point_id::text,
      jsonb_build_object('topic',p.topic,'turn_id',p_turn_id,'carried',p_carried),t.case_id,t.session_id);
  else
    perform audit.record('data','data.talking_point_recorded','success','insights.talking_points',p.point_id::text,
      jsonb_build_object('topic',p.topic,'stage',p.stage,'origin','inferred','created',created,'turn_id',p_turn_id,'action',p_action,
        'carried',p_carried),t.case_id,t.session_id);
  end if;
  return jsonb_build_object('recorded',true,'reason',null,'point_id',p.point_id,'created',created,'stage',p.stage,'kind',p.kind,
    'on_hold',p.on_hold_at is not null,'visible_from',p.visible_from);
end $$;

-- 5. v1 and lane 2 with the new columns ------------------------------------------------------------------------------
-- 0142's v1 recording: concerns capped at 4 (four more exist now) and a mention clears on hold, as in v2.
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
      values(cid,p_topic,p_stage,p_timeframe,insights.merge_concerns('{}',p_concerns),'inferred',at,at,at+interval '24 hours',at+interval '90 days')
      returning * into p;
    created:=true;
  else
    update insights.talking_points set stage=case when insights.stage_rank(p_stage)>insights.stage_rank(stage) then p_stage else stage end,
      timeframe=case when p_timeframe='unknown' then timeframe else p_timeframe end,concerns=insights.merge_concerns(concerns,p_concerns),
      on_hold_at=null,on_hold_turn_id=null,last_seen=at,expires_at=at+interval '90 days' where point_id=p.point_id returning * into p;
  end if;
  insert into insights.talking_point_evidence(point_id,customer_id,turn_id) values(p.point_id,cid,p_turn_id);
  perform audit.record('data','data.talking_point_recorded','success','insights.talking_points',p.point_id::text,
    jsonb_build_object('topic',p.topic,'stage',p.stage,'origin','inferred','created',created,'turn_id',p_turn_id),t.case_id,t.session_id);
  return jsonb_build_object('recorded',true,'reason',null,'point_id',p.point_id,'created',created,'stage',p.stage,'visible_from',p.visible_from);
end $$;

-- 0110's share: what she confirms replaces an inferred point's kind and on hold too (only her words reach the RM).
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
      kind=case when origin='confirmed' then kind end,concerns=case when origin='confirmed' then concerns else '{}' end,
      first_seen=case when origin='confirmed' then first_seen else at end,on_hold_at=null,on_hold_turn_id=null,
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

-- 4. Reads -------------------------------------------------------------------------------------------------------------
-- 0110's reader: an inferred point on hold is neither shown nor pending.
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
      and (p.origin='confirmed' or (g is not null and not paused and p.on_hold_at is null and p.visible_from<=clock_timestamp()
        and (areas is null or areas ? consent.topic_area(p.topic))));
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
    and p.on_hold_at is null and p.expires_at>clock_timestamp() and (paused or p.visible_from>clock_timestamp())
    and (areas is null or areas ? consent.topic_area(p.topic)));
  return rm.gated(p_customer_id,'rm.talking_points','rm.talking_points','chat_topics',g,p_purpose,points)||jsonb_build_object('pending',pending);
end $$;

-- 0110's brief re-check: an inferred point on hold is not_shared (an existing status; the meeting side needs no change).
create or replace function public.rm_point_status(p_customer_id bigint,p_point_ids uuid[]) returns jsonb
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
      when p.origin='confirmed' or (g is not null and not paused and p.on_hold_at is null and p.visible_from<=clock_timestamp()
        and (areas is null or areas ? consent.topic_area(p.topic))) then 'shown'
      else 'not_shared' end,
    'removed_on',((coalesce(p.deleted_at,t.deleted_at)) at time zone 'Asia/Singapore')::date) order by i.n),'[]') into data
  from unnest(p_point_ids) with ordinality i(id,n)
    left join insights.talking_points p on p.point_id=i.id and p.customer_id=p_customer_id
    left join insights.point_tombstones t on t.point_id=i.id and t.customer_id=p_customer_id;
  return rm.result(p_customer_id,'rm.point_status','insights.talking_points',data);
end $$;

-- 0142's own view, with on hold: the point stays on her page, marked, and is not pending while on hold.
create or replace function public.customer_read_talking_points(p_customer_id bigint) returns jsonb
language plpgsql volatile security invoker set search_path='' as $$
declare cid bigint:=banking.require_read_customer(p_customer_id); data jsonb;
begin
  perform insights.tidy();
  select jsonb_build_object(
    'points',(select coalesce(jsonb_agg(insights.point_json(p)||jsonb_build_object('visible_from',p.visible_from,
        'pending',p.origin='inferred' and p.on_hold_at is null and p.visible_from>clock_timestamp(),
        'on_hold',p.on_hold_at is not null,'on_hold_at',p.on_hold_at) order by p.last_seen desc),'[]')
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

-- Grants --------------------------------------------------------------------------------------------------------------
revoke all on function public.customer_topic_state(bigint,uuid),public.customer_apply_talking_point(uuid,text,text,text,text,text,text[],text,boolean)
  from public,anon;
grant execute on function public.customer_topic_state(bigint,uuid),public.customer_apply_talking_point(uuid,text,text,text,text,text,text[],text,boolean)
  to authenticated;
revoke all on function insights.kinds(text),insights.merge_concerns(text[],text[]),insights.active_topics(conversation.turns),
  insights.status_json(bigint) from public,anon,authenticated;
$source_0143$;
begin
  select * into previous from supabase_migrations.schema_migrations where version='0143';
  if found then
    if previous.name is distinct from 'stateful_talking_points' or previous.statements is distinct from array[script] then
      raise exception 'migration 0143 is recorded with different SQL; inspect history before proceeding';
    end if;
    raise notice 'migration 0143 already applied with identical SQL';
  else
    execute script;
    insert into supabase_migrations.schema_migrations(version,name,statements)
      values('0143','stateful_talking_points',array[script]);
  end if;
end $apply_0143$;

do $apply_0144$
declare previous supabase_migrations.schema_migrations; script text:=$source_0144$-- Released source SHA256: a47f7fae9db7999fdeac7b5fc2764fd1e0b21dc73fb28ed1c88ca834fa90f377
-- Client chat insights (8 Nov 2026). Synthetic data only.
-- The talking-points feature is called Client chat insights in words people read: "Chat insights" on her own pages,
-- "Client chat insights" on the RM side (code, the insights schema, insight.* actions and the shared TalkingPoint
-- contract keep their names). Data sharing reads its category labels from consent.categories (0109), so the chat_topics
-- row changes here. Only the label: the description is the consent notice text of ct-2026-11 and stays as she agreed it.

update consent.categories set label='Chat insights' where code='chat_topics' and label='Topics from your chats';
do $$ begin
  if (select label from consent.categories where code='chat_topics') is distinct from 'Chat insights' then
    raise exception 'consent.categories chat_topics label was not updated';
  end if;
end $$;
$source_0144$;
begin
  select * into previous from supabase_migrations.schema_migrations where version='0144';
  if found then
    if previous.name is distinct from 'chat_insights_label' or previous.statements is distinct from array[script] then
      raise exception 'migration 0144 is recorded with different SQL; inspect history before proceeding';
    end if;
    raise notice 'migration 0144 already applied with identical SQL';
  else
    execute script;
    insert into supabase_migrations.schema_migrations(version,name,statements)
      values('0144','chat_insights_label',array[script]);
  end if;
end $apply_0144$;

do $apply_0145$
declare previous supabase_migrations.schema_migrations; script text:=$source_0145$-- Released source SHA256: 5f10b54b3626f9e6f32058da77abd1d0d14fe99e419161297d853903c274bca8
-- Client chat insights without the care pause (8 Nov 2026). Synthetic data only.
-- Since 0108 a care signal in her chat (job loss, debt, bereavement, serious illness, pressure about money) paused chat
-- insights for 60 days: public.customer_pause_suggestions opened a row in insights.opportunity_pauses, and while it was
-- open nothing new was recorded, her inferred insights were hidden from her RM and Data sharing showed a Resume line.
-- The pause is retired: the app no longer calls it after a reply or refuses a share offer on care, and her page has no
-- Resume line. This ends every open pause (audited per pause, system actor) so nothing stays hidden without a way back,
-- and drops the setter so no pause can be opened again. The table, insights.paused() and the paused checks in the
-- released readers stay unchanged (always false with no open pause); insight.resume keeps undoing a mute. Her care flag
-- (guardrail.CARE, AnswerEvent.care: only Talk to a person under her answer) is unchanged and no longer touches insights.

do $$
declare p insights.opportunity_pauses;
begin
  for p in update insights.opportunity_pauses set ended_at=clock_timestamp()
      where ended_at is null and until>clock_timestamp() returning * loop
    perform audit.record('data','data.suggestions_resumed','success','insights.opportunity_pauses',p.pause_id::text,
      jsonb_build_object('reason','care_pause_retired'),p_customer_id=>p.customer_id,p_actor_type=>'system',p_actor_id=>'migration_0145');
  end loop;
end $$;

drop function if exists public.customer_pause_suggestions(uuid);
$source_0145$;
begin
  select * into previous from supabase_migrations.schema_migrations where version='0145';
  if found then
    if previous.name is distinct from 'retire_care_pause' or previous.statements is distinct from array[script] then
      raise exception 'migration 0145 is recorded with different SQL; inspect history before proceeding';
    end if;
    raise notice 'migration 0145 already applied with identical SQL';
  else
    execute script;
    insert into supabase_migrations.schema_migrations(version,name,statements)
      values('0145','retire_care_pause',array[script]);
  end if;
end $apply_0145$;

commit;
select version,name from supabase_migrations.schema_migrations order by version;
