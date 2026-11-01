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

-- Inbox notices about those requests and chats.
delete from notify.inbox where customer_id=1002 and split_part(template_key,'.',1) in ('ticket','handoff','rm','card');

select (select count(*) from support.tickets where customer_id=1002) as tickets_left,
       (select count(*) from conversation.sessions where customer_id=1002) as chats_left;
commit;
