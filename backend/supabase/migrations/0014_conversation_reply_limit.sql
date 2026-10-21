-- Five 4000-character sections plus four blank-line separators fit in a saved reply.
create or replace function public.conversation_finish_turn(p_session_id uuid,p_turn_id uuid,p_run_id uuid,p_response jsonb,p_guest_token text)
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
    if jsonb_typeof(p_response->'answer') is distinct from 'string' or length(p_response->>'answer') not between 1 and 20008 then
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
