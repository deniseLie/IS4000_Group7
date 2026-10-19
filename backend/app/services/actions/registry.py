"""Executor contracts. SQL's private actions.executors registry owns transactional dispatch."""
from collections.abc import Callable
from app.schemas.actions import ActionType,CardBlockTarget,HandoffTarget
from app.schemas.contracts import ActionCommand
from app.services.actions import cards, handoffs

EXECUTORS: dict[ActionType,Callable[[ActionCommand],CardBlockTarget | HandoffTarget]] = {
    'card.temporary_block': cards.temporary_block,
    'handoff.create': handoffs.create,
}
