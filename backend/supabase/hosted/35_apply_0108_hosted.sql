-- Existing DemoBank project: apply only after reviewing hosted schema and RLS.
begin;

set local lock_timeout='10s';
set local statement_timeout='120s';

select pg_advisory_xact_lock(hashtext('demobank.hosted_migrations'));

do $apply_0108$
declare previous supabase_migrations.schema_migrations; script text:=$source_0108$-- Released source SHA256: e1b4ae3661a2d3a57c97c42bba36a87f83a3ac3359e4d190ba1903b9aab80b8f
-- RM copilot build plan phase 4 (docs/planning/rm-copilot-build-plan.md): the talking-point pipeline. Sophie learns what
-- her client is planning without ever seeing the client's chat: only list values, dates and ids cross to the RM side
-- (schemas/meetings.TalkingPoint). Synthetic data only.
--   1. insights schema (not exposed through the Data API; every call goes through the public functions below):
--      talking_points, talking_point_evidence (the client's own quotes: no RM role has a grant and no rm_* function
--      selects from it), topic_mutes, opportunity_pauses and share_prompts. RLS on every table.
--   2. Lane 1 (inferred): customer_record_talking_point under her JWT (rechecks the chat_topics grant, idempotent on
--      turn and topic, merges: at most 3 quotes, stage only moves up, last_seen moves on); customer_pause_suggestions
--      for a care signal (60 days, nothing stored).
--   3. Her controls on the ledger: insight.delete, insight.mute, insight.resume (automatic, with receipts) and lane 2's
--      insight.share (confirmed by her; a confirmed point and a notice to her RM that names no topic).
--   4. rm.topic_shares: what clients shared with their RM, for Today; at most 20 notices per RM per day.
--   5. Reads: customer_read_talking_points, customer_topic_status, rm_talking_points (filled, same shape plus `pending`)
--      and rm_shared_topics (Today: confirmed shares only).
--   6. When the chat_topics grant ends: her inferred points and all evidence are deleted; confirmed points stay.
--   7. Demo seed and rm.reset_meeting_demo (0107) resetting the two demo points too.

-- 1. Schema and tables ------------------------------------------------------------------------------------------------
create schema insights;
revoke all on schema insights from public,anon;
grant usage on schema insights to authenticated;

create function insights.topics() returns text[] language sql immutable set search_path='' as
$$ select array['home_loan','investments','fixed_deposit','insurance'] $$;  -- the product codes of rm.meeting_topics() (0106)
create function insights.stage_rank(p text) returns int language sql immutable set search_path='' as
$$ select array_position(array['concern','curious','exploring','planning','ready'],p) $$;  -- "stage only moves up"
create function insights.valid_concerns(p text[]) returns boolean language sql immutable set search_path='' as $$
  select p is not null and cardinality(p)<=4 and p<@array['rates','fees','eligibility','timing']
    and (select count(distinct c) from unnest(p) c)=cardinality(p)
$$;
-- Amounts are never stored: a quote keeps "[amount]" in their place (services/talking_points.AMOUNT is the same pattern).
create function insights.mask_amounts(p text) returns text language sql immutable set search_path='' as $$
  select regexp_replace(p,'(s?\$|sgd ?|usd ?)\d[\d,]*(\.\d+)?( ?(k|m|mil|million|thousand)\y)?|\y\d[\d,]*(\.\d+)? ?(k|m|mil|million|thousand)\y'
    '|\y\d{1,3}(,\d{3})+(\.\d+)?\y|\y\d{5,}\y','[amount]','gi')
$$;

create table insights.talking_points (
  point_id         uuid primary key default gen_random_uuid(),
  customer_id      bigint not null references banking.customer_profiles(customer_id) on delete cascade,
  topic            text not null check (topic=any(insights.topics())),
  stage            text not null check (insights.stage_rank(stage) is not null),
  timeframe        text not null default 'unknown' check (timeframe in ('now','within_3m','within_12m','later','unknown')),
  concerns         text[] not null default '{}' check (insights.valid_concerns(concerns)),
  origin           text not null check (origin in ('inferred','confirmed')),
  first_seen       timestamptz not null default clock_timestamp(),
  last_seen        timestamptz not null default clock_timestamp(),
  visible_from     timestamptz not null,  -- inferred: 24 hours after creation, so she can remove it first; confirmed: at once
  expires_at       timestamptz not null,  -- 90 days after last_seen
  share_command_id uuid,                  -- the insight.share command behind a confirmed point (null for the demo seed)
  unique (customer_id,topic),             -- one active row per client and topic; an expired row is replaced
  check (origin='confirmed' or share_command_id is null)
);
create table insights.talking_point_evidence (
  evidence_id uuid primary key default gen_random_uuid(),
  point_id    uuid not null references insights.talking_points(point_id) on delete cascade,
  customer_id bigint not null references banking.customer_profiles(customer_id) on delete cascade,
  turn_id     uuid references conversation.turns(turn_id) on delete cascade,  -- null only for the demo seed
  quote       text check (quote is null or length(quote) between 1 and 200),  -- null once 3 newer quotes exist (the turn stays for idempotency)
  recorded_at timestamptz not null default clock_timestamp(),
  unique (point_id,turn_id)
);
create table insights.topic_mutes (
  customer_id bigint not null references banking.customer_profiles(customer_id) on delete cascade,
  topic       text not null check (topic=any(insights.topics())),
  muted_at    timestamptz not null default clock_timestamp(),
  command_id  uuid,
  primary key (customer_id,topic)
);
create table insights.opportunity_pauses (
  pause_id       uuid primary key default gen_random_uuid(),
  customer_id    bigint not null references banking.customer_profiles(customer_id) on delete cascade,
  until          timestamptz not null,
  reason         text not null default 'care' check (reason in ('care')),
  created_at     timestamptz not null default clock_timestamp(),
  ended_at       timestamptz,
  end_command_id uuid
);
-- Lane 2's limit: one Let-Sophie-know prompt per client and topic per 30 days (written when the offer is saved).
create table insights.share_prompts (
  customer_id bigint not null references banking.customer_profiles(customer_id) on delete cascade,
  topic       text not null check (topic=any(insights.topics())),
  offered_at  timestamptz not null,
  primary key (customer_id,topic)
);
create index on insights.talking_point_evidence(customer_id);
create index on insights.opportunity_pauses(customer_id,until desc);

