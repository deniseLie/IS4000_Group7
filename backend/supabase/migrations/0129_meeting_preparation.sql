-- RM-only preparation. Persistent briefs contain a plan, source references and grant IDs, never gated record text.
create table meetings.briefs (
 brief_id uuid primary key,meeting_id uuid not null references meetings.workflows(meeting_id),version int not null,
 revision int not null,source_hash text not null,content_hash text not null,plan jsonb not null,source_manifest jsonb not null,
 grants_used jsonb not null,status text not null check(status in ('draft','approved','changes_requested','rejected')),
 review_note text,approved_at timestamptz,created_at timestamptz not null default clock_timestamp(),unique(meeting_id,version)
);
alter table meetings.briefs enable row level security;
revoke all on meetings.briefs from public,anon,authenticated,demobank_meetings,demobank_meeting_ingress;

create function meetings.brief_source_key(p_meeting uuid,p_reader text,p_record text) returns uuid language sql immutable set search_path='' as $$
 select substr(meetings.hash(jsonb_build_array(p_meeting,p_reader,p_record)),1,32)::uuid
$$;
create function meetings.brief_source(p_meeting uuid,p_reader text,p_record text,p_call text,p_text text,p_category text,p_grant uuid) returns jsonb
language sql immutable set search_path='' as $$
 select jsonb_build_object('source_id',meetings.brief_source_key(p_meeting,p_reader,p_record),'label','record','text',p_text,
  'reference',p_reader||':'||p_call,'category',p_category,'grant_id',p_grant)
$$;

create function public.meeting_preparation_inputs(p_meeting_id uuid) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
declare w meetings.workflows;a rm.appointments;profile jsonb;reviews jsonb;tickets jsonb;notes jsonb;tx jsonb;points jsonb;
 sources jsonb:='[]';point_refs jsonb:='[]';gaps jsonb:='[]';removed jsonb:='[]';row jsonb;item jsonb;position int;key uuid;
 frame jsonb;hash_input jsonb;current_brief meetings.briefs;point_status jsonb;point_ids uuid[];
