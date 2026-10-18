-- Generic row-change audit (system-architecture.md §6.4 "data change", F3): one AFTER trigger per audited business table
-- writes row.insert / row.update / row.delete through audit.record() in the same transaction, so a change from any
-- path (Action Service, staff script, SQL editor) cannot commit without its audit row, and a failed audit write
-- rolls the change back.
-- details: changed column names always; old/new values only for the enum-like columns listed per table below.
-- Never listed: free text, names, amounts, dates, notes, file names, message text.
-- Not audited: conversation.sessions/messages/checkpoints (chat.* events cover them, and they hold text),
-- events.outbox (worker bookkeeping), product catalogues, notify.templates and rm.working_hours (composite keys, admin data).
-- TRUNCATE is not captured; app roles cannot truncate these tables. Rows seeded by 0004 are not back-filled.
-- Every audited write takes the audit advisory lock until commit, so audited writes are serialised (fine for the demo).

-- tg_argv[0] = primary key column; tg_argv[1..] = columns whose values may be logged.
-- customer_id is the row's own; tables without one fall back to the caller's (audit.record()).
-- case_id is copied only where it points at conversation.cases (actions, support, conversation), not compliance case ids.
create or replace function audit.row_change()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  o jsonb := case when tg_op <> 'INSERT' then to_jsonb(old) end;
  n jsonb := case when tg_op <> 'DELETE' then to_jsonb(new) end;
  r jsonb := coalesce(n, o);
  shown text[] := coalesce(tg_argv[1:tg_nargs - 1], '{}');
  changed text[];
  d jsonb;
begin
  if tg_op = 'UPDATE' then
    select coalesce(array_agg(k order by k), '{}') into changed from jsonb_object_keys(n) k where n -> k is distinct from o -> k;
    if cardinality(changed) = 0 then return null; end if;
    d := jsonb_build_object('changed', to_jsonb(changed),
      'old', (select coalesce(jsonb_object_agg(k, o -> k), '{}') from unnest(changed) k where k = any (shown)),
      'new', (select coalesce(jsonb_object_agg(k, n -> k), '{}') from unnest(changed) k where k = any (shown)));
  else
    d := jsonb_build_object(case when tg_op = 'INSERT' then 'new' else 'old' end,
      (select coalesce(jsonb_object_agg(k, r -> k), '{}') from unnest(shown) k));
  end if;
  perform audit.record('row', 'row.' || lower(tg_op), 'success', tg_table_schema || '.' || tg_table_name, r ->> tg_argv[0], d,
    case when tg_table_schema in ('actions', 'support', 'conversation') then (r ->> 'case_id')::uuid end,
    p_customer_id => (r ->> 'customer_id')::bigint);
  return null;
end $$;
revoke all on function audit.row_change() from public, anon, authenticated;

-- table, primary key, loggable columns (comma separated, may be empty)
do $$
declare t text[];
begin
  foreach t slice 1 in array array[
    ['banking.customer_profiles',  'customer_id',     ''],
    ['banking.bank_accounts',      'account_id',      'account_type,status'],
    ['banking.credit_cards',       'card_id',         'network,status'],
    ['banking.loans',              'loan_id',         'loan_type,status'],
    ['banking.transactions',       'transaction_id',  'status,is_overseas'],
    ['compliance.cases',           'case_id',         'case_type,status'],
    ['compliance.case_internal',   'case_id',         ''],
    ['compliance.requested_items', 'item_id',         'submission_status,client_mark'],
    ['compliance.status_history',  'id',              'status'],
    ['compliance.info_requests',   'request_id',      'status'],
    ['compliance.documents',       'document_id',     'mime_type,precheck_status'],
    ['rm.advisors',                'advisor_id',      'is_active'],
    ['rm.assignments',             'assignment_id',   ''],
    ['rm.advisor_blocks',          'block_id',        ''],
    ['rm.agendas',                 'agenda_id',       'status'],
    ['rm.appointments',            'appointment_id',  'format,status'],
    ['rm.requests',                'request_id',      'urgency,status'],
    ['rm.shared_summaries',        'summary_id',      ''],
    ['consent.requests',           'request_id',      'status'],
    ['consent.grants',             'grant_id',        ''],
    ['conversation.cases',         'case_id',         'priority,status'],
    ['actions.commands',           'command_id',      'type,state'],
    ['actions.receipts',           'receipt_id',      'outcome'],
    ['support.handoffs',           'handoff_id',      'priority,status'],
    ['notify.preferences',         'customer_id',     ''],
    ['notify.inbox',               'notification_id', 'is_service']]
  loop
    execute format('create trigger audit_row after insert or update or delete on %s for each row execute function audit.row_change(%s)',
      t[1], (select string_agg(quote_literal(c), ', ') from unnest(t[2] || string_to_array(nullif(t[3], ''), ',')) c));
  end loop;
end $$;
