"""Fenced calendar-to-Zoom effects. Create once, reconcile interrupted outcomes, and never send model-generated fields."""
from pathlib import Path
import httpx,psycopg
from pydantic import ValidationError
from app.schemas.meeting_lifecycle import Claim
from app.schemas.zoom_scheduling import ProviderMeeting
from app.services.meetings.database import WorkerDatabase
from app.services.meetings.zoom import CreateRefused,Owner,Zoom,ZoomFailure,refreshed_token


def verified_owner(provider: Zoom,context: ProviderMeeting) -> Owner:
    owner=provider.owner()
    if context.account_id is not None and context.account_id!=owner.account_id or context.host_id is not None and context.host_id!=owner.id:
        raise PermissionError('The configured own-user Zoom grant does not own the registered meeting; no provider change was made')
    return owner


def schedule(database: WorkerDatabase,claim: Claim,directory: Path) -> None:
    try:
        database.apply_lifecycle(claim);context=database.zoom_schedule_context(claim)
        if context is None or context.linked_current:
            database.finish(claim)
            return
        provider=Zoom(refreshed_token(directory))
        try:
            owner=verified_owner(provider,context);agenda=context.purpose+'\nTopics: '+', '.join(context.topics)
            if context.provider_id is not None:
                remote=provider.move_schedule(context.provider_id,claim.meeting_id,context.starts_at,context.ends_at,agenda,owner)
            elif database.reserve_zoom_job(claim):remote=provider.create_schedule(claim.meeting_id,context.starts_at,context.ends_at,agenda,owner)
            else:
                remote=provider.reconcile(claim.meeting_id,owner)
                if remote is None:raise ZoomFailure('No matching Zoom meeting is visible for the reserved creation. Review the provider outcome before creating another meeting; no duplicate was created.')
            database.commit_scheduled_zoom(claim,remote,owner)
        finally:provider.close()
        database.finish(claim)
    except CreateRefused:
        database.release_refused_zoom(claim);database.fail(claim,'zoom_provider_refused')
        raise
    except ZoomFailure:
        database.fail(claim,'zoom_scheduling_failed')
        raise
    except httpx.HTTPError as error:
        database.fail(claim,'zoom_schedule_network')
        raise ZoomFailure('Zoom scheduling network request failed: '+type(error).__name__+'. The reserved creation will be reconciled before any retry.') from error
    except PermissionError:
        database.reject(claim,'zoom_owner_mismatch')
        raise
    except (ValidationError,ValueError):
        database.reject(claim,'zoom_schedule_invalid')
        raise
    except psycopg.Error as error:
        if error.sqlstate in ('42501','P1001','23505','22023'):database.reject(claim,'zoom_schedule_conflict')
        raise


def cancel(database: WorkerDatabase,claim: Claim,directory: Path) -> None:
    try:
        database.apply_lifecycle(claim);context=database.zoom_cancel_context(claim)
        if context is None:
            database.finish(claim)
            return
        if context.provider_id is None:raise ValueError('The registered cancellation has no provider meeting ID')
        provider=Zoom(refreshed_token(directory))
        try:
            owner=verified_owner(provider,context);outcome=provider.cancel_bound(context.provider_id,claim.meeting_id,owner)
            database.commit_zoom_cancellation(claim,context.provider_id,outcome)
        finally:provider.close()
        database.finish(claim)
    except ZoomFailure:
        database.fail(claim,'zoom_cancellation_failed')
        raise
    except httpx.HTTPError as error:
        database.fail(claim,'zoom_cancel_network')
        raise ZoomFailure('Zoom cancellation network request failed: '+type(error).__name__+'. Retry the registered cancellation after the connection recovers.') from error
    except PermissionError:
        database.reject(claim,'zoom_owner_mismatch')
        raise
    except (ValidationError,ValueError):
        database.reject(claim,'zoom_cancel_invalid')
        raise
    except psycopg.Error as error:
        if error.sqlstate in ('42501','P1001','23505','22023'):database.reject(claim,'zoom_cancel_conflict')
        raise