alter table insights.talking_points enable row level security;
alter table insights.talking_point_evidence enable row level security;
alter table insights.topic_mutes enable row level security;
alter table insights.opportunity_pauses enable row level security;
alter table insights.share_prompts enable row level security;
revoke all on all tables in schema insights from public,anon,authenticated;
-- Her own points, mutes and pauses under RLS (customer_read_talking_points reads them with her JWT). The evidence and the
-- prompt log have no grant to any app role: only the definer functions below read them, each with her customer id.
grant select on insights.talking_points,insights.topic_mutes,insights.opportunity_pauses to authenticated;
create policy own_rows on insights.talking_points for select to authenticated using (customer_id=(select banking.my_customer_id()));
create policy own_rows on insights.topic_mutes for select to authenticated using (customer_id=(select banking.my_customer_id()));
create policy own_rows on insights.opportunity_pauses for select to authenticated using (customer_id=(select banking.my_customer_id()));
create trigger audit_row after insert or update or delete on insights.talking_points for each row
  execute function audit.row_change('point_id','topic','stage','timeframe','origin');

create table rm.topic_shares (
  share_id    uuid primary key default gen_random_uuid(),
  advisor_id  uuid not null references rm.advisors(advisor_id),
  customer_id bigint not null references banking.customer_profiles(customer_id) on delete cascade,
  point_id    uuid not null references insights.talking_points(point_id) on delete cascade,  -- her Delete removes it from Today
  command_id  uuid unique,       -- the confirmed insight.share (null for the demo seed)
  notice      boolean not null,  -- one of the first 20 notices to this RM that day; the rest wait on Today
  created_at  timestamptz not null default clock_timestamp()
);
create index on rm.topic_shares(advisor_id,created_at desc);
alter table rm.topic_shares enable row level security;
revoke all on rm.topic_shares from public,anon,authenticated;

-- Helpers ------------------------------------------------------------------------------------------------------------
create function insights.rm_of(p_customer bigint) returns uuid language sql stable security definer set search_path='' as $$
  select r.advisor_id from rm.assignments r join rm.advisors a using(advisor_id)
  where r.customer_id=p_customer and r.ended_on is null and r.started_on<=rm.today() and a.is_active order by r.started_on desc limit 1
$$;
create function insights.chat_topics_active(p_customer bigint) returns boolean language sql stable security definer set search_path='' as $$
  select coalesce(rm.consent_active(p_customer,'chat_topics',insights.rm_of(p_customer)),false)
$$;
create function insights.paused(p_customer bigint) returns boolean language sql stable security definer set search_path='' as $$
  select exists(select 1 from insights.opportunity_pauses where customer_id=p_customer and ended_at is null and until>clock_timestamp())
$$;
-- The one RM-side rendering of a point: exactly the schemas/meetings.TalkingPoint fields (no quote, no free text).
create function insights.point_json(p insights.talking_points) returns jsonb language sql stable set search_path='' as $$
  select jsonb_build_object('point_id',p.point_id,'topic',p.topic,'stage',p.stage,'timeframe',p.timeframe,'concerns',to_jsonb(p.concerns),
    'origin',p.origin,'first_seen',(p.first_seen at time zone 'Asia/Singapore')::date,'last_seen',(p.last_seen at time zone 'Asia/Singapore')::date)
$$;
create function insights.forget_inferred(p_customer bigint) returns void language plpgsql volatile security definer set search_path='' as $$
declare quotes int; points int;
begin
  delete from insights.talking_point_evidence where customer_id=p_customer;
  get diagnostics quotes=row_count;
  delete from insights.talking_points where customer_id=p_customer and origin='inferred';
  get diagnostics points=row_count;
  if quotes+points>0 then
    perform audit.record('data','data.talking_points_forgotten','success','insights.talking_points',p_customer::text,
      jsonb_build_object('points',points,'evidence',quotes),p_customer_id=>p_customer);
  end if;
