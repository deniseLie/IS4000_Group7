-- Persona 2 demo: Sophie's book is reduced to two clients, Michelle Ng (1002) and Tan Mei Ling (1007, inherited from
-- Rachel Goh), so whole-book assistant questions (triage, follow-ups, schedule) read and send less. Grace Koh (1004),
-- Hafiz Rahman (1005) and, where present, David Lee (1003) move to Priya Nair from today: Sophie's access to them ends
-- through the normal assignment rules (rm.in_book), so asking about them is refused and audited like any client outside
-- her book. Their meetings with Sophie are cancelled and her open tasks for them are closed. Notes, tickets and the audit
-- trail are kept. Synthetic data only.
do $$
declare sophie constant uuid:='00000000-0000-4000-8000-100200000002'; priya constant uuid:='00000000-0000-4000-8000-100100000002';
  today date:=(clock_timestamp() at time zone 'Asia/Singapore')::date; moved bigint[];
begin
  if not exists(select 1 from rm.advisors where advisor_id=priya) then return; end if;
  select coalesce(array_agg(customer_id),'{}') into moved from rm.assignments
    where advisor_id=sophie and ended_on is null and customer_id in (1003,1004,1005);
  update rm.assignments set ended_on=greatest(started_on,today-1)
    where advisor_id=sophie and ended_on is null and customer_id=any(moved);
  insert into rm.assignments(customer_id,advisor_id,started_on,ended_on,transition_reason_code)
    select c,priya,today,null,'team_restructure' from unnest(moved) c;
  update rm.appointments set status='cancelled'
    where advisor_id=sophie and customer_id=any(moved) and status in ('confirmed','held') and starts_at>clock_timestamp();
  update rm.tasks set status='cancelled' where advisor_id=sophie and customer_id=any(moved) and status='open';
end $$;
