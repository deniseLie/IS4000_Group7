-- Include the active workflow in the owned conversation history.
alter function public.conversation_read_customer(uuid) set schema conversation;
alter function conversation.conversation_read_customer(uuid) rename to read_customer_history;
revoke all on function conversation.read_customer_history(uuid) from public,anon;
grant execute on function conversation.read_customer_history(uuid) to authenticated;
create function public.conversation_read_customer(p_session_id uuid)
returns jsonb language plpgsql volatile security invoker set search_path=''
as $$
declare result jsonb; cid bigint:=banking.my_customer_id();
begin
  result:=conversation.read_customer_history(p_session_id);
  return result||jsonb_build_object('workflow',conversation.workflow_status((result#>>'{session,active_turn_id}')::uuid,cid));
end $$;
revoke all on function public.conversation_read_customer(uuid) from public,anon;
grant execute on function public.conversation_read_customer(uuid) to authenticated;
