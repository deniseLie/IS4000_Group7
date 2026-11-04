-- Reset Michelle's (1002) demo conversations and the requests they created. Keeps her accounts, cards, compliance
-- review and documents, the two seeded requests (statement copy, address change), meetings and the audit log.
begin;

-- Requests created from her chats or "Talk to a person" (the seeded ones have source 'seed' and are kept).
delete from support.handoff_notes where handoff_id in (select handoff_id from support.handoffs where customer_id=1002);
delete from support.tickets where customer_id=1002 and source in ('chat','handoff','form','rm');
delete from support.handoffs where customer_id=1002;

-- Her saved chats (messages, turns, cases and checkpoints go with them).
delete from compliance.support_context where action_case_id in (select case_id from conversation.cases where customer_id=1002);
delete from conversation.sessions where customer_id=1002;

-- Actions proposed in those chats lost their case when the chats were deleted; remove them so the Actions list stays valid
-- (same as 23_repair_michelle_actions.sql).
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
update rm.meeting_requests set command_id=null where command_id in (select command_id from orphan_commands);  -- since 0106
delete from support.handoff_notes where command_id in (select command_id from orphan_commands);
delete from actions.staff_commands where command_id in (select command_id from orphan_commands);
delete from events.outbox where command_id in (select command_id from orphan_commands);
-- receipts and workflow links are removed with their command (on delete cascade)
delete from actions.commands where command_id in (select command_id from orphan_commands);

-- Inbox notices about those requests and chats.
delete from notify.inbox where customer_id=1002 and split_part(template_key,'.',1) in ('ticket','handoff','rm','card');

select (select count(*) from support.tickets where customer_id=1002) as tickets_left,
       (select count(*) from conversation.sessions where customer_id=1002) as chats_left;
commit;
