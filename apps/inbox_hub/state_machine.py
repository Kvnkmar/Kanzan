"""HubEmail lifecycle state machine.

Single source of truth for which ``HubEmail.State`` transitions are legal.
Service mutations that change state route through :func:`assert_transition`
so the workspace can never reach an inconsistent lifecycle (e.g. re-opening a
converted email, or assigning a dismissed one).

Terminal states (CONVERTED_TO_TICKET, DISMISSED) have no outgoing edges.
"""

from apps.inbox_hub.models import HubEmail

State = HubEmail.State

ALLOWED_TRANSITIONS = {
    State.NEW: {
        State.ASSIGNED, State.ESCALATED, State.DISMISSED, State.CONVERTED_TO_TICKET,
    },
    State.ASSIGNED: {
        State.NEW, State.IN_PROGRESS, State.PENDING_AGENT, State.AWAITING_CUSTOMER,
        State.ESCALATED, State.RESOLVED, State.DISMISSED, State.CONVERTED_TO_TICKET,
    },
    State.IN_PROGRESS: {
        State.PENDING_AGENT, State.AWAITING_CUSTOMER, State.ESCALATED,
        State.RESOLVED, State.DISMISSED, State.CONVERTED_TO_TICKET,
    },
    State.PENDING_AGENT: {
        State.IN_PROGRESS, State.AWAITING_CUSTOMER, State.ESCALATED,
        State.RESOLVED, State.DISMISSED, State.CONVERTED_TO_TICKET,
    },
    State.AWAITING_CUSTOMER: {
        State.IN_PROGRESS, State.PENDING_AGENT, State.ESCALATED,
        State.RESOLVED, State.DISMISSED, State.CONVERTED_TO_TICKET,
    },
    State.ESCALATED: {
        State.ASSIGNED, State.IN_PROGRESS, State.RESOLVED,
        State.DISMISSED, State.CONVERTED_TO_TICKET,
    },
    State.RESOLVED: {
        State.IN_PROGRESS, State.CONVERTED_TO_TICKET, State.DISMISSED,
    },
    State.CONVERTED_TO_TICKET: set(),
    State.DISMISSED: set(),
}


def can_transition(old_state, new_state):
    """Return True if ``old_state -> new_state`` is a legal transition."""
    if old_state == new_state:
        return False
    return new_state in ALLOWED_TRANSITIONS.get(old_state, set())


def assert_transition(old_state, new_state):
    """Raise ``ValueError`` when the transition is illegal."""
    if not can_transition(old_state, new_state):
        raise ValueError(
            f"Illegal HubEmail transition: {old_state} -> {new_state}"
        )
