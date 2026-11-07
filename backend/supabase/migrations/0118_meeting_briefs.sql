-- Brief Writer (architecture §15.4, 7 Nov 2026): one saved, versioned pre-meeting brief per confirmed meeting.
--   * Prepared with the RM's JWT (services/meetings/brief.py runs the copilot's meeting brief for that appointment); the
--     restricted worker still reads no client records. The workspace prepares missing briefs for the next 7 days when
--     the RM opens it, so a brief is ready before she asks.
--   * Stored: the record sections, the model's checked points and the source titles. Not stored: anything from a
--     consent-gated read (topics from her chats, transaction insights, large movements); those items are re-read under
--     her consent each time the brief is opened. grants_used records which grants were active and which topics were shown.
--   * On open, rm_meeting_brief re-checks consent and deletions: a withdrawn grant or a removed topic is returned with
--     its date, and the brief renders it as "Removed by client on <date>". `stale` says notes or tasks changed since.
--   * Approval: the RM approves an exact version (rm_meeting_brief_approve); a newer version needs a new approval.
-- Owned by the meeting side. Numbered 0118 (a free number) because 0125 is the meeting team's zoom_my_notes on hosted, and
-- named meetings.copilot_briefs because the meeting team's own meetings.briefs (a different shape) already exists there.

create table meetings.copilot_briefs (
  brief_id uuid primary key default gen_random_uuid(),
  meeting_id uuid not null references rm.appointments(appointment_id) on delete cascade,
  customer_id bigint not null references banking.customer_profiles(customer_id) on delete cascade,
  advisor_id uuid not null,
  version int not null check (version>=1),
  status text not null default 'draft' check (status in ('draft','approved')),
  content jsonb not null check (jsonb_typeof(content)='object' and jsonb_typeof(content->'sections')='array' and pg_column_size(content)<=262144),
  grants_used jsonb not null default '{}' check (jsonb_typeof(grants_used)='object'),
  point_ids uuid[] not null default '{}' check (cardinality(point_ids)<=50),
  command_id uuid not null unique,
  created_at timestamptz not null default clock_timestamp(),
  approved_at timestamptz,
  unique (meeting_id,version)
);
alter table meetings.copilot_briefs enable row level security;  -- no policies: read and written only by the functions below
revoke all on meetings.copilot_briefs from public,anon,authenticated;

-- The signed-in RM's upcoming or recent confirmed meeting with a client in her current book, or a refusal.
create function rm.brief_meeting(p_meeting uuid) returns rm.appointments language plpgsql stable security definer set search_path='' as $$
declare advisor uuid:=rm.current_advisor(); a rm.appointments;
begin
  select * into a from rm.appointments where appointment_id=p_meeting;
  if not found or a.advisor_id is distinct from advisor or not rm.in_book(advisor,a.customer_id) then
    raise exception 'this meeting is not one of yours' using errcode='42501'; end if;
  if a.status<>'confirmed' then raise exception 'only a confirmed meeting has a brief' using errcode='P1001'; end if;
  return a;
end $$;

-- Re-check of one brief: grants it used that are no longer active, topics it showed that the client removed since.
create function rm.brief_recheck(b meetings.copilot_briefs) returns jsonb language plpgsql stable security definer set search_path='' as $$
declare withdrawn jsonb:='[]'; removed jsonb:='[]'; cat text; g consent.grants;
begin
  for cat in select jsonb_object_keys(b.grants_used) loop
    continue when b.grants_used->>cat is null;
    select * into g from consent.grants where grant_id=(b.grants_used->>cat)::uuid;
    if not found or g.revoked_at is not null or g.expires_at<=clock_timestamp() then
      withdrawn:=withdrawn||jsonb_build_object('category',cat,'on',rm.day((coalesce(g.revoked_at,g.expires_at,clock_timestamp()) at time zone 'Asia/Singapore')::date));
    end if;
  end loop;
  select coalesce(jsonb_agg(jsonb_build_object('point_id',i.id,'on',rm.day((coalesce(p.deleted_at,t.deleted_at) at time zone 'Asia/Singapore')::date))),'[]') into removed
    from unnest(b.point_ids) i(id)
    left join insights.talking_points p on p.point_id=i.id and p.customer_id=b.customer_id
    left join insights.point_tombstones t on t.point_id=i.id and t.customer_id=b.customer_id
    where p.deleted_at is not null or (p.point_id is null and t.point_id is not null);
  return jsonb_build_object('withdrawn',withdrawn,'removed_points',removed,
    'stale',exists(select 1 from rm.meeting_notes n where n.customer_id=b.customer_id and n.created_at>b.created_at)
      or exists(select 1 from rm.tasks k where k.customer_id=b.customer_id and k.advisor_id=b.advisor_id and k.created_at>b.created_at));
