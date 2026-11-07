-- All source cues remain available. Bounded model chunks are private, fenced and removed with their transcript.
create or replace function meetings.validate_cues(p_cues jsonb,p_kind text) returns void language plpgsql set search_path='' as $$
declare cue jsonb;ids text[]:='{}';
begin
 if p_cues is null or jsonb_typeof(p_cues)<>'array' or jsonb_array_length(p_cues) not between 1 and 1000 or octet_length(p_cues::text)>128000
 then raise exception 'source requires 1-1000 redacted cues within 128000 UTF-8 bytes; no partial source is saved' using errcode='22023';end if;
 for cue in select * from jsonb_array_elements(p_cues) loop
  if (select array_agg(key order by key) from jsonb_object_keys(cue) key) is distinct from array['cue_id','speaker','start_seconds','text']
   or jsonb_typeof(cue->'cue_id') is distinct from 'string' or cue->>'cue_id'!~'^[a-z][a-z0-9_-]{0,39}$' or cue->>'cue_id'=any(ids)
   or jsonb_typeof(cue->'text') is distinct from 'string' or length(btrim(cue->>'text')) not between 1 and 2000
   or jsonb_typeof(cue->'speaker') is distinct from 'string' or cue->>'speaker' not in ('rm','client','unmapped')
  then raise exception 'invalid source cue identity, text or speaker' using errcode='22023';end if;
  if p_kind='transcript' and cue->'start_seconds'<>'null'::jsonb and (jsonb_typeof(cue->'start_seconds') is distinct from 'number' or cue->>'start_seconds'!~'^[0-9]{1,7}$')
  then raise exception 'transcript cues require a nonnegative integer position' using errcode='22023';end if;
  if p_kind='rm_notes' and cue->'start_seconds'<>'null'::jsonb then raise exception 'typed notes must not claim transcript timestamps' using errcode='22023';end if;
  perform conversation.require_redacted(cue->>'text');ids:=array_append(ids,cue->>'cue_id');
 end loop;
end $$;

alter function meetings.validate_content(jsonb,jsonb) rename to validate_content_before_long;
create function meetings.validate_content(p_cues jsonb,p_content jsonb) returns void language plpgsql set search_path='' as $$
declare item jsonb;first_summary jsonb;part jsonb;ids text[]:='{}';
begin
 if p_content is null or jsonb_typeof(p_content)<>'object' or octet_length(p_content::text)>128000
  or (select array_agg(key order by key) from jsonb_object_keys(p_content) key) is distinct from array['actions','client_recap','decisions','summary','title']
  or jsonb_typeof(p_content->'client_recap') is distinct from 'string' or length(btrim(p_content->>'client_recap')) not between 1 and 5000
  or jsonb_typeof(p_content->'summary') is distinct from 'array' or jsonb_array_length(p_content->'summary') not between 1 and 4
  or jsonb_typeof(p_content->'decisions') is distinct from 'array' or jsonb_array_length(p_content->'decisions')>10
  or jsonb_typeof(p_content->'actions') is distinct from 'array' or jsonb_array_length(p_content->'actions')>10
 then raise exception 'complete minutes require a bounded schema, at most ten decisions/actions and a recap within 5000 characters' using errcode='22023';end if;
 if (select sum(length(value->>'text'))+count(*)-1 from jsonb_array_elements(p_content->'summary'))>2000
 then raise exception 'combined summary exceeds the RM note limit of 2000 characters' using errcode='22023';end if;
 perform conversation.require_redacted(p_content->>'client_recap');
 first_summary:=p_content->'summary'->0;
 part:=jsonb_build_object('title',p_content->>'title','client_recap',first_summary->>'text','summary',jsonb_build_array(first_summary),'decisions','[]'::jsonb,'actions','[]'::jsonb);
 for item in select value from jsonb_array_elements(p_content->'summary') loop
  perform meetings.validate_content_before_long(p_cues,part||jsonb_build_object('summary',jsonb_build_array(item)));
 end loop;
 for item in select value from jsonb_array_elements(p_content->'decisions') loop
  perform meetings.validate_content_before_long(p_cues,part||jsonb_build_object('decisions',jsonb_build_array(item)));
 end loop;
 for item in select value from jsonb_array_elements(p_content->'actions') loop
  if item->>'action_id'=any(ids) then raise exception 'action IDs must be distinct' using errcode='22023';end if;
  ids:=array_append(ids,item->>'action_id');
  perform meetings.validate_content_before_long(p_cues,part||jsonb_build_object('actions',jsonb_build_array(item)));
 end loop;