end $$;
-- Her own clean-up before she reads: expired points, and inferred points and evidence once no chat_topics grant is
-- active (a revocation deletes them at once, section 6; an expiry is only noticed here or by the next record).
create function insights.tidy() returns void language plpgsql volatile security definer set search_path='' as $$
declare cid bigint:=banking.my_customer_id();
begin
  if cid is null then return; end if;
  delete from insights.talking_points where customer_id=cid and expires_at<=clock_timestamp();
  if not insights.chat_topics_active(cid) then perform insights.forget_inferred(cid); end if;
end $$;
create function insights.my_evidence(p_point uuid) returns jsonb language sql stable security definer set search_path='' as $$
  select coalesce(jsonb_agg(jsonb_build_object('quote',e.quote,'recorded_at',e.recorded_at) order by e.recorded_at desc),'[]')
  from insights.talking_point_evidence e where e.point_id=p_point and e.customer_id=banking.my_customer_id() and e.quote is not null
$$;
create function insights.my_chat_topics() returns boolean language sql stable security definer set search_path='' as
$$ select insights.chat_topics_active(banking.my_customer_id()) $$;
create function insights.first_name(p_advisor uuid) returns text language sql stable security definer set search_path='' as
$$ select split_part(rm.display_name(a.full_name),' ',1) from rm.advisors a where a.advisor_id=p_advisor $$;
create function insights.my_rm_first_name() returns text language sql stable security definer set search_path='' as
$$ select insights.first_name(insights.rm_of(banking.my_customer_id())) $$;

-- 2. Lane 1: inferred points ------------------------------------------------------------------------------------------
create function insights.require_turn(p_turn uuid) returns conversation.turns language plpgsql stable security definer set search_path='' as $$
declare cid bigint:=banking.my_customer_id(); t conversation.turns;
begin
  if auth.uid() is null or cid is null then raise exception 'a linked customer session is required' using errcode='42501'; end if;
  select * into t from conversation.turns where turn_id=p_turn and customer_id=cid;
  if not found then raise exception 'the chat turn is not in this customer scope' using errcode='42501'; end if;
  return t;
end $$;

-- One point from one of her saved chat turns. services/talking_points.py validates first; this checks again: the quote is
-- her own words from that turn, the topic, stage, timeframe and concerns come from the fixed lists, chat_topics is active
-- for her current RM, the topic is not muted and suggestions are not paused. Returns recorded or the reason it was not.
create function public.customer_record_talking_point(p_turn_id uuid,p_topic text,p_stage text,p_timeframe text,p_concerns text[],p_quote text)
returns jsonb language plpgsql volatile security definer set search_path='' as $$
declare t conversation.turns:=insights.require_turn(p_turn_id); cid bigint:=t.customer_id; message text; words text; reason text;
  p insights.talking_points; created boolean:=false; at timestamptz:=clock_timestamp();
begin
  if p_topic is null or not (p_topic=any(insights.topics())) or insights.stage_rank(p_stage) is null
      or p_timeframe is null or p_timeframe not in ('now','within_3m','within_12m','later','unknown') or not insights.valid_concerns(coalesce(p_concerns,'{}'))
      or p_quote is null or length(btrim(p_quote)) not between 1 and 200 then
    raise exception 'a talking point needs a listed topic, stage, timeframe and concerns and a quote of 1-200 characters' using errcode='22023';
  end if;
  select m.redacted_text into message from conversation.messages m where m.message_id=t.user_message_id and m.customer_id=cid;
  words:=insights.mask_amounts(btrim(p_quote));
  if message is null or position(words in insights.mask_amounts(message))=0 then
    raise exception 'the quote must be the client''s own words from this turn' using errcode='22023';
  end if;
  perform pg_advisory_xact_lock(hashtext('insights.talking_points'),hashtext(cid::text));
  reason:=case when not insights.chat_topics_active(cid) then 'no_consent' when insights.paused(cid) then 'paused'
    when exists(select 1 from insights.topic_mutes where customer_id=cid and topic=p_topic) then 'muted' end;
  if reason is null then
    select * into p from insights.talking_points where customer_id=cid and topic=p_topic for update;
    if p.point_id is not null and p.expires_at<=at then delete from insights.talking_points where point_id=p.point_id; p:=null; end if;
    if p.origin='confirmed' then reason:='confirmed';  -- her own share stands as she gave it; inference never changes it
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