end $$;

create function rm.brief_json(b meetings.copilot_briefs) returns jsonb language sql stable security definer set search_path='' as $$
  select jsonb_build_object('brief_id',b.brief_id,'meeting_id',b.meeting_id,'customer_id',b.customer_id,'version',b.version,'status',b.status,
    'content',b.content,'grants_used',b.grants_used,'created_at',b.created_at,'created_text',rm.sgt(b.created_at),
    'approved_at',b.approved_at)||rm.brief_recheck(b)
$$;

-- The next 14 days of the RM's confirmed meetings and where each brief stands (Today and Schedule; ids, dates, codes).
create function public.rm_meeting_briefs() returns jsonb language plpgsql volatile security definer set search_path='' as $$
declare advisor uuid:=rm.current_advisor();
begin
  if advisor is null then raise exception 'relationship managers only' using errcode='42501'; end if;
  return rm.result(null,'rm.meeting_briefs','meetings.copilot_briefs',(select coalesce(jsonb_agg(jsonb_build_object(
      'meeting_id',a.appointment_id,'customer_id',a.customer_id,'client',rm.display_name(p.full_name),'starts_at',a.starts_at,'when',rm.sgt(a.starts_at),
      'format',a.format,'purpose',a.purpose,'brief_version',b.version,'brief_status',b.status,'brief_created',rm.sgt(b.created_at),
      'stale',case when b.brief_id is null then null else (rm.brief_recheck(b)->>'stale')::boolean end) order by a.starts_at),'[]')
    from rm.appointments a join banking.customer_profiles p using(customer_id)
    left join lateral (select * from meetings.copilot_briefs x where x.meeting_id=a.appointment_id order by x.version desc limit 1) b on true
    where a.advisor_id=advisor and a.status='confirmed' and a.starts_at>clock_timestamp() and a.starts_at<clock_timestamp()+interval '14 days'
      and rm.in_book(advisor,a.customer_id)));
end $$;

create function public.rm_meeting_brief(p_meeting_id uuid) returns jsonb language plpgsql volatile security definer set search_path='' as $$
declare a rm.appointments:=rm.brief_meeting(p_meeting_id); b meetings.copilot_briefs;
begin
  select * into b from meetings.copilot_briefs where meeting_id=a.appointment_id order by version desc limit 1;
  return rm.result(a.customer_id,'rm.meeting_brief','meetings.copilot_briefs',jsonb_build_object('meeting',jsonb_build_object('meeting_id',a.appointment_id,
      'customer_id',a.customer_id,'when',rm.sgt(a.starts_at),'starts_at',a.starts_at,'format',a.format,'purpose',a.purpose,'topics',to_jsonb(coalesce(a.topics,'{}'))),
    'brief',case when b.brief_id is null then null else rm.brief_json(b) end));
end $$;

