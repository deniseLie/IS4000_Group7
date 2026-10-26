-- Workflow 3 (David): approved relationship-manager change wording, customer actions on requests,
-- recorded joining details per meeting format, and two audited customer reads. Synthetic data only.
-- The change explanation is approved DemoBank text keyed by the recorded reason code; the code itself
-- is never shown to customers or models. No real meeting links, calendars or advisors are connected.

insert into kb.articles(article_id,version,title,categories,publisher,scope,kind,status,reference_url,checked_on,review_due) values
  ('rm-change',1,'Changes to your relationship manager',array['relationship'],'DemoBank coursework project','Fictional demo guidance; not a live bank policy.','demo','published',null,null,null);
insert into kb.sections(article_id,version,anchor,heading,body,keywords) values
  ('rm-change',1,'team-restructure','Why your relationship manager changed','Your relationship manager changed because DemoBank reorganised its private client advisory teams. DemoBank does not share further details about individual staff members.','relationship manager changed change why leave left reassigned new old previous advisor team reorganised restructure'),
  ('rm-change',1,'continuity','What stays the same after a change','A change of relationship manager does not by itself change your accounts, products, fees or standing instructions. Your new relationship manager can see the requests and appointments recorded for you. Requests you sent earlier keep their recorded status; they are not marked as transferred unless that is recorded. DemoBank will tell you separately about any other change. This assistant cannot promise other arrangements, response times or outcomes.','same stay continue continuity accounts products fees standing instructions requests history transferred'),
  ('rm-change',1,'unrecorded','No approved explanation is recorded','DemoBank has not recorded an approved explanation for this change. For more detail, contact your relationship manager using the recorded details or use Talk to a person.','reason explanation unknown not recorded why'),
  ('rm-change',1,'no-advisor','When no relationship manager is shown','Your new relationship manager''s details are not available here yet. Use Talk to a person, or contact DemoBank through its official app or website. Do not rely on contact details from other sources.','unavailable missing no advisor details contact support');

-- Approved wording for each recorded transition reason. Reference data; customers read it only through the RPC below.
create table rm.transition_notices (
  reason_code text primary key check (reason_code ~ '^[a-z][a-z0-9_]{0,39}$'),
  article_id text not null,
  version int not null,
  anchor text not null,
  foreign key (article_id,version,anchor) references kb.sections on delete restrict
);
insert into rm.transition_notices(reason_code,article_id,version,anchor) values ('team_restructure','rm-change',1,'team-restructure');
alter table rm.transition_notices enable row level security;
revoke all on rm.transition_notices from public,anon,authenticated;
grant select on rm.transition_notices to authenticated;
create policy approved_notices on rm.transition_notices for select to authenticated using (true);

-- Anything the customer must do for a request, recorded by the bank. Null means nothing is recorded.
alter table rm.requests add column customer_action text check (customer_action is null or length(btrim(customer_action)) between 1 and 300);
update rm.requests set customer_action='Confirm the new quarterly transfer amount with your relationship manager'
  where request_id='00000000-0000-4000-8000-100300000031' and customer_action is null;

-- Joining details the bank records for each advisor and meeting format (synthetic; no live links).
create table rm.meeting_options (
  advisor_id uuid not null references rm.advisors(advisor_id) on delete cascade,
  format text not null check (format in ('video','phone','in_person')),
  joining_details text not null check (length(btrim(joining_details)) between 1 and 500),
  primary key (advisor_id,format)
);
alter table rm.meeting_options enable row level security;
revoke all on rm.meeting_options from public,anon,authenticated;
grant select on rm.meeting_options to authenticated;
create policy my_advisor on rm.meeting_options for select to authenticated using (rm.is_my_advisor(advisor_id));
insert into rm.meeting_options(advisor_id,format,joining_details) values
  ('00000000-0000-4000-8000-100300000002','video','Join from Appointments in the DemoBank app at the booked time. Demo meeting room: DEMO-MC-ROOM-1 (synthetic, no live video service).'),
  ('00000000-0000-4000-8000-100300000002','phone','Marcus Chua (DEMO) calls the phone number recorded on your profile at the booked time.'),
  ('00000000-0000-4000-8000-100300000002','in_person','DemoBank Private Clients Centre (DEMO), Level 10, 1 Demo Plaza, Singapore. Show your appointment at reception.')
on conflict do nothing;

