"""Action type -> executor. Adding an action means one executor function and one row here; run_action() does not change."""
from app.services.actions import cards, handoffs

EXECUTORS = {
    'card.temporary_block': cards.temporary_block,
    'handoff.create': handoffs.create,
}