-- Save a new version. grants_used comes from the active grants here, never from the caller; point ids must be hers.
create function public.rm_meeting_brief_save(p_meeting_id uuid,p_content jsonb,p_point_ids uuid[],p_command_id uuid) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
declare a rm.appointments:=rm.brief_meeting(p_meeting_id); advisor uuid:=rm.current_advisor(); b meetings.copilot_briefs; used jsonb:='{}'; cat text;
begin
  select * into b from meetings.copilot_briefs where command_id=p_command_id;
  if found then
    if b.meeting_id<>p_meeting_id then raise exception 'command id already used for another meeting' using errcode='23505'; end if;
    return jsonb_build_object('brief_id',b.brief_id,'version',b.version,'replayed',true);
  end if;
  if p_content is null or jsonb_typeof(p_content->'sections')<>'array' or jsonb_array_length(p_content->'sections') not between 1 and 12 then
    raise exception 'a brief has 1 to 12 sections' using errcode='22023'; end if;
  if exists(select 1 from unnest(coalesce(p_point_ids,'{}')) i(id) where not exists(
      select 1 from insights.talking_points p where p.point_id=i.id and p.customer_id=a.customer_id)) then
    raise exception 'a topic does not belong to this client' using errcode='22023'; end if;
  foreach cat in array array['transaction_insights','chat_topics'] loop
    used:=used||jsonb_build_object(cat,(select g.grant_id from rm.active_consent(advisor,a.customer_id) g where cat=any(g.categories) limit 1));
  end loop;
  perform pg_advisory_xact_lock(hashtext('meetings.copilot_briefs'),hashtext(p_meeting_id::text));
  insert into meetings.copilot_briefs(meeting_id,customer_id,advisor_id,version,content,grants_used,point_ids,command_id)
    values(a.appointment_id,a.customer_id,advisor,coalesce((select max(version) from meetings.copilot_briefs where meeting_id=a.appointment_id),0)+1,
      p_content,used,coalesce(p_point_ids,'{}'),p_command_id) returning * into b;
  perform audit.record('action','action.meeting_brief_saved','success','meetings.copilot_briefs',b.brief_id::text,
    jsonb_build_object('meeting_id',a.appointment_id,'version',b.version,'grants_used',used,'topics',cardinality(b.point_ids)),
    p_actor_type=>'staff',p_actor_id=>auth.uid()::text,p_customer_id=>a.customer_id);
  return jsonb_build_object('brief_id',b.brief_id,'version',b.version,'replayed',false);
end $$;

-- The RM approves the exact version she read; approving an older version than the latest is refused.
create function public.rm_meeting_brief_approve(p_meeting_id uuid,p_brief_id uuid,p_version int,p_command_id uuid) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
declare a rm.appointments:=rm.brief_meeting(p_meeting_id); b meetings.copilot_briefs;
begin
  select * into b from meetings.copilot_briefs where meeting_id=a.appointment_id order by version desc limit 1 for update;
  if not found or b.brief_id<>p_brief_id or b.version<>p_version then
    raise exception 'approval references a replaced brief; read the latest version first' using errcode='P1001'; end if;
  if b.status='approved' then return jsonb_build_object('brief_id',b.brief_id,'version',b.version,'status','approved','replayed',true); end if;
  update meetings.copilot_briefs set status='approved',approved_at=clock_timestamp() where brief_id=b.brief_id;
  perform audit.record('action','action.meeting_brief_approved','success','meetings.copilot_briefs',b.brief_id::text,
    jsonb_build_object('meeting_id',a.appointment_id,'version',b.version,'command_id',p_command_id),
    p_actor_type=>'staff',p_actor_id=>auth.uid()::text,p_customer_id=>a.customer_id);
  return jsonb_build_object('brief_id',b.brief_id,'version',b.version,'status','approved','replayed',false);
end $$;

revoke all on function rm.brief_meeting(uuid),rm.brief_recheck(meetings.copilot_briefs),rm.brief_json(meetings.copilot_briefs) from public,anon,authenticated;
revoke all on function public.rm_meeting_briefs(),public.rm_meeting_brief(uuid),public.rm_meeting_brief_save(uuid,jsonb,uuid[],uuid),
  public.rm_meeting_brief_approve(uuid,uuid,int,uuid) from public,anon;
grant execute on function public.rm_meeting_briefs(),public.rm_meeting_brief(uuid),public.rm_meeting_brief_save(uuid,jsonb,uuid[],uuid),
  public.rm_meeting_brief_approve(uuid,uuid,int,uuid) to authenticated;