-- A care signal on one of her saved turns: product suggestions pause for 60 days (an active pause is extended). Nothing
-- about the turn is stored, and the audit records neither the reason nor any text.
create function public.customer_pause_suggestions(p_turn_id uuid) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
declare t conversation.turns:=insights.require_turn(p_turn_id); p insights.opportunity_pauses; until_ timestamptz:=clock_timestamp()+interval '60 days';
begin
  perform pg_advisory_xact_lock(hashtext('insights.talking_points'),hashtext(t.customer_id::text));
  update insights.opportunity_pauses set until=greatest(until,until_) where pause_id=(select pause_id from insights.opportunity_pauses
    where customer_id=t.customer_id and ended_at is null and until>clock_timestamp() order by until desc limit 1) returning * into p;
  if p.pause_id is null then
    insert into insights.opportunity_pauses(customer_id,until) values(t.customer_id,until_) returning * into p;
  end if;
  perform audit.record('data','data.suggestions_paused','success','insights.opportunity_pauses',p.pause_id::text,
    jsonb_build_object('days',60),t.case_id,t.session_id);
  return jsonb_build_object('pause_id',p.pause_id,'until',p.until);
end $$;

-- 3. Her controls on the ledger ---------------------------------------------------------------------------------------
create function actions.normalise_insight_point(p_target jsonb) returns jsonb language plpgsql immutable set search_path='' as $$
begin
  if jsonb_typeof(p_target->'point_id') is distinct from 'string' or (p_target->>'point_id') !~* '^[0-9a-f]{8}-([0-9a-f]{4}-){3}[0-9a-f]{12}$' then
    raise exception 'a talking point action needs the point UUID' using errcode='22023';
  end if;
  return jsonb_build_object('point_id',(p_target->>'point_id')::uuid);
end $$;
create function actions.normalise_insight_topic(p_target jsonb) returns jsonb language plpgsql immutable set search_path='' as $$
begin
  if jsonb_typeof(p_target->'topic') is distinct from 'string' or not ((p_target->>'topic')=any(insights.topics())) then
    raise exception 'topic must be home_loan, investments, fixed_deposit or insurance' using errcode='22023';
  end if;
  return jsonb_build_object('topic',p_target->>'topic');
end $$;
-- insight.resume: {"topic": null} ends the pause; {"topic": "<code>"} lets that topic be suggested again.
create function actions.normalise_insight_resume(p_target jsonb) returns jsonb language plpgsql immutable set search_path='' as $$
begin
  if not (p_target ? 'topic') or jsonb_typeof(p_target->'topic')='null' then return jsonb_build_object('topic',null); end if;
  return actions.normalise_insight_topic(p_target);
end $$;
create function actions.normalise_insight_share(p_target jsonb) returns jsonb language plpgsql immutable set search_path='' as $$
begin
  if jsonb_typeof(p_target->'stage') is distinct from 'string' or insights.stage_rank(p_target->>'stage') is null
      or jsonb_typeof(p_target->'timeframe') is distinct from 'string' or p_target->>'timeframe' not in ('now','within_3m','within_12m','later','unknown') then
    raise exception 'a shared topic needs a listed stage and timeframe' using errcode='22023';
  end if;
  return actions.normalise_insight_topic(p_target)||jsonb_build_object('stage',p_target->>'stage','timeframe',p_target->>'timeframe');
end $$;

create function actions.insight_delete_target(p_target jsonb,p_customer_id bigint) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
declare target jsonb:=actions.normalise_insight_point(p_target); p insights.talking_points;
begin
  select * into p from insights.talking_points where point_id=(target->>'point_id')::uuid and customer_id=p_customer_id;
  if not found then raise exception 'talking point is not available in this customer scope' using errcode='42501'; end if;
  return target||jsonb_build_object('topic',p.topic);
end $$;
create function actions.execute_insight_delete(p_command actions.commands) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
begin
  delete from insights.talking_points where point_id=(p_command.target->>'point_id')::uuid and customer_id=p_command.customer_id;
  if not found then raise exception 'the talking point was already removed' using errcode='P1001'; end if;
  return jsonb_build_object('result',jsonb_build_object('point_id',p_command.target->>'point_id','topic',p_command.target->>'topic',
    'status','deleted'),'event_type','insight.deleted');
end $$;

create function actions.insight_mute_target(p_target jsonb,p_customer_id bigint) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
begin return actions.normalise_insight_topic(p_target); end $$;
-- Don't suggest this again: no new inferred point and no Let-Sophie-know prompt for the topic; her inferred point for it
-- goes now. A topic she shared herself stays until she deletes it.
create function actions.execute_insight_mute(p_command actions.commands) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
declare removed int;
begin
  insert into insights.topic_mutes(customer_id,topic,command_id) values(p_command.customer_id,p_command.target->>'topic',p_command.command_id)
    on conflict (customer_id,topic) do nothing;
  delete from insights.talking_points where customer_id=p_command.customer_id and topic=p_command.target->>'topic' and origin='inferred';
  get diagnostics removed=row_count;
  return jsonb_build_object('result',jsonb_build_object('topic',p_command.target->>'topic','status','muted','removed',removed),'event_type','insight.muted');
end $$;

create function actions.insight_resume_target(p_target jsonb,p_customer_id bigint) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
declare target jsonb:=actions.normalise_insight_resume(p_target);
begin
  if target->>'topic' is null and not insights.paused(p_customer_id) then raise exception 'suggestions are not paused' using errcode='22023'; end if;
  if target->>'topic' is not null and not exists(select 1 from insights.topic_mutes where customer_id=p_customer_id and topic=target->>'topic') then
    raise exception 'this topic is not muted' using errcode='22023';
  end if;
  return target;
