-- Client chat insights without the care pause (8 Nov 2026). Synthetic data only.
-- Since 0108 a care signal in her chat (job loss, debt, bereavement, serious illness, pressure about money) paused chat
-- insights for 60 days: public.customer_pause_suggestions opened a row in insights.opportunity_pauses, and while it was
-- open nothing new was recorded, her inferred insights were hidden from her RM and Data sharing showed a Resume line.
-- The pause is retired: the app no longer calls it after a reply or refuses a share offer on care, and her page has no
-- Resume line. This ends every open pause (audited per pause, system actor) so nothing stays hidden without a way back,
-- and drops the setter so no pause can be opened again. The table, insights.paused() and the paused checks in the
-- released readers stay unchanged (always false with no open pause); insight.resume keeps undoing a mute. Her care flag
-- (guardrail.CARE, AnswerEvent.care: only Talk to a person under her answer) is unchanged and no longer touches insights.

do $$
declare p insights.opportunity_pauses;
begin
  for p in update insights.opportunity_pauses set ended_at=clock_timestamp()
      where ended_at is null and until>clock_timestamp() returning * loop
    perform audit.record('data','data.suggestions_resumed','success','insights.opportunity_pauses',p.pause_id::text,
      jsonb_build_object('reason','care_pause_retired'),p_customer_id=>p.customer_id,p_actor_type=>'system',p_actor_id=>'migration_0145');
  end loop;
end $$;

drop function if exists public.customer_pause_suggestions(uuid);
