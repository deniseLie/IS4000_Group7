-- Reject and confirm both recheck a binding after obtaining the command row lock.
-- Cancellation's terminal turn trigger may reject undecided proposals without a review checkpoint.
create or replace function actions.guard_workflow_confirmation()
returns trigger language plpgsql security definer set search_path=''
as $$
begin
  if new.state='rejected' and exists(select 1 from actions.workflow_commands b join conversation.turns t using(turn_id)
      where b.command_id=new.command_id and t.state='failed') then return new; end if;
  perform actions.require_workflow_decision(new.command_id);
  return new;
end $$;
drop trigger guard_workflow_confirmation on actions.commands;
create trigger guard_workflow_confirmation before update of state on actions.commands for each row
  when(old.state='proposed' and new.state in ('confirmed','rejected')) execute function actions.guard_workflow_confirmation();