end $$;

create table meetings.source_chunks (
 source_id uuid not null references meetings.sources(source_id),chunk_index int not null check(chunk_index between 0 and 31),
 prompt_id text not null,chunk_hash text not null,content jsonb not null,input_tokens int,output_tokens int,
 charged_tokens int not null check(charged_tokens>0),created_at timestamptz not null default clock_timestamp(),
 primary key(source_id,chunk_index,prompt_id)
);
alter table meetings.source_chunks enable row level security;
revoke all on meetings.source_chunks from public,anon,authenticated,demobank_meetings,demobank_meeting_ingress;

create function meetings.job_chunk(p_event bigint,p_token uuid,p_index int,p_prompt text,p_cue_ids text[]) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
declare context jsonb;c meetings.source_chunks;selected jsonb;hash text;
begin
 context:=meetings.job_context(p_event,p_token);
 select jsonb_agg(value order by ordinal) into selected from jsonb_array_elements(context->'cues') with ordinality as x(value,ordinal) where value->>'cue_id'=any(p_cue_ids);
 if p_index not between 0 and 31 or p_cue_ids is null or cardinality(p_cue_ids) not between 1 and 80
  or selected is null or jsonb_array_length(selected)<>cardinality(p_cue_ids) or octet_length(selected::text)>12000
 then raise exception 'requested chunk does not match bounded current source cues' using errcode='22023';end if;
 hash:=meetings.hash(selected);
 select * into c from meetings.source_chunks where source_id=(context->>'source_id')::uuid and chunk_index=p_index and prompt_id=p_prompt;
 if not found then return jsonb_build_object('chunk_hash',hash,'content',null,'charged_tokens',0);end if;
 if c.chunk_hash is distinct from hash then raise exception 'cached chunk does not match the selected evidence' using errcode='P1001';end if;
 return jsonb_build_object('chunk_hash',hash,'content',c.content,'charged_tokens',c.charged_tokens);
end $$;

create function meetings.save_job_chunk(p_event bigint,p_token uuid,p_epoch int,p_index int,p_prompt text,p_hash text,p_cue_ids text[],
 p_content jsonb,p_input_tokens int,p_output_tokens int,p_charged_tokens int) returns void
language plpgsql volatile security definer set search_path='' as $$
declare context jsonb;selected jsonb;c meetings.source_chunks;
begin
 context:=meetings.job_context(p_event,p_token);
 if p_epoch is distinct from (context->>'consent_epoch')::int or p_index not between 0 and 31 or p_prompt!~'^v[1-9][0-9]*\+[a-f0-9]{8}$'
  or p_cue_ids is null or cardinality(p_cue_ids) not between 1 and 80 or cardinality(p_cue_ids)<>(select count(distinct x) from unnest(p_cue_ids) x)
  or p_charged_tokens is null or p_charged_tokens<=0 or p_input_tokens<0 or p_output_tokens<0
 then raise exception 'chunk version, permission, identity or token accounting is invalid' using errcode='22023';end if;
 select jsonb_agg(value order by ordinal) into selected from jsonb_array_elements(context->'cues') with ordinality as c(value,ordinal) where value->>'cue_id'=any(p_cue_ids);
 if selected is null or jsonb_array_length(selected)<>cardinality(p_cue_ids) or octet_length(selected::text)>12000 or meetings.hash(selected) is distinct from p_hash
 then raise exception 'chunk must match its bounded current source cues' using errcode='P1001';end if;
 perform meetings.validate_content(selected,p_content);
 select * into c from meetings.source_chunks where source_id=(context->>'source_id')::uuid and chunk_index=p_index and prompt_id=p_prompt;
 if found then
  if c.chunk_hash<>p_hash or c.content<>p_content then raise exception 'a persisted chunk cannot be overwritten' using errcode='23505';end if;
  return;
 end if;
 insert into meetings.source_chunks(source_id,chunk_index,prompt_id,chunk_hash,content,input_tokens,output_tokens,charged_tokens)
 values((context->>'source_id')::uuid,p_index,p_prompt,p_hash,p_content,p_input_tokens,p_output_tokens,p_charged_tokens);
