-- Persona 2: saved RM assistant conversations, so Sophie can leave and come back to a conversation (like Recent chats
-- for clients). Each conversation belongs to one advisor and is readable only through these functions, which resolve the
-- advisor from the RM claim and staff login (rm.current_advisor, 0089). No client can read this table.
-- Turns keep the client they were about. When a conversation is reopened, turns about a client who is no longer in the
-- advisor's book (for example after a reassignment) are left out, so a previous RM cannot read an old conversation about
-- a client she no longer serves. Message text is redacted before it is saved (conversation.require_redacted).

create table rm.assistant_conversations (
  conversation_id uuid primary key,
  advisor_id      uuid not null references rm.advisors(advisor_id),
  title           text not null check (length(btrim(title)) between 1 and 120),
  client_ids      bigint[] not null default '{}' check (cardinality(client_ids)<=50),
  messages        jsonb not null default '[]' check (jsonb_typeof(messages)='array' and jsonb_array_length(messages)<=80),
  context         jsonb not null default '{}' check (jsonb_typeof(context)='object'),
  revision        int not null default 1 check (revision>=1),
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now(),
  check (pg_column_size(messages)<=2000000)
);
create index assistant_conversations_advisor_idx on rm.assistant_conversations(advisor_id,updated_at desc);
alter table rm.assistant_conversations enable row level security;
revoke all on rm.assistant_conversations from public,anon,authenticated;

-- The 50 most recent conversations: title, when, and which book clients they were about.
create function public.rm_conversations() returns jsonb language plpgsql volatile security definer set search_path='' as $$
declare advisor uuid:=rm.current_advisor();
begin
  -- A title can name a client; when the conversation touched a client no longer in the book, a neutral title is shown.
  return jsonb_build_object('as_of',clock_timestamp(),'data',(select coalesce(jsonb_agg(jsonb_build_object('conversation_id',c.conversation_id,
      'title',case when exists(select 1 from unnest(c.client_ids) x where not rm.in_book(advisor,x)) then 'Earlier conversation' else c.title end,'updated_at',c.updated_at,'created_at',c.created_at,'turns',jsonb_array_length(c.messages),
      'clients',(select coalesce(jsonb_agg(rm.display_name(p.full_name) order by p.full_name),'[]') from banking.customer_profiles p
        where p.customer_id=any(c.client_ids) and rm.in_book(advisor,p.customer_id))) order by c.updated_at desc),'[]')
    from (select * from rm.assistant_conversations where advisor_id=advisor order by updated_at desc limit 50) c));
end $$;

-- One conversation, without turns about clients outside the current book (and a context that no longer points at them).
create function public.rm_conversation(p_conversation_id uuid) returns jsonb language plpgsql volatile security definer set search_path='' as $$
declare advisor uuid:=rm.current_advisor(); c rm.assistant_conversations; kept jsonb; dropped int; ctx jsonb;
begin
  select * into c from rm.assistant_conversations where conversation_id=p_conversation_id and advisor_id=advisor;
  if not found then raise exception 'conversation not found' using errcode='P0002'; end if;
  select coalesce(jsonb_agg(m order by n),'[]') into kept from jsonb_array_elements(c.messages) with ordinality x(m,n)
    where m->'client_id' is null or m->>'client_id' is null or rm.in_book(advisor,(m->>'client_id')::bigint);
  dropped:=jsonb_array_length(c.messages)-jsonb_array_length(kept);
  ctx:=case when c.context->>'client_id' is not null and not rm.in_book(advisor,(c.context->>'client_id')::bigint) then '{}'::jsonb else c.context end;
  perform audit.record('data','data.read','success','rm.assistant_conversations',c.conversation_id::text,
    jsonb_build_object('tool','rm.conversation','result_count',jsonb_array_length(kept),'withheld',dropped),p_actor_type=>'staff',p_actor_id=>auth.uid()::text);
  return jsonb_build_object('conversation_id',c.conversation_id,'title',case when dropped>0 then 'Earlier conversation' else c.title end,'revision',c.revision,'updated_at',c.updated_at,
    'messages',kept,'context',ctx,'withheld_turns',dropped);
end $$;

-- Save (create or replace) a conversation. Every client a turn is about must be in the advisor's book.
create function public.rm_save_conversation(p_conversation_id uuid,p_title text,p_messages jsonb,p_context jsonb)
returns jsonb language plpgsql volatile security definer set search_path='' as $$
declare advisor uuid:=rm.current_advisor(); owner uuid; clients bigint[]; c rm.assistant_conversations;
begin
  if p_conversation_id is null or jsonb_typeof(p_messages) is distinct from 'array' or jsonb_typeof(coalesce(p_context,'{}')) is distinct from 'object' then
    raise exception 'a conversation needs an id, a list of messages and a context object' using errcode='22023';
  end if;
  select advisor_id into owner from rm.assistant_conversations where conversation_id=p_conversation_id;
  if found and owner<>advisor then raise exception 'conversation not found' using errcode='P0002'; end if;
  select coalesce(array_agg(distinct (m->>'client_id')::bigint),'{}') into clients from jsonb_array_elements(p_messages) m
    where m->>'client_id' is not null and m->>'client_id' ~ '^[0-9]{1,12}$';
  if exists(select 1 from unnest(clients) x where not rm.in_book(advisor,x)) then
    perform rm.deny((select x from unnest(clients) x where not rm.in_book(advisor,x) limit 1),'rm.save_conversation');
    raise exception 'a conversation can only be about clients in your book' using errcode='42501';
  end if;
  perform conversation.require_redacted(p_messages::text);
  insert into rm.assistant_conversations(conversation_id,advisor_id,title,client_ids,messages,context)
    values(p_conversation_id,advisor,rm.require_text(left(regexp_replace(coalesce(p_title,''),'\s+',' ','g'),120),120,'title'),clients,p_messages,coalesce(p_context,'{}'))
  on conflict (conversation_id) do update set title=excluded.title,client_ids=excluded.client_ids,messages=excluded.messages,context=excluded.context,
    revision=rm.assistant_conversations.revision+1,updated_at=clock_timestamp()
  returning * into c;
  return jsonb_build_object('conversation_id',c.conversation_id,'revision',c.revision,'updated_at',c.updated_at);
end $$;

create function public.rm_delete_conversation(p_conversation_id uuid) returns jsonb language plpgsql volatile security definer set search_path='' as $$
declare advisor uuid:=rm.current_advisor();
begin
  delete from rm.assistant_conversations where conversation_id=p_conversation_id and advisor_id=advisor;
  if not found then raise exception 'conversation not found' using errcode='P0002'; end if;
  return jsonb_build_object('deleted',true,'conversation_id',p_conversation_id);
end $$;

-- Keep only the 50 most recent conversations per advisor.
create function rm.trim_conversations() returns trigger language plpgsql security definer set search_path='' as $$
begin
  delete from rm.assistant_conversations where advisor_id=new.advisor_id and conversation_id in (
    select conversation_id from rm.assistant_conversations where advisor_id=new.advisor_id order by updated_at desc offset 50);
  return null;
end $$;
create trigger trim_conversations after insert on rm.assistant_conversations for each row execute function rm.trim_conversations();

do $$
declare f text;
begin
  foreach f in array array['rm_conversations()','rm_conversation(uuid)','rm_save_conversation(uuid,text,jsonb,jsonb)','rm_delete_conversation(uuid)'] loop
    execute format('revoke all on function public.%s from public,anon',f);
    execute format('grant execute on function public.%s to authenticated',f);
  end loop;
end $$;
revoke all on function rm.trim_conversations() from public,anon,authenticated;
