-- Durable redacted turns. Guest session IDs are not credentials: a separate 256-bit capability is required.
alter table conversation.sessions add column revision bigint not null default 0 check (revision>=0);
alter table conversation.sessions add column mode text not null default 'public' check (mode in ('public','personal'));
alter table conversation.sessions add column language text not null default 'English' check (language in ('English','Chinese','Malay','Tamil'));
alter table conversation.messages add column sequence bigint generated always as identity;
alter table conversation.messages add column payload jsonb;
create unique index messages_sequence_idx on conversation.messages(sequence);

create table conversation.guest_access (
  session_id uuid primary key references conversation.sessions on delete cascade,
  key_hash text not null check (key_hash ~ '^[a-f0-9]{64}$')
);
create table conversation.turns (
  turn_id uuid primary key,
  session_id uuid not null references conversation.sessions on delete cascade,
  customer_id bigint references banking.customer_profiles(customer_id) on delete cascade,
  case_id uuid not null references conversation.cases,
  user_message_id uuid not null unique references conversation.messages,
  answer_message_id uuid unique references conversation.messages,
  state text not null check (state in ('processing','completed','failed')),
  run_id uuid not null,
  request_hash text not null,
  revision bigint not null,
  created_at timestamptz not null default clock_timestamp()
);
alter table conversation.sessions add column active_turn_id uuid references conversation.turns on delete set null;
alter table conversation.guest_access enable row level security;
alter table conversation.turns enable row level security;
revoke all on conversation.guest_access,conversation.turns from public,anon,authenticated;
grant select on conversation.turns to authenticated;
create policy own_rows on conversation.turns for select to authenticated using (customer_id=banking.my_customer_id());

create function conversation.require_access(p_session_id uuid,p_guest_token text)
returns bigint language plpgsql volatile security definer set search_path=''
as $$
declare s conversation.sessions; own_customer bigint:=banking.my_customer_id();
begin
  select * into s from conversation.sessions where session_id=p_session_id;
  if not found or s.expires_at<=clock_timestamp() then
    raise exception 'conversation not found or expired' using errcode='P0002';
  end if;
  if auth.uid() is not null then
    if own_customer is null or s.customer_id is distinct from own_customer then
      raise exception 'conversation not found or expired' using errcode='P0002';
    end if;
  elsif s.customer_id is not null or p_guest_token is null or p_guest_token !~ '^[A-Za-z0-9_-]{43}$'
      or not exists(select 1 from conversation.guest_access a where a.session_id=p_session_id
        and a.key_hash=encode(sha256(convert_to(p_guest_token,'UTF8')),'hex')) then
    raise exception 'conversation not found or expired' using errcode='P0002';
  end if;
  return s.customer_id;
end $$;

create function public.conversation_start_turn(p_session_id uuid,p_turn_id uuid,p_revision bigint,p_user_text text,p_priority text,p_run_id uuid,p_guest_token text)
returns jsonb language plpgsql volatile security definer set search_path=''
as $$
declare cid bigint:=conversation.require_access(p_session_id,p_guest_token); s conversation.sessions;
  t conversation.turns; v_user_id uuid:=gen_random_uuid(); v_case_id uuid:=gen_random_uuid();
