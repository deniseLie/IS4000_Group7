-- RM Zoom check (8 Nov 2026): the codes-only record read behind the read-only `zoom_meeting_check` tool.
--   One appointment of the signed-in relationship manager, in her current book: its time, format and status, and whether
--   it has a Zoom link (meetings.zoom_links, 0123/0138) that still matches the booked time. The Zoom meeting number goes
--   back to the backend tool only, which reads that meeting from Zoom and never shows the number to a model or a person.
--   The join URL, Zoom UUID, host and account ids are not returned. An appointment that is not hers or whose client is not
--   in her book is refused and audited (rm.deny), with the same reply whether or not it exists. Read only: no table changes.
create function public.rm_zoom_check(p_appointment_id uuid) returns jsonb language plpgsql volatile security definer set search_path='' as $$
declare advisor uuid:=rm.current_advisor(); a rm.appointments; l meetings.zoom_links;
begin
  select * into a from rm.appointments where appointment_id=p_appointment_id;
  if not found or a.advisor_id is distinct from advisor or not rm.in_book(advisor,a.customer_id) then
    return rm.deny(a.customer_id,'rm.zoom_check');
  end if;
  select * into l from meetings.zoom_links where meeting_id=p_appointment_id and cancelled_at is null;
  return rm.result(a.customer_id,'rm.zoom_check','meetings.zoom_links',jsonb_build_object(
    'meeting',rm.sgt(a.starts_at),'starts_at',a.starts_at,'ends_at',a.ends_at,'format',a.format,'status',a.status,
    'link',case when l.meeting_id is null then 'none' when l.starts_at is distinct from a.starts_at then 'stale' else 'set_up' end,
    'provider_id',l.provider_id));
end $$;
revoke all on function public.rm_zoom_check(uuid) from public,anon;
grant execute on function public.rm_zoom_check(uuid) to authenticated;