end $$;
create function actions.execute_insight_resume(p_command actions.commands) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
begin
  if p_command.target->>'topic' is null then
    update insights.opportunity_pauses set ended_at=clock_timestamp(),end_command_id=p_command.command_id
      where customer_id=p_command.customer_id and ended_at is null and until>clock_timestamp();
  else
    delete from insights.topic_mutes where customer_id=p_command.customer_id and topic=p_command.target->>'topic';
  end if;
  if not found then raise exception 'nothing was paused or muted any more' using errcode='P1001'; end if;
  return jsonb_build_object('result',jsonb_build_object('topic',p_command.target->'topic','status','resumed'),'event_type','insight.resumed');
end $$;

-- Lane 2: offered by Relationship & Service (services/orchestrator.share_offer), confirmed by her. Saving the offer runs
-- this validator, which also records the prompt: one per client and topic per 30 days.
create function actions.insight_share_target(p_target jsonb,p_customer_id bigint) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
declare target jsonb:=actions.normalise_insight_share(p_target); advisor uuid:=insights.rm_of(p_customer_id);
begin
  if advisor is null then raise exception 'the client has no relationship manager to tell' using errcode='42501'; end if;
  if insights.paused(p_customer_id) or exists(select 1 from insights.topic_mutes m where m.customer_id=p_customer_id and m.topic=target->>'topic') then
    raise exception 'suggestions for this topic are off' using errcode='22023';
  end if;
  if exists(select 1 from insights.share_prompts s where s.customer_id=p_customer_id and s.topic=target->>'topic'
      and s.offered_at>clock_timestamp()-interval '30 days') then
    raise exception 'this topic was offered in the last 30 days' using errcode='22023';
  end if;
  insert into insights.share_prompts(customer_id,topic,offered_at) values(p_customer_id,target->>'topic',clock_timestamp())
    on conflict (customer_id,topic) do update set offered_at=excluded.offered_at;
  return target||jsonb_build_object('advisor_id',advisor);
end $$;
create function actions.execute_insight_share(p_command actions.commands) returns jsonb
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
  if p.point_id is not null and p.expires_at<=at then delete from insights.talking_points where point_id=p.point_id; p:=null; end if;
  if p.point_id is null then
    insert into insights.talking_points(customer_id,topic,stage,timeframe,origin,first_seen,last_seen,visible_from,expires_at,share_command_id)
      values(cid,t->>'topic',t->>'stage',t->>'timeframe','confirmed',at,at,at,at+interval '90 days',p_command.command_id) returning * into p;
  else
    -- Only what she confirmed (or confirmed before) reaches her RM without chat_topics: an inferred point's stage,
    -- timeframe, concerns and first-seen date are replaced, never carried over.
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

insert into actions.executors(type,validator,executor,confirmation_required,normaliser,event_types) values
  ('insight.delete','actions.insight_delete_target(jsonb,bigint)'::regprocedure,'actions.execute_insight_delete(actions.commands)'::regprocedure,false,
    'actions.normalise_insight_point(jsonb)'::regprocedure,array['insight.deleted']),
  ('insight.mute','actions.insight_mute_target(jsonb,bigint)'::regprocedure,'actions.execute_insight_mute(actions.commands)'::regprocedure,false,
    'actions.normalise_insight_topic(jsonb)'::regprocedure,array['insight.muted']),
  ('insight.resume','actions.insight_resume_target(jsonb,bigint)'::regprocedure,'actions.execute_insight_resume(actions.commands)'::regprocedure,false,
    'actions.normalise_insight_resume(jsonb)'::regprocedure,array['insight.resumed']),
  ('insight.share','actions.insight_share_target(jsonb,bigint)'::regprocedure,'actions.execute_insight_share(actions.commands)'::regprocedure,true,
    'actions.normalise_insight_share(jsonb)'::regprocedure,array['insight.shared']);

-- Her receipts are in Activity; the RM's notice is rm.topic_shares (read on Today). No client inbox notice.
alter function notify.deliver(events.outbox) rename to deliver_before_talking_points;
revoke all on function notify.deliver_before_talking_points(events.outbox) from public,anon,authenticated;
create function notify.deliver(p_event events.outbox) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
begin
  if p_event.event_type in ('insight.deleted','insight.muted','insight.resumed','insight.shared') then
    return jsonb_build_object('disposition','suppressed','notification_id',null,'available_at',null);
  end if;
  return notify.deliver_before_talking_points(p_event);
end $$;
revoke all on function notify.deliver(events.outbox) from public,anon,authenticated;