begin
  if p_turn_id is null or p_run_id is null or p_revision is null or p_user_text is null
      or length(btrim(p_user_text)) not between 1 and 4000 or p_priority is null or p_priority not in ('P0','P1','P2','P3') then
    raise exception 'conversation turn requires bounded redacted text and a revision' using errcode='22023';
  end if;
  perform conversation.require_redacted(p_user_text);
  select * into s from conversation.sessions where session_id=p_session_id for update;
  perform conversation.require_access(p_session_id,p_guest_token);
  select * into t from conversation.turns where turn_id=p_turn_id;
  if found then
    if t.session_id<>p_session_id or t.customer_id is distinct from cid
        or t.request_hash<>encode(sha256(convert_to(p_user_text,'UTF8')),'hex') then
      raise exception 'turn id already belongs to another request' using errcode='23505';
    end if;
    if t.state='processing' and t.run_id<>p_run_id then
      raise exception 'this turn is processing; reopen it or cancel it before retrying' using errcode='55P03';
    end if;
    return jsonb_build_object('turn',to_jsonb(t),'response',(select payload from conversation.messages where message_id=t.answer_message_id));
  end if;
  if s.active_turn_id is not null then raise exception 'another turn is processing; reopen it or cancel it first' using errcode='55P03'; end if;
  if s.revision<>p_revision then raise exception 'conversation changed; reload its latest revision before retrying' using errcode='23505'; end if;
  insert into conversation.cases(case_id,session_id,customer_id,topic,priority) values(v_case_id,p_session_id,cid,'chat_turn',p_priority);
  insert into conversation.messages(message_id,session_id,customer_id,role,redacted_text,payload) values(v_user_id,p_session_id,cid,'user',p_user_text,null);
  insert into conversation.turns(turn_id,session_id,customer_id,case_id,user_message_id,request_hash,revision,state,run_id)
    values(p_turn_id,p_session_id,cid,v_case_id,v_user_id,encode(sha256(convert_to(p_user_text,'UTF8')),'hex'),s.revision+1,'processing',p_run_id) returning * into t;
  update conversation.sessions set revision=revision+1,active_turn_id=p_turn_id,last_active_at=clock_timestamp() where session_id=p_session_id;
  perform audit.record('chat','chat.turn_started','success','conversation.turns',p_turn_id::text,
    jsonb_build_object('message_count',1,'revision',s.revision+1),v_case_id,p_session_id);
  return jsonb_build_object('turn',to_jsonb(t),'response',null);
end $$;

create function public.conversation_finish_turn(p_session_id uuid,p_turn_id uuid,p_run_id uuid,p_response jsonb,p_guest_token text)
returns jsonb language plpgsql volatile security definer set search_path=''
as $$
declare cid bigint:=conversation.require_access(p_session_id,p_guest_token); s conversation.sessions;
  t conversation.turns; v_answer_id uuid:=gen_random_uuid(); response_text text; next_state text;
