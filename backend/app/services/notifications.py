"""Customer inbox operations. SQL migrations 0023–0025 own template delivery, dedupe and atomic worker auditing.

The worker uses narrow private SQL functions under demobank_worker; it never uses a service-role key.
Customer reads keep both JWT/RLS and explicit customer scope. Mark-read is idempotent and audited in SQL.
"""
from uuid import UUID
from postgrest import SyncPostgrestClient
from pydantic import TypeAdapter
from app.core.data_api import execute
from app.schemas.contracts import RequestContext
from app.schemas.notifications import Inbox,Notification
from app.services.authorization import require_permission


class NotificationIntegrityError(ValueError):
    """Returned notifications do not match the requested record or customer scope."""


def checked(item: Notification,ctx: RequestContext) -> Notification:
    if item.customer_id!=ctx.principal.customer_id:raise NotificationIntegrityError('Notification is outside the authenticated customer scope')
    return item


async def recent(ctx: RequestContext,db: SyncPostgrestClient) -> Inbox:
    require_permission(ctx.principal,'notifications.list',{'customer_id':ctx.principal.customer_id},ctx)
    response=await execute(lambda:db.rpc('notification_list_customer',{'p_customer_id':ctx.principal.customer_id}).execute(),'notifications.list')
    items=TypeAdapter(list[Notification]).validate_python(response.data)
    if len(items)>50 or len({item.notification_id for item in items})!=len(items):raise NotificationIntegrityError('Inbox listing exceeded its bound or repeated a notification')
    return Inbox(customer_id=ctx.principal.customer_id,items=[checked(item,ctx) for item in items])


async def mark_read(notification_id: UUID,ctx: RequestContext,db: SyncPostgrestClient) -> Notification:
    require_permission(ctx.principal,'notifications.read',{'customer_id':ctx.principal.customer_id},ctx)
    response=await execute(lambda:db.rpc('notification_mark_read',{'p_notification_id':str(notification_id),
        'p_customer_id':ctx.principal.customer_id}).execute(),'notifications.read')
    item=checked(Notification.model_validate(response.data),ctx)
    if item.notification_id!=notification_id or item.read_at is None:raise NotificationIntegrityError('The recorded read status does not match this notification')
    return item