create or replace function public.customer_read_requests(p_customer_id bigint)
returns jsonb language plpgsql volatile security invoker set search_path=''
as $$
declare cid bigint:=banking.require_read_customer(p_customer_id); data jsonb;
begin
  select coalesce(jsonb_agg(to_jsonb(r) order by r.created_at desc,r.request_id),'[]') into data from(
    select customer_id,request_id,advisor_id,who,what,urgency,deadline,country,currency,status,customer_action,created_at,updated_at
    from rm.requests where customer_id=cid order by created_at desc,request_id limit 200)r;
  return banking.customer_result(cid,'requests.list','rm.requests',data);
end $$;

-- Current advisor introduction plus approved change wording, in one audited statement snapshot.
create function public.customer_read_introduction(p_customer_id bigint)
returns jsonb language plpgsql volatile security invoker set search_path=''
as $$
declare cid bigint:=banking.require_read_customer(p_customer_id); cur record; prev record; anchors text[]; data jsonb;
begin
  if (select count(*) from rm.assignments where customer_id=cid and ended_on is null)>1 then
    raise exception 'more than one current advisor assignment was returned' using errcode='23514';
  end if;
  select a.advisor_id,a.started_on,a.transition_reason_code,r.full_name,r.role,r.team_name,r.email,r.phone into cur
    from rm.assignments a join rm.advisors r on r.advisor_id=a.advisor_id
    where a.customer_id=cid and a.ended_on is null order by a.started_on desc,a.assignment_id limit 1;
  select a.started_on,a.ended_on,r.full_name,r.role,r.team_name into prev
    from rm.assignments a join rm.advisors r on r.advisor_id=a.advisor_id
    where a.customer_id=cid and a.ended_on is not null order by a.ended_on desc,a.started_on desc,a.assignment_id limit 1;
  if cur.advisor_id is null then anchors:=array['no-advisor'];
  elsif prev.full_name is not null then
    anchors:=array[coalesce((select n.anchor from rm.transition_notices n where n.reason_code=cur.transition_reason_code
      and n.article_id='rm-change' and n.version=1),'unrecorded'),'continuity'];
  else anchors:=array[]::text[];
  end if;
  data:=jsonb_build_object(
    'current',case when cur.advisor_id is null then null else jsonb_build_object('rm_id',cur.advisor_id,'name',cur.full_name,'role',cur.role,
      'team',cur.team_name,'contact',jsonb_build_object('email',cur.email,'phone',cur.phone),'started_on',cur.started_on) end,
    'previous',case when prev.full_name is null then null else jsonb_build_object('name',prev.full_name,'role',prev.role,'team',prev.team_name,
      'started_on',prev.started_on,'ended_on',prev.ended_on) end,
    'notice',coalesce((select jsonb_agg(jsonb_build_object('cite','kb:'||s.article_id||'@v'||s.version||'#'||s.anchor,'article_id',s.article_id,
      'version',s.version,'anchor',s.anchor,'title',a.title,'heading',s.heading,'body',s.body,'publisher',a.publisher) order by x.ord)
      from unnest(anchors) with ordinality x(anchor,ord) join kb.sections s on s.article_id='rm-change' and s.version=1 and s.anchor=x.anchor
      join kb.articles a on a.article_id=s.article_id and a.version=s.version and a.status='published'),'[]'));
  if jsonb_array_length(data->'notice')<>coalesce(array_length(anchors,1),0) then
    raise exception 'approved relationship change wording is unavailable' using errcode='23514';
  end if;
  return banking.customer_result(cid,'rm.get_introduction','rm.assignments',data);
end $$;

-- Recorded joining details for the current advisor only.
create function public.customer_read_meeting_options(p_customer_id bigint)
returns jsonb language plpgsql volatile security invoker set search_path=''
as $$
declare cid bigint:=banking.require_read_customer(p_customer_id); data jsonb;
begin
  select coalesce(jsonb_agg(jsonb_build_object('advisor_id',o.advisor_id,'format',o.format,'joining_details',o.joining_details) order by o.format),'[]') into data
    from rm.meeting_options o join rm.assignments a on a.advisor_id=o.advisor_id and a.customer_id=cid and a.ended_on is null;
  return banking.customer_result(cid,'appointments.meeting_options','rm.meeting_options',data);
end $$;

revoke all on function public.customer_read_introduction(bigint),public.customer_read_meeting_options(bigint) from public,anon;
grant execute on function public.customer_read_introduction(bigint),public.customer_read_meeting_options(bigint) to authenticated;
