-- Step 2 (destructive, review first): drop tables left over from before the repository migrations.
-- Run 01_inventory.sql first. Only tables it marks 'legacy' belong here. Nothing in app/, scripts/ or the
-- frontend reads these four; they predate migration 0001/0008:
--   ops.llm_traces, ops.test_results   old tracing/eval tables (0001 says "not touched"; replaced by ops.traces/ops.spans)
--   kb.documents, kb.support_contacts  the old knowledge library (replaced by kb.articles/kb.sections/kb.facts in 0008)
-- 'restrict' makes Postgres refuse if anything still depends on a table; nothing is cascaded.
-- To keep a copy first: Table editor > the table > Export to CSV.
-- If 01_inventory.sql lists other 'legacy' tables (for example old tables in public), add them below
-- only after checking with the group that nobody uses them.
begin;
set local lock_timeout='10s';
drop table if exists ops.llm_traces restrict;
drop table if exists ops.test_results restrict;
drop table if exists kb.documents restrict;
drop table if exists kb.support_contacts restrict;
commit;
