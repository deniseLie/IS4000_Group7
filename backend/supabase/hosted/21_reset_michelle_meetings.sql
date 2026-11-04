-- Reset Michelle's (1002) meetings to the demo starting point: removes every appointment she has (confirmed, held,
-- cancelled or expired, including test bookings) and her booking notices, then books the one seeded meeting again:
-- next Wednesday 14:30 (Singapore time), video, with Sophie, purpose "Periodic review documents and fixed deposit maturity",
-- and (after 0095) the undocumented phone check-in on the previous week's Friday at 15:00 used by Today → Meetings to document.
-- After 0107 it ends with rm.reset_meeting_demo for Michelle: her meeting requests go too, and the 0106 "Home loan options"
-- meeting is booked again on a later free Thursday with its meeting.scheduled event (deleting her appointments alone would
-- leave that request scheduled with no meeting). Needs 0107 (batch 33).
-- Keeps her records, requests, chats and the audit log (the deletions are themselves audited). Safe to run more than once.
begin;

delete from notify.inbox where customer_id=1002 and template_key in ('booking.held','booking.confirmed','booking.cancelled');
delete from rm.appointments where customer_id=1002;

insert into rm.appointments(customer_id,advisor_id,starts_at,ends_at,format,status,joining_details,purpose)
select 1002,r.advisor_id,s.at,s.at+interval '30 minutes','video','confirmed',
  (select o.joining_details from rm.meeting_options o where o.advisor_id=r.advisor_id and o.format='video'),
  'Periodic review documents and fixed deposit maturity'
from rm.assignments r
cross join lateral (select (clock_timestamp() at time zone 'Asia/Singapore')::date today) t
cross join lateral (select ((t.today+((3-extract(isodow from t.today)::int+6)%7)+1)+time '14:30') at time zone 'Asia/Singapore' at) s
where r.customer_id=1002 and r.ended_on is null;

insert into rm.appointments(customer_id,advisor_id,starts_at,ends_at,format,status,joining_details,purpose)
select 1002,r.advisor_id,s.at,s.at+interval '30 minutes','phone','confirmed',
  (select o.joining_details from rm.meeting_options o where o.advisor_id=r.advisor_id and o.format='phone'),
  'Check-in on the sale agreement and fixed deposit maturity'
from rm.assignments r
cross join lateral (select (clock_timestamp() at time zone 'Asia/Singapore')::date today) t
cross join lateral (select ((t.today-(extract(isodow from t.today)::int+2))+time '15:00') at time zone 'Asia/Singapore' at) s
where r.customer_id=1002 and r.ended_on is null;

select rm.reset_meeting_demo(array[1002]::bigint[]) as meeting_demo;

select to_char(starts_at at time zone 'Asia/Singapore','Dy DD Mon YYYY, HH12:MI am') as michelle_meeting,format,status,purpose
from rm.appointments where customer_id=1002 order by starts_at;
commit;
