-- Client-safe discussion packs: only the agreed agenda, approved catalogue terms and explicit RM-entered amounts.
create table meetings.catalogue_terms (
 item_id uuid primary key,code text not null unique,name text not null,topic text not null check(topic=any(rm.meeting_topics())),
 version int not null check(version>0),currency text not null check(currency~'^[A-Z]{3}$'),
 annual_rate numeric(8,4),minimum_amount numeric(16,2),fee numeric(16,2),term_months int,
 effective_from date not null,effective_to date not null,status text not null check(status in ('approved','retired')),
 check(effective_to>=effective_from),check(annual_rate is null or annual_rate>=0),check(minimum_amount is null or minimum_amount>=0),
 check(fee is null or fee>=0),check(term_months is null or term_months>0)
);
insert into meetings.catalogue_terms values
 ('00000000-0000-4000-8000-200000000001','demo-fixed-deposit-sgd','Demo fixed deposit','fixed_deposit',1,'SGD',2.5000,5000,0,6,current_date,current_date+365,'approved'),
 ('00000000-0000-4000-8000-200000000002','demo-home-loan-sgd','Demo home loan discussion','home_loan',1,'SGD',2.8000,100000,250,240,current_date,current_date+365,'approved');
create table meetings.packs (
 document_id uuid primary key,meeting_id uuid not null references meetings.workflows(meeting_id),version int not null,revision int not null,
 source_hash text not null,content_hash text not null,plan jsonb not null,content jsonb not null,amounts jsonb not null,
 status text not null check(status in ('draft','approved','shared')),approved_at timestamptz,shared_at timestamptz,
 created_at timestamptz not null default clock_timestamp(),unique(meeting_id,version)
);
alter table meetings.catalogue_terms enable row level security;
alter table meetings.packs enable row level security;
revoke all on meetings.catalogue_terms,meetings.packs from public,anon,authenticated,demobank_meetings,demobank_meeting_ingress;

create function meetings.pack_inputs(p_meeting uuid,p_amounts jsonb) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
declare w meetings.workflows;a rm.appointments;item jsonb;catalogue jsonb;body jsonb;
begin
 w:=meetings.require_current(p_meeting);select * into a from rm.appointments where appointment_id=p_meeting;
 if p_amounts is null or jsonb_typeof(p_amounts)<>'array' or jsonb_array_length(p_amounts)>5 then raise exception 'provide at most five explicitly entered amount records' using errcode='22023';end if;
 for item in select value from jsonb_array_elements(p_amounts) loop
  if (select array_agg(key order by key) from jsonb_object_keys(item) key) is distinct from array['amount','currency','label']
   or jsonb_typeof(item->'label') is distinct from 'string' or length(btrim(item->>'label')) not between 1 and 80
   or jsonb_typeof(item->'currency') is distinct from 'string' or item->>'currency'!~'^[A-Z]{3}$'
   or coalesce(item->>'amount','')!~'^[0-9]{1,14}(\.[0-9]{1,2})?$' or (item->>'amount')::numeric>99999999999999.99
  then raise exception 'each stated amount needs a label, three-letter currency and nonnegative two-decimal amount' using errcode='22023';end if;
  perform conversation.require_redacted(item->>'label');
 end loop;
 select coalesce(jsonb_agg(to_jsonb(t)||jsonb_build_object('annual_rate',t.annual_rate::text,'minimum_amount',t.minimum_amount::text,'fee',t.fee::text) order by t.code),'[]') into catalogue from meetings.catalogue_terms t
 where t.status='approved' and rm.today() between t.effective_from and t.effective_to and t.topic=any(a.topics);
 body:=jsonb_build_object('meeting_id',p_meeting,'revision',w.revision,'purpose',a.purpose,'topics',a.topics,'catalogue',catalogue,'amounts',p_amounts);
 perform meetings.read_audit(a.customer_id,'meetings.catalogue_terms',p_meeting::text,'meeting.pack_inputs');
 return body||jsonb_build_object('source_hash',meetings.hash(body),'as_of',clock_timestamp());
end $$;

create function public.meeting_pack_inputs(p_meeting_id uuid,p_amounts jsonb) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
begin
 perform meetings.require_rm(p_meeting_id);
 return meetings.pack_inputs(p_meeting_id,p_amounts);
end $$;

create function public.meeting_save_pack(p_meeting_id uuid,p_revision int,p_source_hash text,p_amounts jsonb,p_plan jsonb,p_command_id uuid) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
declare w meetings.workflows;inputs jsonb;item jsonb;products jsonb;content jsonb;doc meetings.packs;next_version int;receipt jsonb;
 params jsonb:=jsonb_build_array(p_revision,p_source_hash,p_amounts,p_plan);chosen text[]:='{}';
