-- Fix for 0089: its closing "revoke all on all functions in schema rm" also removed two grants that existed before it.
-- * rm.is_my_advisor(uuid) is used by the customer RLS policies on rm.advisors, rm.working_hours and rm.meeting_options
--   (0003, 0062). Without EXECUTE, a client's own advisor reads (customer_read_assignment, customer_read_introduction,
--   meetings) fail with "permission denied for function is_my_advisor" (42501), shown as "Record access does not match
--   the signed-in customer". Restored exactly as in 0003.
-- * The constructor functions of the rm.advisor_range type (0034) had the default PUBLIC EXECUTE; restored.
-- The RM-only helpers added by 0089 stay revoked: only the security-definer RM functions call them.
revoke execute on function rm.is_my_advisor(uuid) from public, anon;
grant execute on function rm.is_my_advisor(uuid) to authenticated;
grant execute on function rm.advisor_range(uuid,uuid),rm.advisor_range(uuid,uuid,text) to public;
