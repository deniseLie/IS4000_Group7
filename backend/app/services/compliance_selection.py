"""Owned record selectors travel as IDs in the request/checkpoint, never identity or document text."""
from contextvars import ContextVar
from app.core.errors import ApiError
from app.schemas.chat import RecordSelection
selection: ContextVar[RecordSelection | None]=ContextVar('compliance_selection',default=None)


def validate(chosen,workspace):
    review=workspace.review
    if review is None or chosen.case_id!=review.case_id:raise ApiError(404,'record_not_available','The selected review is no longer available. Reload it.')
    if chosen.kind=='information_request':
        if not any(r.request_id==chosen.request_id for r in review.info_requests):raise ApiError(404,'record_not_available','The selected request is no longer available.')
    else:
        if workspace.current_documents.get(chosen.item_id)!=chosen.document_id or not any(
            r.check_id==chosen.check_id and r.document_id==chosen.document_id and r.item_id==chosen.item_id for r in workspace.checks):
            raise ApiError(404,'record_not_available','The selected upload or check changed. Reload before explaining it.')
