-- Repair after 17_reset_michelle_demo.sql: deleting Michelle's saved chats also deleted their cases, and every action
-- (command) proposed in those chats lost its case (actions.commands.case_id is set to null on delete). The Actions list
-- then fails validation (GET /commands 503 "action_request_failed ... ValidationError"). This removes only those orphaned
-- actions and their receipts; records they created (documents, consent, requests) are kept with the link cleared.
-- The audit log keeps the history. Safe to run more than once.
begin;

create temporary table orphan_commands on commit drop as
  select command_id from actions.commands where customer_id=1002 and case_id is null;

update compliance.documents set command_id=null where command_id in (select command_id from orphan_commands);
update rm.requests set command_id=null where command_id in (select command_id from orphan_commands);
update rm.agendas set last_command_id=null where last_command_id in (select command_id from orphan_commands);
update rm.shared_summaries set command_id=null where command_id in (select command_id from orphan_commands);
update consent.requests set command_id=null where command_id in (select command_id from orphan_commands);
update consent.grants set command_id=null where command_id in (select command_id from orphan_commands);
update consent.grants set revocation_command_id=null where revocation_command_id in (select command_id from orphan_commands);
update support.tickets set command_id=null where command_id in (select command_id from orphan_commands);
delete from support.handoff_notes where command_id in (select command_id from orphan_commands);
delete from actions.staff_commands where command_id in (select command_id from orphan_commands);
delete from events.outbox where command_id in (select command_id from orphan_commands);
-- receipts and workflow links are removed with their command (on delete cascade)
delete from actions.commands where command_id in (select command_id from orphan_commands);

select (select count(*) from orphan_commands) as removed_actions,
       (select count(*) from actions.commands where customer_id=1002) as actions_left;
commit;