begin
  select * into s from conversation.sessions where session_id=p_session_id for update;
  perform conversation.require_access(p_session_id,p_guest_token);
  select * into t from conversation.turns where turn_id=p_turn_id and session_id=p_session_id;
  if not found then raise exception 'conversation turn not found' using errcode='P0002'; end if;
  if t.state<>'processing' then
    return jsonb_build_object('turn',to_jsonb(t),'response',(select payload from conversation.messages where message_id=t.answer_message_id));
  end if;
  if t.run_id is distinct from p_run_id or s.active_turn_id is distinct from p_turn_id then
    raise exception 'the active conversation turn changed' using errcode='23505';
  end if;
  if p_response is null or jsonb_typeof(p_response)<>'object' or octet_length(p_response::text)>131072 then
    raise exception 'a bounded response object is required' using errcode='22023';
  end if;
  if p_response ? 'answer' then
    if jsonb_typeof(p_response->'answer') is distinct from 'string' or length(p_response->>'answer') not between 1 and 20000 then
      raise exception 'a bounded answer string is required' using errcode='22023';
    end if;
    response_text:=p_response->>'answer'; next_state:='completed';
  elsif p_response ? 'error' then
    if (p_response#>>'{error,code}') is null or (p_response#>>'{error,code}') not in ('assistant_unavailable','cancelled','interrupted')
        or jsonb_typeof(p_response#>'{error,message}') is distinct from 'string' or length(p_response#>>'{error,message}') not between 1 and 500 then
      raise exception 'a bounded explicit error is required' using errcode='22023';
    end if;
    response_text:=p_response#>>'{error,message}'; next_state:='failed';
  else raise exception 'response must be an answer or explicit failure' using errcode='22023';
  end if;
  perform conversation.require_redacted(p_response::text);
  insert into conversation.messages(message_id,session_id,customer_id,role,redacted_text,payload)
    values(v_answer_id,p_session_id,cid,'assistant',response_text,p_response);
  update conversation.turns set answer_message_id=v_answer_id,state=next_state where turn_id=p_turn_id returning * into t;
  update conversation.sessions set active_turn_id=null,last_active_at=clock_timestamp() where session_id=p_session_id;
  perform audit.record('chat','chat.turn_saved',case when next_state='failed' then 'failed' else 'success' end,
    'conversation.turns',p_turn_id::text,jsonb_build_object('message_count',1,'revision',s.revision),t.case_id,p_session_id);
  return jsonb_build_object('turn',to_jsonb(t),'response',p_response);
end $$;

create function public.conversation_cancel_turn(p_session_id uuid,p_turn_id uuid,p_guest_token text)
returns jsonb language plpgsql volatile security definer set search_path=''
as $$
declare t conversation.turns;
begin
  perform conversation.require_access(p_session_id,p_guest_token);
  select * into t from conversation.turns where turn_id=p_turn_id and session_id=p_session_id;
  if not found then raise exception 'conversation turn not found' using errcode='P0002'; end if;
  return public.conversation_finish_turn(p_session_id,p_turn_id,t.run_id,
    '{"error":{"code":"cancelled","message":"This reply was cancelled. No AI answer was saved."}}'::jsonb,p_guest_token);
end $$;
revoke all on function conversation.require_access(uuid,text) from public,anon,authenticated;

-- Independent defence for callers bypassing the Python redactor. Rejected values never reach storage.
-- Keep these secret families aligned with services/privacy.py and the shared synthetic privacy fixtures.
create function conversation.require_redacted(p_text text)
returns void language plpgsql immutable set search_path=''
as $fn$
declare normalized text:=translate(normalize(p_text,NFKC),U&'\200b\200c\200d\feff',''); pattern text;
begin
  foreach pattern in array array[
    $rx$(?i)\m(password|passcode)\s*(is\s+|[:=]\s*)("[^"\n]+"|'[^'\n]+'|[^\s,;"']+)$rx$,
    $rx$(?i)\m(pin|otp|cvv|cvc|one[- ]time (password|code))\s*(is\s+|[:=]\s*)?([0-9][ -]?){2,9}[0-9]\M$rx$,
    $rx$(?<![0-9])([0-9][ -]?){13,19}(?![0-9])$rx$,
    $rx$(?i)\m[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\M$rx$,
    $rx$(?i)\m[STFGM][0-9]{7}[A-Z]\M$rx$
  ] loop
    if normalized ~ pattern then raise exception 'conversation content must be redacted before storage' using errcode='22023'; end if;
  end loop;
end $fn$;
revoke all on function conversation.require_redacted(text) from public,anon,authenticated;

create function public.conversation_create(p_session_id uuid,p_mode text,p_language text,p_guest_token text)
returns jsonb language plpgsql volatile security definer set search_path=''
as $$
declare cid bigint:=banking.my_customer_id(); s conversation.sessions; inserted int;
begin
  if p_session_id is null or p_mode is null or p_mode not in ('public','personal')
      or p_language is null or p_language not in ('English','Chinese','Malay','Tamil') then
    raise exception 'conversation requires an id, mode and supported language' using errcode='22023';
  end if;
  if auth.uid() is null then
    if p_mode<>'public' or p_guest_token is null or p_guest_token !~ '^[A-Za-z0-9_-]{43}$' then
      raise exception 'guest conversations require public mode and an access token' using errcode='42501';
    end if;
  elsif cid is null then raise exception 'no customer profile is linked to this login' using errcode='42501';
  end if;
  insert into conversation.sessions(session_id,customer_id,mode,language,expires_at)
    values(p_session_id,cid,p_mode,p_language,case when auth.uid() is null then clock_timestamp()+interval '24 hours' end)
    on conflict do nothing;
  get diagnostics inserted=row_count;
  if inserted=1 and auth.uid() is null then
    insert into conversation.guest_access values(p_session_id,encode(sha256(convert_to(p_guest_token,'UTF8')),'hex'));
  end if;
  perform conversation.require_access(p_session_id,p_guest_token);
  select * into s from conversation.sessions where session_id=p_session_id;
  if s.mode<>p_mode or s.language<>p_language then
    raise exception 'conversation id already belongs to another mode or language' using errcode='23505';
  end if;
  if inserted=1 then perform audit.record('chat','chat.session_created','success','conversation.sessions',p_session_id::text,'{}',null,p_session_id); end if;
  return to_jsonb(s);
end $$;

create function public.conversation_read_guest(p_session_id uuid,p_guest_token text)
returns jsonb language plpgsql volatile security definer set search_path=''
as $$
declare cid bigint:=conversation.require_access(p_session_id,p_guest_token); result jsonb; message_count int;
begin
  if auth.uid() is not null then raise exception 'guest conversation access requires a guest session' using errcode='42501'; end if;
  select count(*) into message_count from conversation.messages where session_id=p_session_id and customer_id is not distinct from cid and role in ('user','assistant');
  select jsonb_build_object('session',to_jsonb(s),'messages',coalesce((select jsonb_agg(to_jsonb(m) order by m.sequence)
    from (select message_id,role,redacted_text,created_at,sequence,payload from conversation.messages
      where session_id=p_session_id and customer_id is not distinct from cid and role in ('user','assistant') order by sequence desc limit 60) m),'[]'::jsonb),
    'history_trimmed',message_count>60,'active_turn',(select to_jsonb(t) from conversation.turns t where t.turn_id=s.active_turn_id)) into result from conversation.sessions s where s.session_id=p_session_id;
  perform audit.record('data','data.read','success','conversation.messages',p_session_id::text,
    jsonb_build_object('tool','conversation.read','message_count',least(message_count,60)),null,p_session_id);
  return result;
end $$;

revoke all on function public.conversation_create(uuid,text,text,text),public.conversation_read_guest(uuid,text),
  public.conversation_start_turn(uuid,uuid,bigint,text,text,uuid,text),public.conversation_finish_turn(uuid,uuid,uuid,jsonb,text),
  public.conversation_cancel_turn(uuid,uuid,text) from public;
grant execute on function public.conversation_create(uuid,text,text,text),public.conversation_read_guest(uuid,text),
  public.conversation_start_turn(uuid,uuid,bigint,text,text,uuid,text),public.conversation_finish_turn(uuid,uuid,uuid,jsonb,text),
  public.conversation_cancel_turn(uuid,uuid,text) to anon,authenticated;

-- Customer history reads remain invoker-scoped and are protected by the original customer RLS policies.
grant execute on function conversation.require_access(uuid,text) to authenticated;
create function public.conversation_read_customer(p_session_id uuid)
returns jsonb language plpgsql volatile security invoker set search_path=''
as $$
declare cid bigint:=conversation.require_access(p_session_id,null); result jsonb; message_count int;
begin
  select count(*) into message_count from conversation.messages where session_id=p_session_id and customer_id=cid and role in ('user','assistant');
  select jsonb_build_object('session',to_jsonb(s),'messages',coalesce((select jsonb_agg(to_jsonb(m) order by m.sequence)
    from (select message_id,role,redacted_text,created_at,sequence,payload from conversation.messages
      where session_id=p_session_id and customer_id=cid and role in ('user','assistant') order by sequence desc limit 60) m),'[]'::jsonb),
    'history_trimmed',message_count>60,'active_turn',(select to_jsonb(t) from conversation.turns t where t.turn_id=s.active_turn_id and t.customer_id=cid))
    into result from conversation.sessions s where s.session_id=p_session_id and s.customer_id=cid;
  if result is null then raise exception 'conversation not found or expired' using errcode='P0002'; end if;
  perform public.audit_record_batch(jsonb_build_array(jsonb_build_object('category','data','action','data.read','outcome','success',
    'resource_type','conversation.messages','resource_id',p_session_id::text,'session_id',p_session_id::text,
    'details',jsonb_build_object('tool','conversation.read','message_count',least(message_count,60)))));
  return result;
end $$;
revoke all on function public.conversation_read_customer(uuid) from public,anon;
grant execute on function public.conversation_read_customer(uuid) to authenticated;
