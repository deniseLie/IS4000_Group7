-- Step 1 (read-only, safe): run in the Supabase SQL editor and send the result to the group.
-- Lists every table in the app schemas with its approximate row count and whether a repository migration owns it.
-- 'legacy' rows are the only candidates for 02_drop_legacy.sql. Nothing is changed by this script.
with expected(name) as (values
    ('actions.commands'),
    ('actions.executors'),
    ('actions.receipts'),
    ('actions.staff_commands'),
    ('actions.workflow_commands'),
    ('audit.events'),
    ('banking.bank_accounts'),
    ('banking.credit_card_products'),
    ('banking.credit_cards'),
    ('banking.customer_profiles'),
    ('banking.loan_products'),
    ('banking.loans'),
    ('banking.transactions'),
    ('compliance.case_internal'),
    ('compliance.cases'),
    ('compliance.check_policy'),
    ('compliance.documents'),
    ('compliance.info_requests'),
    ('compliance.precheck_keys'),
    ('compliance.precheck_rules'),
    ('compliance.prechecks'),
    ('compliance.requested_items'),
    ('compliance.status_history'),
    ('compliance.support_context'),
    ('consent.grants'),
    ('consent.requests'),
    ('conversation.agent_workflows'),
    ('conversation.cases'),
    ('conversation.checkpoint_writes'),
    ('conversation.checkpoints'),
    ('conversation.guest_access'),
    ('conversation.messages'),
    ('conversation.sessions'),
    ('conversation.turns'),
    ('events.outbox'),
    ('kb.articles'),
    ('kb.facts'),
    ('kb.sections'),
    ('notify.inbox'),
    ('notify.preferences'),
    ('notify.templates'),
    ('ops.spans'),
    ('ops.traces'),
    ('rm.advisor_blocks'),
    ('rm.advisors'),
    ('rm.agendas'),
    ('rm.appointments'),
    ('rm.assignments'),
    ('rm.meeting_options'),
    ('rm.requests'),
    ('rm.shared_summaries'),
    ('rm.transition_notices'),
    ('rm.working_hours'),
    ('support.handoff_notes'),
    ('support.handoffs'),
    ('support.request_categories'),
    ('support.ticket_events'),
    ('support.tickets')
), actual as (
  select n.nspname||'.'||c.relname as name, c.reltuples::bigint as approx_rows
  from pg_class c join pg_namespace n on n.oid=c.relnamespace
  where c.relkind in ('r','p') and n.nspname in ('public','ops','kb','banking','compliance','rm','consent','conversation','actions','support','notify','events','audit')
)
select a.name, a.approx_rows,
  case when e.name is not null then 'in use (repository migration)' else 'legacy (not created by any migration)' end as status
from actual a left join expected e using(name)
order by status desc, a.name;

-- Migration history check: every repository migration up to 0072 should be listed once.
select version, name from supabase_migrations.schema_migrations order by version;