begin
 w:=meetings.require_rm(p_meeting_id);
 receipt:=meetings.replay(p_command_id,p_meeting_id,'save_pack',params);if receipt is not null then return receipt;end if;
 inputs:=meetings.pack_inputs(p_meeting_id,p_amounts);
 if p_revision is distinct from w.revision or p_source_hash is distinct from inputs->>'source_hash' then raise exception 'agenda or catalogue terms changed during drafting; create a new pack' using errcode='P1001';end if;
 if p_plan is null or (select array_agg(key order by key) from jsonb_object_keys(p_plan) key) is distinct from array['agenda','product_ids','questions']
  or jsonb_typeof(p_plan->'agenda') is distinct from 'array' or jsonb_array_length(p_plan->'agenda') not between 1 and 6
  or jsonb_typeof(p_plan->'questions') is distinct from 'array' or jsonb_array_length(p_plan->'questions')>6
  or jsonb_typeof(p_plan->'product_ids') is distinct from 'array' or jsonb_array_length(p_plan->'product_ids')>5
 then raise exception 'pack plan requires supported agenda/question codes and catalogue references' using errcode='22023';end if;
 if (select count(*)<>count(distinct value) from jsonb_array_elements(p_plan->'agenda'))
  or (select count(*)<>count(distinct value) from jsonb_array_elements(p_plan->'questions'))
 then raise exception 'pack agenda and question codes must be distinct' using errcode='22023';end if;
 for item in select value from jsonb_array_elements(p_plan->'agenda') loop
  if jsonb_typeof(item)<>'string' or item#>>'{}' not in ('goals','status','documents','options','next_steps','questions') then raise exception 'unsupported pack agenda code' using errcode='22023';end if;
 end loop;
 for item in select value from jsonb_array_elements(p_plan->'questions') loop
  if jsonb_typeof(item)<>'string' or item#>>'{}' not in ('clarify_objective','confirm_documents','review_options','confirm_next_steps','ask_product_risks','confirm_timeframe') then raise exception 'unsupported client-safe question code' using errcode='22023';end if;
 end loop;
 for item in select value from jsonb_array_elements(p_plan->'product_ids') loop
  if jsonb_typeof(item)<>'string' or item#>>'{}'=any(chosen) or not exists(select 1 from jsonb_array_elements(inputs->'catalogue') x where x->>'item_id'=item#>>'{}')
  then raise exception 'the pack may reference only distinct current catalogue items for the agreed topics' using errcode='22023';end if;
  chosen:=array_append(chosen,item#>>'{}');
 end loop;
 select coalesce(jsonb_agg(x order by x->>'code'),'[]') into products from jsonb_array_elements(inputs->'catalogue') x where x->>'item_id'=any(chosen);
 content:=jsonb_build_object('kind','meeting_pack','title','Meeting discussion pack','purpose',inputs->'purpose','topics',inputs->'topics',
  'agenda',p_plan->'agenda','questions',p_plan->'questions','products',products,'stated_amounts',p_amounts,'as_of',inputs->'as_of');
 select coalesce(max(p.version),0)+1 into next_version from meetings.packs p where meeting_id=p_meeting_id;
 insert into meetings.packs(document_id,meeting_id,version,revision,source_hash,content_hash,plan,content,amounts,status)
 values(p_command_id,p_meeting_id,next_version,w.revision,p_source_hash,meetings.hash(content),p_plan,content,p_amounts,'draft') returning * into doc;
 return meetings.remember(p_command_id,p_meeting_id,'save_pack',params,to_jsonb(doc));
end $$;

create function meetings.require_pack(p_meeting uuid,p_document uuid,p_version int,p_hash text,p_revision int) returns meetings.packs
language plpgsql volatile security definer set search_path='' as $$
declare w meetings.workflows;doc meetings.packs;inputs jsonb;
begin
 w:=meetings.require_rm(p_meeting);
 select * into doc from meetings.packs where document_id=p_document and meeting_id=p_meeting for update;
 if not found or p_revision is distinct from w.revision or doc.revision<>w.revision or doc.version is distinct from p_version or doc.content_hash is distinct from p_hash
  or doc.version<>(select max(version) from meetings.packs where meeting_id=p_meeting)
 then raise exception 'approve or share the exact current meeting pack version' using errcode='P1001';end if;
 inputs:=meetings.pack_inputs(p_meeting,doc.amounts);
 if doc.source_hash<>inputs->>'source_hash' then raise exception 'pack agenda or approved catalogue terms changed; create a refreshed document' using errcode='P1001';end if;
 return doc;
end $$;

create function public.meeting_approve_pack(p_meeting_id uuid,p_document_id uuid,p_version int,p_content_hash text,p_revision int,p_command_id uuid) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
declare doc meetings.packs;receipt jsonb;params jsonb:=jsonb_build_array(p_document_id,p_version,p_content_hash,p_revision);
begin
 perform meetings.require_rm(p_meeting_id);
 receipt:=meetings.replay(p_command_id,p_meeting_id,'approve_pack',params);if receipt is not null then return receipt;end if;
 doc:=meetings.require_pack(p_meeting_id,p_document_id,p_version,p_content_hash,p_revision);
 if doc.status='draft' then update meetings.packs set status='approved',approved_at=clock_timestamp() where document_id=doc.document_id returning * into doc;end if;
 perform audit.record('document','document.meeting_pack_approved','success','meetings.packs',doc.document_id::text,
  jsonb_build_object('meeting_id',p_meeting_id,'version',doc.version),p_actor_type=>'staff',p_actor_id=>auth.uid()::text,
  p_customer_id=>(select customer_id from rm.appointments where appointment_id=p_meeting_id));
 return meetings.remember(p_command_id,p_meeting_id,'approve_pack',params,to_jsonb(doc));
end $$;

create function public.meeting_share_pack(p_meeting_id uuid,p_document_id uuid,p_version int,p_content_hash text,p_revision int,p_command_id uuid) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
declare doc meetings.packs;a rm.appointments;receipt jsonb;params jsonb:=jsonb_build_array(p_document_id,p_version,p_content_hash,p_revision);
begin
 perform meetings.require_rm(p_meeting_id);
 receipt:=meetings.replay(p_command_id,p_meeting_id,'share_pack',params);if receipt is not null then return receipt;end if;
 doc:=meetings.require_pack(p_meeting_id,p_document_id,p_version,p_content_hash,p_revision);
 if doc.status='draft' then raise exception 'approve the exact meeting pack before sharing it' using errcode='P1001';end if;
 if doc.status<>'shared' then
  select * into a from rm.appointments where appointment_id=p_meeting_id;
  receipt:=public.rm_send_client_update(doc.document_id,a.customer_id,null,'Your approved meeting discussion pack is available under My requests - Meetings.',null,least(doc.version,50),false);
  if receipt->>'denied'='true' then raise exception 'RM authorization changed before sharing the pack' using errcode='42501';end if;
  update meetings.packs set status='shared',shared_at=clock_timestamp() where document_id=doc.document_id returning * into doc;
 end if;
 return meetings.remember(p_command_id,p_meeting_id,'share_pack',params,to_jsonb(doc));
end $$;

create function public.meeting_current_pack(p_meeting_id uuid) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
declare w meetings.workflows;doc meetings.packs;own_client boolean;inputs jsonb;
begin
 w:=meetings.require_participant(p_meeting_id);
 own_client:=banking.my_customer_id()=(select customer_id from rm.appointments where appointment_id=p_meeting_id);
 select * into doc from meetings.packs where meeting_id=p_meeting_id and (not coalesce(own_client,false) or status='shared') order by version desc limit 1;
 if not found then return null;end if;
 perform meetings.read_audit((select customer_id from rm.appointments where appointment_id=p_meeting_id),'meetings.packs',doc.document_id::text,'meeting.pack_read');
 inputs:=meetings.pack_inputs(p_meeting_id,doc.amounts);
 return to_jsonb(doc)||jsonb_build_object('sources_changed',doc.source_hash<>inputs->>'source_hash');
end $$;

revoke all on function meetings.pack_inputs(uuid,jsonb),meetings.require_pack(uuid,uuid,int,text,int) from public,anon,authenticated,demobank_meetings,demobank_meeting_ingress;
revoke all on function public.meeting_pack_inputs(uuid,jsonb),public.meeting_save_pack(uuid,int,text,jsonb,jsonb,uuid),
 public.meeting_approve_pack(uuid,uuid,int,text,int,uuid),public.meeting_share_pack(uuid,uuid,int,text,int,uuid),public.meeting_current_pack(uuid) from public,anon;
grant execute on function public.meeting_pack_inputs(uuid,jsonb),public.meeting_save_pack(uuid,int,text,jsonb,jsonb,uuid),
 public.meeting_approve_pack(uuid,uuid,int,text,int,uuid),public.meeting_share_pack(uuid,uuid,int,text,int,uuid),public.meeting_current_pack(uuid) to authenticated;