end $$;

create function meetings.purge_source_chunks() returns trigger language plpgsql security definer set search_path='' as $$
begin
 if new.revoked_at is not null or new.purged_at is not null then delete from meetings.source_chunks where source_id=new.source_id;end if;
 return new;
end $$;
create trigger purge_source_chunks after update of revoked_at,purged_at on meetings.sources for each row execute function meetings.purge_source_chunks();

-- The inbox remains a short notification. The shared workspace renders the complete exact-approved recap.
create or replace function public.meeting_share_recap(p_meeting_id uuid,p_draft_id uuid,p_version int,p_content_hash text,p_revision int,p_consent_epoch int,p_command_id uuid) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
declare d meetings.drafts;a rm.appointments;receipt jsonb;message uuid:=gen_random_uuid();params jsonb:=jsonb_build_array(p_draft_id,p_version,p_content_hash,p_revision,p_consent_epoch);
begin
 d:=meetings.require_draft(p_meeting_id,p_draft_id,p_version,p_content_hash,p_revision,p_consent_epoch);
 if d.note_id is null then raise exception 'approve the exact minutes before sharing the recap' using errcode='P1001';end if;
 receipt:=meetings.replay(p_command_id,p_meeting_id,'share_recap',params);if receipt is not null then return receipt;end if;
 select e.receipt into receipt from meetings.effects e where draft_id=d.draft_id and scope='recap' and target_key='client';
 if receipt is null then
  select * into a from rm.appointments where appointment_id=p_meeting_id;
  receipt:=public.rm_send_client_update(message,a.customer_id,null,d.content->'summary'->0->>'text',null,least(d.version,50),false);
  if receipt->>'denied'='true' then raise exception 'RM authorization changed before sharing' using errcode='42501';end if;
  receipt:=jsonb_build_object('message_id',message,'draft_id',d.draft_id,'shared',true);
  insert into meetings.effects values(d.draft_id,'recap','client',receipt);
 end if;
 return meetings.remember(p_command_id,p_meeting_id,'share_recap',params,receipt);
end $$;

alter function public.meeting_view(uuid) rename to meeting_view_before_long;
alter function public.meeting_view_before_long(uuid) set schema meetings;
revoke all on function meetings.meeting_view_before_long(uuid) from public,anon,authenticated;
create function public.meeting_view(p_meeting_id uuid) returns jsonb language plpgsql volatile security definer set search_path='' as $$
declare result jsonb;recap text;
begin
 result:=meetings.meeting_view_before_long(p_meeting_id);
 select d.content->>'client_recap' into recap from meetings.effects e join meetings.drafts d using(draft_id)
 where d.meeting_id=p_meeting_id and e.scope='recap' and e.target_key='client' order by d.version desc limit 1;
 return result||jsonb_build_object('shared_recap',recap);
end $$;
revoke all on function public.meeting_view(uuid) from public,anon;
grant execute on function public.meeting_view(uuid) to authenticated;
revoke all on function meetings.validate_content_before_long(jsonb,jsonb),meetings.validate_content(jsonb,jsonb),meetings.validate_cues(jsonb,text),
 meetings.job_chunk(bigint,uuid,int,text,text[]),meetings.save_job_chunk(bigint,uuid,int,int,text,text,text[],jsonb,int,int,int),meetings.purge_source_chunks()
 from public,anon,authenticated,demobank_meeting_ingress,demobank_meetings;
grant execute on function meetings.job_chunk(bigint,uuid,int,text,text[]),meetings.save_job_chunk(bigint,uuid,int,int,text,text,text[],jsonb,int,int,int) to demobank_meetings;
