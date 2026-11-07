-- Isolated candidate for the new consent-v2 seam. Not a hosted or main migration.
-- Exact-version approval and its original effects run before closing meeting-bound grants.
create or replace function public.meeting_approve_minutes(p_meeting_id uuid,p_draft_id uuid,p_version int,p_content_hash text,p_revision int,p_consent_epoch int,p_command_id uuid)
returns jsonb language plpgsql volatile security definer set search_path='' as $$
declare result jsonb;
begin
 result:=public.meeting_approve_minutes_before_retention(p_meeting_id,p_draft_id,p_version,p_content_hash,p_revision,p_consent_epoch,p_command_id);
 update meetings.sources set purge_after=coalesce(purge_after,clock_timestamp()+interval '30 days')
 where source_id=(select source_id from meetings.drafts where draft_id=p_draft_id) and kind='transcript';
 perform consent.close_meeting_grants(p_meeting_id,'meeting_closed');
 return result;
end $$;
