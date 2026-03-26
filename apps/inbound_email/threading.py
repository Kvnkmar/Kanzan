"""
Email threading service.

Handles matching inbound emails to existing tickets via RFC 2822
headers, and building outbound threading headers for reply chains.

Thread matching priority (most reliable first):
  1. In-Reply-To header → lookup by message_id
  2. References header  → lookup each ref_id (most recent first)
  3. Subject [#N]       → regex extraction (last resort, least reliable)

All lookups are explicitly tenant-scoped.
"""

import logging
import re

from apps.inbound_email.models import InboundEmail

logger = logging.getLogger(__name__)

# Pattern to match ticket reference in subject: [#123] or [Ticket #123]
TICKET_REF_PATTERN = re.compile(r"\[(?:Ticket\s*)?#(\d+)\]", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Inbound: match email to existing ticket
# ---------------------------------------------------------------------------


def find_existing_ticket(tenant, inbound_email):
    """
    Try to find the existing ticket this email is replying to.

    Uses three strategies in order of reliability. All queries are
    explicitly filtered by tenant to prevent cross-tenant matching.

    Args:
        tenant: The resolved Tenant instance.
        inbound_email: The InboundEmail being processed.

    Returns:
        A Ticket instance, or None if no match found.
    """
    # Priority 1: In-Reply-To header (most reliable)
    ticket = _match_by_in_reply_to(tenant, inbound_email)
    if ticket:
        return ticket

    # Priority 2: References header chain
    ticket = _match_by_references(tenant, inbound_email)
    if ticket:
        return ticket

    # Priority 3: Subject line [#N] (last resort — least reliable because
    # customers can forward old emails or edit subjects)
    ticket = _match_by_subject(tenant, inbound_email)
    if ticket:
        return ticket

    return None


def _match_by_in_reply_to(tenant, inbound_email):
    """Match via In-Reply-To header against stored message_ids."""
    if not inbound_email.in_reply_to:
        return None

    prev = (
        InboundEmail.objects.filter(
            tenant=tenant,
            message_id=inbound_email.in_reply_to,
            ticket__isnull=False,
        )
        .select_related("ticket")
        .first()
    )
    if prev:
        logger.info(
            "Matched email %s to ticket #%d via In-Reply-To",
            inbound_email.pk,
            prev.ticket.number,
        )
        return prev.ticket
    return None


def _match_by_references(tenant, inbound_email):
    """Match via References header (iterate from most recent to oldest)."""
    if not inbound_email.references:
        return None

    ref_ids = inbound_email.references.split()
    for ref_id in reversed(ref_ids):
        ref_id = ref_id.strip()
        if not ref_id:
            continue
        prev = (
            InboundEmail.objects.filter(
                tenant=tenant,
                message_id=ref_id,
                ticket__isnull=False,
            )
            .select_related("ticket")
            .first()
        )
        if prev:
            logger.info(
                "Matched email %s to ticket #%d via References",
                inbound_email.pk,
                prev.ticket.number,
            )
            return prev.ticket
    return None


def _match_by_subject(tenant, inbound_email):
    """
    Match via [#N] ticket reference in subject line.

    This is the least reliable strategy because:
    - Customers can forward old emails to report new issues
    - Subject lines can be edited by email clients
    - A typo in the ticket number routes to the wrong ticket

    The query is explicitly tenant-scoped to prevent cross-tenant matches.
    """
    ticket_number = extract_ticket_number(inbound_email.subject)
    if not ticket_number:
        return None

    from apps.tickets.models import Ticket

    ticket = Ticket.unscoped.filter(
        tenant=tenant, number=ticket_number,
    ).first()
    if ticket:
        logger.info(
            "Matched email %s to ticket #%d via subject line (least reliable)",
            inbound_email.pk,
            ticket_number,
        )
        return ticket
    return None


def extract_ticket_number(subject):
    """
    Extract a ticket number from the email subject line.

    Looks for patterns like [#42] or [Ticket #42].
    Returns the ticket number (int) or None.
    """
    if not subject:
        return None
    match = TICKET_REF_PATTERN.search(subject)
    if match:
        return int(match.group(1))
    return None


# ---------------------------------------------------------------------------
# Outbound: build threading headers for reply chains
# ---------------------------------------------------------------------------


def build_thread_headers(tenant, ticket, new_message_id):
    """
    Build In-Reply-To and References headers for an outbound email.

    Queries the ticket's email history to find the most recent
    message_id, then constructs a proper RFC 2822 reply chain.

    Args:
        tenant: The Tenant instance.
        ticket: The Ticket instance being replied to.
        new_message_id: The raw message_id for this outbound email
                        (without angle brackets).

    Returns:
        dict of headers: {"Message-ID": ..., "In-Reply-To": ..., "References": ...}
        Only includes In-Reply-To and References if there's a thread history.
    """
    headers = {"Message-ID": f"<{new_message_id}>"}

    # Find the most recent message_ids linked to this ticket
    recent_ids = list(
        InboundEmail.objects.filter(
            tenant=tenant,
            ticket=ticket,
            message_id__gt="",
        )
        .order_by("-created_at")
        .values_list("message_id", flat=True)[:10]
    )

    if not recent_ids:
        return headers

    # In-Reply-To: the most recent message in the thread
    headers["In-Reply-To"] = f"<{recent_ids[0]}>"

    # References: the full chain (oldest to newest), plus our new message_id
    chain = list(reversed(recent_ids))
    headers["References"] = " ".join(f"<{rid}>" for rid in chain)

    return headers