-- 5. Reads ------------------------------------------------------------------------------------------------------------
-- Data sharing > What Sophie sees: her points (pending during the 24-hour hold) with her own quotes, her muted topics,
-- the active pause and whether chat topics are on. Her JWT and RLS; the evidence through its owner-checked helper.
create function public.customer_read_talking_points(p_customer_id bigint) returns jsonb
language plpgsql volatile security invoker set search_path='' as $$
declare cid bigint:=banking.require_read_customer(p_customer_id); data jsonb;
begin
  perform insights.tidy();
  select jsonb_build_object(
    'points',(select coalesce(jsonb_agg(insights.point_json(p)||jsonb_build_object('visible_from',p.visible_from,
        'pending',p.origin='inferred' and p.visible_from>clock_timestamp(),'evidence',insights.my_evidence(p.point_id)) order by p.last_seen desc),'[]')
      from insights.talking_points p where p.customer_id=cid),
    'muted',(select coalesce(jsonb_agg(m.topic order by m.topic),'[]') from insights.topic_mutes m where m.customer_id=cid),
    'pause',(select jsonb_build_object('pause_id',x.pause_id,'until',x.until) from insights.opportunity_pauses x
      where x.customer_id=cid and x.ended_at is null and x.until>clock_timestamp() order by x.until desc limit 1),
    'chat_topics',insights.my_chat_topics(),'rm_first_name',insights.my_rm_first_name()) into data;
  return banking.customer_result(cid,'insights.talking_points','insights.talking_points',data);
end $$;

-- What both lanes check before doing anything: chat_topics, the pause, muted topics, topics prompted in the last 30
-- days, open meeting request topics and her RM's first name (for the prompt). Ids, codes and a name only.
create function public.customer_topic_status(p_customer_id bigint) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
declare cid bigint:=banking.require_read_customer(p_customer_id); advisor uuid:=insights.rm_of(cid);
begin
  return banking.customer_result(cid,'insights.status','insights.talking_points',jsonb_build_object(
    'chat_topics',insights.chat_topics_active(cid),'paused',insights.paused(cid),'has_rm',advisor is not null,
    'rm_first_name',insights.first_name(advisor),
    'muted',(select coalesce(jsonb_agg(m.topic order by m.topic),'[]') from insights.topic_mutes m where m.customer_id=cid),
    'prompted',(select coalesce(jsonb_agg(s.topic order by s.topic),'[]') from insights.share_prompts s
      where s.customer_id=cid and s.offered_at>clock_timestamp()-interval '30 days'),
    'meeting_topics',(select coalesce(jsonb_agg(distinct x),'[]') from rm.meeting_requests r,unnest(r.topics) x
      where r.customer_id=cid and r.status in ('requested','countered'))));
end $$;

-- The 1d shape, filled (one client per call; nothing returns topics across the book):
-- * with her chat_topics grant: confirmed points and inferred points past their 24-hour hold, unless suggestions are
--   paused; audited as a data.read with the grant id and purpose (rm.gated);
-- * without it: shared=false with the consent-gap audit, and data holds only the points she shared herself, audited as a
--   data.read with basis client_shared and the shares' command ids instead of a grant id.
-- pending: some inferred topic is on hold or paused (never which, and never why); the copilot shows it as a gap.
create or replace function public.rm_talking_points(p_customer_id bigint,p_purpose text) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
declare advisor uuid:=rm.current_advisor(); g uuid; paused boolean; points jsonb; commands jsonb; pending boolean; r jsonb;
begin
  if p_purpose is null or p_purpose not in ('meeting_prep','client_view') then raise exception 'purpose must be meeting_prep or client_view' using errcode='22023'; end if;
  if not rm.in_book(advisor,p_customer_id) then return rm.deny(p_customer_id,'rm.talking_points'); end if;
  g:=rm.grant_for(p_customer_id,'chat_topics',advisor);
  paused:=insights.paused(p_customer_id);
  select coalesce(jsonb_agg(insights.point_json(p) order by p.origin desc,p.last_seen desc),'[]'),
      coalesce(jsonb_agg(p.share_command_id) filter (where p.share_command_id is not null),'[]')
    into points,commands from insights.talking_points p
    where p.customer_id=p_customer_id and p.expires_at>clock_timestamp()
      and (p.origin='confirmed' or (g is not null and not paused and p.visible_from<=clock_timestamp()));
  if g is null then
    r:=rm.gated(p_customer_id,'rm.talking_points','rm.talking_points','chat_topics',null,p_purpose,null);
    if jsonb_array_length(points)>0 then
      perform audit.record('data','data.read','success','insights.talking_points',r->>'call_id',
        jsonb_build_object('tool','rm.talking_points','result_count',jsonb_array_length(points),'basis','client_shared',
          'command_ids',commands,'purpose',p_purpose),p_actor_type=>'staff',p_actor_id=>auth.uid()::text,p_customer_id=>p_customer_id);
    end if;
    return r||jsonb_build_object('data',points,'pending',false);
  end if;
  pending:=exists(select 1 from insights.talking_points p where p.customer_id=p_customer_id and p.origin='inferred'
    and p.expires_at>clock_timestamp() and (paused or p.visible_from>clock_timestamp()));
  return rm.gated(p_customer_id,'rm.talking_points','rm.talking_points','chat_topics',g,p_purpose,points)||jsonb_build_object('pending',pending);
end $$;

