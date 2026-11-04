-- Reset the meeting demo (0106) to today. Seeds fix their dates on the day they are applied, so run this before each demo
-- and whenever the meeting side needs a fresh upcoming meeting (tell them first: it removes the meeting their brief
-- belongs to). Needs 0107 (batch 33).
--
-- rm.reset_meeting_demo removes the meeting requests of Michelle (1002) and Mei Ling (1007), the meetings and held times
-- those requests created, their queued meetings.events and their meeting notices, then seeds again:
-- * Michelle's grant to Sophie for transaction insights, chat topics and meeting recording (restored if it was revoked
--   or has expired);
-- * her accepted request "Home loan options": a confirmed video meeting at 15:00 on the first free Thursday of a week in
--   which she has no other meeting, with its meeting.scheduled event;
-- * Mei Ling's open fixed deposit request for the first free Monday and Tuesday at 16:30.
-- Since 0108 (batch 35) it also resets their talking points: Michelle's inferred home loan topic already past its 24-hour
-- hold, with her quote, and Mei Ling's investments topic she shared herself (no grant), with its Today notice.
-- Other meetings, records, chats and the audit log stay (the deletions are themselves audited). Safe to run more than once.
begin;

select rm.reset_meeting_demo() as meeting_demo;

select r.customer_id,r.status,array(select to_char(t at time zone 'Asia/Singapore','Dy DD Mon, HH24:MI') from unnest(r.preferred_times) t) as preferred,
  to_char(a.starts_at at time zone 'Asia/Singapore','Dy DD Mon YYYY, HH24:MI') as meeting,a.status as meeting_status,
  (select count(*) from meetings.events e where e.meeting_id=a.appointment_id) as events
from rm.meeting_requests r left join rm.appointments a on a.appointment_id=r.appointment_id
where r.customer_id in (1002,1007) order by r.customer_id;
commit;