begin
 w:=meetings.require_rm(p_meeting_id);select * into a from rm.appointments where appointment_id=p_meeting_id;
 profile:=public.rm_client_profile(a.customer_id);reviews:=public.rm_review_status();tickets:=public.rm_tickets();notes:=public.rm_meeting_notes(a.customer_id);
 tx:=public.rm_transaction_insights(a.customer_id,'meeting_prep',90);points:=public.rm_talking_points(a.customer_id,'meeting_prep');
 if jsonb_typeof(profile->'data'->'profile') is distinct from 'object'
  or jsonb_typeof(profile->'data'->'accounts') is distinct from 'array' or jsonb_typeof(profile->'data'->'cards') is distinct from 'array'
  or jsonb_typeof(reviews->'data') is distinct from 'array' or jsonb_typeof(tickets->'data'->'rm_owned') is distinct from 'array'
  or jsonb_typeof(tickets->'data'->'specialist') is distinct from 'array' or jsonb_typeof(notes->'data'->'notes') is distinct from 'array'
  or jsonb_typeof(tx->'shared') is distinct from 'boolean' or jsonb_typeof(points->'shared') is distinct from 'boolean'
  or jsonb_typeof(points->'data') is distinct from 'array'
 then raise exception 'a required scoped preparation reader returned an invalid data shape' using errcode='22023';end if;
 if (tx->>'shared')::boolean and jsonb_typeof(tx->'data'->'monthly') is distinct from 'array'
 then raise exception 'shared transaction insights require the bounded monthly aggregate collection' using errcode='22023';end if;
 if profile->'data'->'profile'->>'name' is null then raise exception 'owned client profile is missing its required name' using errcode='22023';end if;
 sources:=sources||jsonb_build_array(meetings.brief_source(p_meeting_id,'rm_client_profile','profile',profile->>'call_id',
  format('Client: %s. Preferred language: %s. Employment: %s.',profile->'data'->'profile'->>'name',profile->'data'->'profile'->>'preferred_language',coalesce(profile->'data'->'profile'->>'employment_status','not recorded')),null,null));
 for row in select value from jsonb_array_elements(profile->'data'->'accounts') loop
  sources:=sources||jsonb_build_array(meetings.brief_source(p_meeting_id,'rm_client_profile','account:'||(row->>'number'),profile->>'call_id',
   format('Account product: %s, %s, status %s.',row->>'type',row->>'currency',row->>'status'),null,null));
 end loop;
 for row in select value from jsonb_array_elements(profile->'data'->'cards') loop
  sources:=sources||jsonb_build_array(meetings.brief_source(p_meeting_id,'rm_client_profile','card:'||(row->>'last4'),profile->>'call_id',
   format('Card product: %s, %s, status %s.',row->>'name',row->>'currency',row->>'status'),null,null));
 end loop;
 for row in select value from jsonb_array_elements(reviews->'data') where (value->>'customer_id')::bigint=a.customer_id loop
  sources:=sources||jsonb_build_array(meetings.brief_source(p_meeting_id,'rm_review_status','review:'||(row->>'title'),reviews->>'call_id',
   format('Compliance review: %s. Recorded status: %s. Due: %s.',row->>'title',row->>'status',coalesce(row->>'due_date','not recorded')),null,null));
  for item in select value from jsonb_array_elements(row->'outstanding_items') loop
   sources:=sources||jsonb_build_array(meetings.brief_source(p_meeting_id,'rm_review_status','item:'||(row->>'title')||':'||(item#>>'{}'),reviews->>'call_id',
    'Outstanding compliance requirement: '||(item#>>'{}'),null,null));
  end loop;
 end loop;
 for row in select value from jsonb_array_elements((tickets->'data'->'rm_owned')||(tickets->'data'->'specialist')) where (value->>'customer_id')::bigint=a.customer_id loop
  sources:=sources||jsonb_build_array(meetings.brief_source(p_meeting_id,'rm_tickets',row->>'reference',tickets->>'call_id',
   format('Request %s: %s. Recorded status: %s.',row->>'reference',row->>'category_label',row->>'status'),null,null));
 end loop;
 for row in select value from jsonb_array_elements(notes->'data'->'notes') with ordinality as n(value,ordinal) where ordinal<=5 loop
  for position in select generate_series(1,greatest(1,length(row->>'summary')),700) loop
   sources:=sources||jsonb_build_array(meetings.brief_source(p_meeting_id,'rm_meeting_notes',(row->>'note_id')||':'||position,notes->>'call_id',
    format('Meeting note dated %s: %s',row->>'met_on',substring(row->>'summary' from position for 700)),null,null));
  end loop;
 end loop;
 if (tx->>'shared')::boolean then
  for row in select value from jsonb_array_elements(tx->'data'->'monthly') loop
   sources:=sources||jsonb_build_array(meetings.brief_source(p_meeting_id,'rm_transaction_insights',(row->>'month')||':'||(row->>'currency'),tx->>'call_id',
    format('Shared monthly transaction totals for %s: %s incoming %s, outgoing %s, across %s transactions.',row->>'month',row->>'currency',row->>'money_in',row->>'money_out',row->>'count'),
    'transaction_insights',(tx->>'grant_id')::uuid));
  end loop;
 else gaps:=gaps||jsonb_build_array(jsonb_build_object('category','transaction_insights','text','Transaction insights are not shared for this preparation.'));end if;
 for row in select value from jsonb_array_elements(points->'data') loop
  point_refs:=point_refs||jsonb_build_array(jsonb_build_object('source_id',meetings.brief_source_key(p_meeting_id,'rm_talking_points',row->>'point_id'),
   'point',row,'reference','rm_talking_points:'||(points->>'call_id'),'category',case when row->>'origin'='inferred' then 'chat_topics' else null end,
   'grant_id',case when row->>'origin'='inferred' then points->'grant_id' else 'null'::jsonb end));
 end loop;
 if not (points->>'shared')::boolean then gaps:=gaps||jsonb_build_array(jsonb_build_object('category','chat_topics','text','Inferred chat topics are not shared. Only topics the client explicitly shared may be included.'));end if;
 if (points->>'pending')::boolean then gaps:=gaps||jsonb_build_array(jsonb_build_object('category','chat_topics','text','Some inferred topics are on hold or paused; their content is not available.'));end if;
 select * into current_brief from meetings.briefs where meeting_id=p_meeting_id order by version desc limit 1;
 if found then
  select array_agg((x->>'point_id')::uuid) into point_ids from jsonb_array_elements(current_brief.source_manifest) x where x->>'point_id' is not null;
  if cardinality(point_ids)>0 then point_status:=public.rm_point_status(a.customer_id,point_ids);end if;
  for row in select value from jsonb_array_elements(current_brief.source_manifest) loop
   key:=(row->>'source_id')::uuid;
   if not exists(select 1 from jsonb_array_elements(sources||point_refs) x where (x->>'source_id')::uuid=key) then
    item:=null;
    if row->>'point_id' is not null then
     select jsonb_build_object('source_id',key,'removed_on',p->>'removed_on','reason','removed') into item
     from jsonb_array_elements(point_status->'data') p where p->>'point_id'=row->>'point_id' and p->>'status'='removed';
    end if;
    if item is null and row->>'grant_id' is not null then
     select jsonb_build_object('source_id',key,'removed_on',(coalesce(g.revoked_at,g.expires_at) at time zone 'Asia/Singapore')::date,'reason',
      case when g.revoked_at is not null and g.end_reason='revoked' then 'removed' else 'access_ended' end)
     into item from consent.grants g where g.grant_id=(row->>'grant_id')::uuid and g.customer_id=a.customer_id and g.recipient=a.advisor_id::text;
    end if;
    if item is not null then removed:=removed||jsonb_build_array(item);end if;
   end if;
  end loop;
 end if;
 hash_input:=jsonb_build_object('revision',w.revision,'purpose',a.purpose,'topics',a.topics,
  'sources',(select coalesce(jsonb_agg(x-'reference'),'[]') from jsonb_array_elements(sources) x),
  'points',(select coalesce(jsonb_agg(x-'reference'),'[]') from jsonb_array_elements(point_refs) x),
  'gaps',gaps,'grants_used',jsonb_build_object('transaction_insights',tx->'grant_id','chat_topics',points->'grant_id'),
  'consent',jsonb_build_object('transaction_insights',(tx->>'shared')::boolean,'chat_topics',(points->>'shared')::boolean));
 frame:=jsonb_build_object('meeting_id',p_meeting_id,'revision',w.revision,'purpose',a.purpose,'topics',a.topics,'sources',sources,
  'talking_points',point_refs,'gaps',gaps,'removed_sources',removed,'source_hash',meetings.hash(hash_input),
  'consent',(hash_input->'consent')||jsonb_build_object('meeting_recording',meetings.recording_allowed(p_meeting_id)),
  'grants_used',jsonb_build_object('transaction_insights',tx->'grant_id','chat_topics',points->'grant_id'),'as_of',clock_timestamp());
 return frame;
end $$;

create function public.meeting_current_brief(p_meeting_id uuid) returns jsonb language plpgsql volatile security definer set search_path='' as $$
declare b meetings.briefs;
begin
 perform meetings.require_rm(p_meeting_id);
 select * into b from meetings.briefs where meeting_id=p_meeting_id order by version desc limit 1;
 if not found then return null;end if;
 return to_jsonb(b);
end $$;

create function public.meeting_save_brief(p_meeting_id uuid,p_revision int,p_source_hash text,p_plan jsonb,p_command_id uuid) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
declare w meetings.workflows;inputs jsonb;section jsonb;item jsonb;known jsonb;selected jsonb:='[]';ids text[]:='{}';manifest jsonb;
 b meetings.briefs;receipt jsonb;params jsonb:=jsonb_build_array(p_revision,p_source_hash,p_plan);next_version int;
begin
 w:=meetings.require_rm(p_meeting_id);
 receipt:=meetings.replay(p_command_id,p_meeting_id,'save_brief',params);if receipt is not null then return receipt;end if;
 inputs:=public.meeting_preparation_inputs(p_meeting_id);
 if p_revision is distinct from w.revision or inputs->>'source_hash' is distinct from p_source_hash then raise exception 'preparation records or permission changed during generation; refresh the brief' using errcode='P1001';end if;
 known:=(inputs->'sources')||(inputs->'talking_points');
 if p_plan is null or (select array_agg(key) from jsonb_object_keys(p_plan) key) is distinct from array['sections']
  or jsonb_typeof(p_plan->'sections') is distinct from 'array' or jsonb_array_length(p_plan->'sections') not between 1 and 7
 then raise exception 'brief plan must contain bounded source-reference sections' using errcode='22023';end if;
 for section in select value from jsonb_array_elements(p_plan->'sections') loop
  if (select array_agg(key order by key) from jsonb_object_keys(section) key) is distinct from array['heading','items']
   or section->>'heading' not in ('profile','compliance','requests','history','shared_topics','transaction_insights','questions')
   or jsonb_typeof(section->'items') is distinct from 'array' or jsonb_array_length(section->'items') not between 1 and 30
  then raise exception 'invalid brief section' using errcode='22023';end if;
  for item in select value from jsonb_array_elements(section->'items') loop
   if (select array_agg(key order by key) from jsonb_object_keys(item) key) is distinct from array['kind','question','source_id']
    or item->>'kind' not in ('record','question') or not exists(select 1 from jsonb_array_elements(known) x where x->>'source_id'=item->>'source_id')
    or (item->>'source_id')||':'||(item->>'kind')=any(ids)
    or (item->>'kind'='record' and item->'question'<>'null'::jsonb)
    or (item->>'kind'='question' and coalesce(item->>'question','') not in ('confirm_goal','review_status','clarify_next_step','discuss_shared_topic','check_information','compare_product_terms','confirm_document_next_step'))
   then raise exception 'brief items must cite unique available sources and supported question codes' using errcode='22023';end if;
   ids:=array_append(ids,(item->>'source_id')||':'||(item->>'kind'));selected:=selected||jsonb_build_array(item->'source_id');
  end loop;
 end loop;
 if cardinality(ids)>30 then raise exception 'brief supports at most thirty selected source items' using errcode='22023';end if;
 select coalesce(jsonb_agg(jsonb_build_object('source_id',x->'source_id','grant_id',x->'grant_id','category',x->'category','point_id',x->'point'->'point_id')),'[]') into manifest
 from jsonb_array_elements(known) x where selected ? (x->>'source_id');
 select coalesce(max(stored.version),0)+1 into next_version from meetings.briefs stored where meeting_id=p_meeting_id;
 insert into meetings.briefs(brief_id,meeting_id,version,revision,source_hash,content_hash,plan,source_manifest,grants_used,status)
 values(p_command_id,p_meeting_id,next_version,w.revision,p_source_hash,meetings.hash(jsonb_build_array(p_source_hash,p_plan)),p_plan,manifest,inputs->'grants_used','draft') returning * into b;
 return meetings.remember(p_command_id,p_meeting_id,'save_brief',params,to_jsonb(b));
end $$;

create function public.meeting_decide_brief(p_meeting_id uuid,p_version int,p_content_hash text,p_revision int,p_outcome text,p_note text,p_command_id uuid) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
declare w meetings.workflows;b meetings.briefs;inputs jsonb;receipt jsonb;params jsonb:=jsonb_build_array(p_version,p_content_hash,p_revision,p_outcome,p_note);
begin
 w:=meetings.require_rm(p_meeting_id);
 receipt:=meetings.replay(p_command_id,p_meeting_id,'decide_brief',params);if receipt is not null then return receipt;end if;
 select * into b from meetings.briefs where meeting_id=p_meeting_id order by version desc limit 1;
 if not found or b.version is distinct from p_version or b.content_hash is distinct from p_content_hash or w.revision is distinct from p_revision or b.revision<>w.revision
 then raise exception 'approve or request changes to the exact current brief version' using errcode='P1001';end if;
 if p_outcome is null or p_outcome not in ('approve','changes','reject') or (p_outcome='changes' and nullif(btrim(p_note),'') is null) or length(p_note)>1000
 then raise exception 'a supported decision and a reason for changes are required' using errcode='22023';end if;
 if p_note is not null then perform conversation.require_redacted(p_note);end if;
 if p_outcome='approve' and b.status in ('changes_requested','rejected') then
  raise exception 'this brief was returned or rejected; create and review a refreshed version before approval' using errcode='P1001';end if;
 inputs:=public.meeting_preparation_inputs(p_meeting_id);
 if p_outcome='approve' and b.source_hash<>inputs->>'source_hash' then raise exception 'brief sources changed; refresh the brief before approval' using errcode='P1001';end if;
 update meetings.briefs set status=case p_outcome when 'approve' then 'approved' when 'changes' then 'changes_requested' else 'rejected' end,
  review_note=p_note,approved_at=case when p_outcome='approve' then clock_timestamp() end where brief_id=b.brief_id returning * into b;
 perform audit.record('document','document.meeting_brief_reviewed','success','meetings.briefs',b.brief_id::text,
  jsonb_build_object('meeting_id',p_meeting_id,'version',p_version,'outcome',p_outcome),p_actor_type=>'staff',p_actor_id=>auth.uid()::text,
  p_customer_id=>(select customer_id from rm.appointments where appointment_id=p_meeting_id));
 return meetings.remember(p_command_id,p_meeting_id,'decide_brief',params,to_jsonb(b));
end $$;

revoke all on function meetings.brief_source_key(uuid,text,text),meetings.brief_source(uuid,text,text,text,text,text,uuid) from public,anon,authenticated,demobank_meetings,demobank_meeting_ingress;
revoke all on function public.meeting_preparation_inputs(uuid),public.meeting_current_brief(uuid),public.meeting_save_brief(uuid,int,text,jsonb,uuid),public.meeting_decide_brief(uuid,int,text,int,text,text,uuid) from public,anon;
grant execute on function public.meeting_preparation_inputs(uuid),public.meeting_current_brief(uuid),public.meeting_save_brief(uuid,int,text,jsonb,uuid),public.meeting_decide_brief(uuid,int,text,int,text,text,uuid) to authenticated;