-- Today: the topics clients in her book shared with her (confirmed shares only, never an inferred point) in the last 30
-- days, latest share per topic, at most 20, each with the notice that names no topic when it was one of that day's 20.
create function public.rm_shared_topics() returns jsonb language plpgsql volatile security definer set search_path='' as $$
declare advisor uuid:=rm.current_advisor(); call_id uuid:=gen_random_uuid(); data jsonb; commands jsonb;
begin
  with latest as (
    select distinct on (s.point_id) s.*,p as point from rm.topic_shares s join insights.talking_points p using(point_id)
    where s.advisor_id=advisor and rm.in_book(advisor,s.customer_id) and p.origin='confirmed' and p.expires_at>clock_timestamp()
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

-- 6. Withdrawal -------------------------------------------------------------------------------------------------------
-- A revoked, narrowed or deleted chat_topics grant: when none is active for her current RM any more, her inferred points
-- and all evidence go at once. Confirmed points stay until she deletes them or they expire.
create function insights.grant_changed() returns trigger language plpgsql security definer set search_path='' as $$
begin
  if old.purpose='rm_brief' and 'chat_topics'=any(old.categories) and not insights.chat_topics_active(old.customer_id) then
    perform insights.forget_inferred(old.customer_id);
  end if;
  return null;
end $$;
create trigger forget_chat_topics after update or delete on consent.grants for each row execute function insights.grant_changed();

-- 7. Demo seed --------------------------------------------------------------------------------------------------------
-- Michelle (chat_topics granted, 0106): an inferred home loan point already past its hold, with her words as evidence.
-- Mei Ling (no grant): a confirmed investments point she shared with Sophie, on Sophie's Today.
create function insights.seed_demo_points(p_customers bigint[]) returns int language plpgsql volatile set search_path='' as $$
declare sophie uuid:='00000000-0000-4000-8000-100200000002'; at timestamptz:=clock_timestamp(); n int:=0;
begin
  if 1002=any(p_customers) then
    insert into insights.talking_points(point_id,customer_id,topic,stage,timeframe,origin,first_seen,last_seen,visible_from,expires_at)
      values('00000000-0000-4000-8000-100200000091',1002,'home_loan','planning','within_12m','inferred',at-interval '3 days',at-interval '3 days',
        at-interval '2 days',at+interval '87 days');
    insert into insights.talking_point_evidence(point_id,customer_id,quote,recorded_at)
      values('00000000-0000-4000-8000-100200000091',1002,'I''m thinking of buying a condo next year',at-interval '3 days');
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
select insights.seed_demo_points(array[1002,1007]::bigint[]);

-- 0107's demo reset, now also resetting the demo clients' talking points, mutes, pauses and prompts and seeding the two
-- points again. Owner only; hosted 34_reset_meeting_demo.sql calls it.
create or replace function rm.reset_meeting_demo(p_customers bigint[] default array[1002,1007]::bigint[]) returns jsonb
language plpgsql volatile set search_path='' as $$
declare
  sophie uuid:='00000000-0000-4000-8000-100200000002'; today date:=rm.today(); at timestamptz; mon timestamptz; a rm.appointments;
  i int; meetings uuid[]; removed int; michelle timestamptz; mei_ling timestamptz[]; points int;
begin
  if p_customers is null or cardinality(p_customers)=0 or not p_customers<@array[1002,1007]::bigint[] then
    raise exception 'only the demo clients 1002 and 1007' using errcode='22023';
  end if;
  perform pg_advisory_xact_lock(hashtext('rm.appointments'),hashtext(sophie::text));
  select coalesce(array_agg(appointment_id),'{}') into meetings from rm.meeting_requests where customer_id=any(p_customers) and appointment_id is not null;
  delete from rm.meeting_requests where customer_id=any(p_customers);
  get diagnostics removed=row_count;
  delete from rm.appointments where appointment_id=any(meetings);
  delete from notify.inbox where customer_id=any(p_customers) and template_key like 'meeting.%';
  delete from insights.talking_points where customer_id=any(p_customers);  -- evidence and Today shares go with them
  delete from insights.topic_mutes where customer_id=any(p_customers);
  delete from insights.opportunity_pauses where customer_id=any(p_customers);
  delete from insights.share_prompts where customer_id=any(p_customers);

  if 1002=any(p_customers) then
    insert into consent.grants(grant_id,customer_id,purpose,recipient,categories,granted_at,expires_at) values
      ('00000000-0000-4000-8000-100200000041',1002,'rm_brief',sophie::text,array['transaction_insights','chat_topics','meeting_recording'],
       clock_timestamp()-interval '1 day',clock_timestamp()+interval '180 days')
    on conflict (grant_id) do update set categories=excluded.categories,granted_at=excluded.granted_at,expires_at=excluded.expires_at,
      revoked_at=null,revocation_command_id=null;
    for i in 0..7 loop
      at:=((today+((4-extract(isodow from today)::int+6)%7)+1+7*i)+time '15:00') at time zone 'Asia/Singapore';
      if rm.slot_problem(sophie,1002,at) is null and rm.client_week_meetings(1002,sophie,at)=0 then michelle:=at; exit; end if;
    end loop;
    if michelle is null then raise exception 'no free Thursday 15:00 for Michelle in the next 8 weeks' using errcode='P0001'; end if;
    insert into rm.appointments(customer_id,advisor_id,starts_at,ends_at,format,status,joining_details,purpose,topics)
      values(1002,sophie,michelle,michelle+interval '30 minutes','video','confirmed',
        (select o.joining_details from rm.meeting_options o where o.advisor_id=sophie and o.format='video'),'Home loan options',
        array['home_loan','term_sheet','compliance_review']) returning * into a;
    insert into rm.meeting_requests(request_id,customer_id,advisor_id,purpose,topics,format,preferred_times,status,respond_by,appointment_id,
        decision_id,decided_at,created_at,updated_at)
      values('00000000-0000-4000-8000-100200000071',1002,sophie,'Home loan options',array['home_loan','term_sheet','compliance_review'],'video',
        array[michelle,michelle+interval '1 day'],'scheduled',rm.next_business_day(clock_timestamp()-interval '1 day'),a.appointment_id,
        '00000000-0000-4000-8000-100200000072',clock_timestamp()-interval '20 hours',clock_timestamp()-interval '1 day',clock_timestamp()-interval '20 hours');
    perform meetings.enqueue('meeting.scheduled',a.appointment_id,rm.meeting_payload(a,'00000000-0000-4000-8000-100200000071'::uuid,'rm'),
      'decision:00000000-0000-4000-8000-100200000072');
  end if;

  if 1007=any(p_customers) and exists(select 1 from rm.assignments where customer_id=1007 and advisor_id=sophie and ended_on is null) then
    for i in 0..7 loop
      mon:=((today+((1-extract(isodow from today)::int+6)%7)+1+7*i)+time '16:30') at time zone 'Asia/Singapore';
      if rm.slot_problem(sophie,1007,mon) is null and rm.slot_problem(sophie,1007,mon+interval '1 day') is null then
        mei_ling:=array[mon,mon+interval '1 day']; exit;
      end if;
    end loop;
    if mei_ling is null then raise exception 'no free Monday and Tuesday 16:30 for Mei Ling in the next 8 weeks' using errcode='P0001'; end if;
    insert into rm.meeting_requests(request_id,customer_id,advisor_id,purpose,topics,format,preferred_times,respond_by)
      values('00000000-0000-4000-8000-100700000071',1007,sophie,'Renewing my fixed deposit before it matures in December',array['fixed_deposit'],'video',
        mei_ling,rm.next_business_day(clock_timestamp()));
  end if;
  points:=insights.seed_demo_points(p_customers);
  return jsonb_build_object('removed_requests',removed,'removed_meetings',cardinality(meetings),'michelle_meeting',michelle,'mei_ling_times',to_jsonb(mei_ling),
    'talking_points',points);
end $$;

-- Grants --------------------------------------------------------------------------------------------------------------
do $$
declare f text;
begin
  foreach f in array array['customer_record_talking_point(uuid,text,text,text,text[],text)','customer_pause_suggestions(uuid)',
    'customer_read_talking_points(bigint)','customer_topic_status(bigint)','rm_shared_topics()'] loop
    execute format('revoke all on function public.%s from public,anon',f);
    execute format('grant execute on function public.%s to authenticated',f);
  end loop;
end $$;
revoke all on all functions in schema insights from public,anon,authenticated;
-- customer_read_talking_points runs with her JWT (security invoker), so these helpers are callable by a signed-in user:
-- point_json only renders the row it is given; the other four work only on banking.my_customer_id().
grant execute on function insights.point_json(insights.talking_points),insights.tidy(),insights.my_evidence(uuid),insights.my_chat_topics(),
  insights.my_rm_first_name() to authenticated;
revoke all on function actions.normalise_insight_point(jsonb),actions.normalise_insight_topic(jsonb),actions.normalise_insight_resume(jsonb),
  actions.normalise_insight_share(jsonb),actions.insight_delete_target(jsonb,bigint),actions.execute_insight_delete(actions.commands),
  actions.insight_mute_target(jsonb,bigint),actions.execute_insight_mute(actions.commands),actions.insight_resume_target(jsonb,bigint),
  actions.execute_insight_resume(actions.commands),actions.insight_share_target(jsonb,bigint),actions.execute_insight_share(actions.commands)
  from public,anon,authenticated;
revoke all on function rm.reset_meeting_demo(bigint[]) from public,anon,authenticated;
$source_0108$;
begin
  select * into previous from supabase_migrations.schema_migrations where version='0108';
  if found then
    if previous.name is distinct from 'talking_points' or previous.statements is distinct from array[script] then
      raise exception 'migration 0108 is recorded with different SQL; inspect history before proceeding';
    end if;
    raise notice 'migration 0108 already applied with identical SQL';
  else
    execute script;
    insert into supabase_migrations.schema_migrations(version,name,statements)
      values('0108','talking_points',array[script]);
  end if;
end $apply_0108$;

commit;
select version,name from supabase_migrations.schema_migrations order by version;
