"""
Unified sidebar badge-count endpoint.

Frontend usage:
- Call GET /api/v1/nav/badge-counts/ on sidebar mount
- Poll every 60 seconds to refresh counts
- If a key value is 0, hide the badge entirely
- If a key value is > 99, display "99+"
- Badge style should match existing Tickets badge (same CSS class)

Nav item → key mapping:
- Calendar  → response.calendar
- Messages  → response.messages
- Emails    → response.emails
- Tickets   → response.tickets (replace existing hardcoded count if any)
"""

import logging

from django.db.models import Q, Count
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import IsTenantMember, _get_membership

logger = logging.getLogger(__name__)

MAX_BADGE = 99


def _cap(n):
    """Clamp count to MAX_BADGE."""
    return min(n, MAX_BADGE)


@extend_schema(
    summary="Sidebar badge counts",
    description="Returns live unread/pending counts for sidebar nav badges. All counts capped at 99.",
    tags=["Navigation"],
)
class BadgeCountView(APIView):
    """
    GET /api/v1/nav/badge-counts/

    Returns live unread/pending counts for the sidebar nav badges.
    All counts are integers (never null), capped at 99.
    """

    permission_classes = [IsAuthenticated, IsTenantMember]

    def get(self, request):
        tenant = request.tenant
        user = request.user

        membership = _get_membership(request, tenant)
        # Use effective_role so a temporary role grant shifts badge scoping
        # consistently with the rest of the codebase (and IsHubEmailAccessible).
        is_agent = membership and membership.effective_role.hierarchy_level > 20

        tickets = self._ticket_count(tenant, user, is_agent)
        calendar = self._calendar_count(tenant, user)
        messages = self._message_count(tenant, user)
        emails = self._email_count(tenant, user)
        reminders = self._reminder_count(tenant, user, is_agent)
        knowledge = self._knowledge_count(tenant, user)
        inbox_hub = self._inbox_hub_count(tenant, user, membership)

        return Response(
            {
                "tickets": _cap(tickets),
                "calendar": _cap(calendar),
                "messages": _cap(messages),
                "emails": _cap(emails),
                "reminders": _cap(reminders),
                "knowledge": _cap(knowledge),
                "inbox_hub": _cap(inbox_hub),
            }
        )

    # ------------------------------------------------------------------
    # Individual count helpers — each runs a single aggregated query
    # ------------------------------------------------------------------

    @staticmethod
    def _ticket_count(tenant, user, is_agent):
        """Active tickets needing attention, scoped by role.

        Counts every non-closed, non-resolved ticket — i.e. open,
        in-progress *and* waiting — so the badge matches the active total a
        user sees on the ticket list (the sum of the non-done status tabs:
        Open + In Progress + Waiting). Resolved and closed are terminal
        "done" states and stay out of the count.
        """
        from apps.tickets.models import Ticket, TicketStatus

        active_statuses = TicketStatus.unscoped.filter(
            tenant=tenant,
            is_closed=False,
        ).exclude(
            slug__in=["resolved", "closed"],
        ).values_list("pk", flat=True)

        qs = Ticket.unscoped.filter(
            tenant=tenant,
            status_id__in=active_statuses,
            is_deleted=False,
        )

        if is_agent:
            from apps.tickets.access import agent_visible_tickets_q

            qs = qs.filter(agent_visible_tickets_q(user))

        return qs.count()

    @staticmethod
    def _calendar_count(tenant, user):
        """Activities due today or overdue that are incomplete and assigned to user."""
        try:
            from apps.crm.models import Activity
        except ImportError:
            # CRM app not yet installed
            return 0

        end_of_today = timezone.now().replace(hour=23, minute=59, second=59)

        return (
            Activity.unscoped.filter(
                tenant=tenant,
                assigned_to=user,
                completed_at__isnull=True,
                due_at__lte=end_of_today,
            )
            .count()
        )

    @staticmethod
    def _message_count(tenant, user):
        """
        Unread chat messages across conversations the user participates in.

        Unread = ``created_at > participant.last_read_at`` (or the user has
        never opened the conversation). Excludes the user's own messages.
        Scopes by conversation tenant so cross-tenant chats never bleed
        into another workspace's badge.
        """
        from apps.messaging.models import ConversationParticipant, Message

        participations = list(
            ConversationParticipant.objects.filter(
                user=user,
                conversation__tenant=tenant,
            ).values("conversation_id", "last_read_at")
        )
        if not participations:
            return 0

        # Build one ORed predicate over every (conversation, last_read_at)
        # pair so the whole count is a single DB roundtrip instead of N+1.
        cond = Q()
        for p in participations:
            per = Q(conversation_id=p["conversation_id"])
            if p["last_read_at"]:
                per &= Q(created_at__gt=p["last_read_at"])
            cond |= per

        return (
            Message.unscoped.filter(cond)
            .exclude(author=user)
            .count()
        )

    @staticmethod
    def _email_count(tenant, user):
        """Unactioned emails in *this user's* personal inbox.

        Mirrors the scope the Inbox page (``/inbox/``) actually shows so the
        badge and the page stay in sync. That page is a personal surface — it
        dual-loads ``?assigned=me`` (mail handed to the agent from the Emails
        triage desk) and ``?internal=true&mine=true`` (internal mail addressed
        to the agent). Customer mail lives in the Emails triage desk (its own
        badge), and the
        tenant's *outbound* system records (sent ticket replies / notification
        copies persisted for threading) are noise here, so both are excluded.

        Counts only ``pending``/``linked`` rows — i.e. mail still awaiting
        action — so the badge clears as the agent works through the inbox,
        rather than counting every row that was ever received.
        """
        from apps.inbound_email.models import InboundEmail

        SenderType = InboundEmail.SenderType
        Direction = InboundEmail.Direction
        InboxStatus = InboundEmail.InboxStatus

        # Assigned to me (handoff), OR an internal message addressed to me
        # (excluding outbound system notification copies).
        personal = Q(assignee=user) | (
            ~Q(sender_type=SenderType.CUSTOMER)
            & Q(recipient_email__iexact=(user.email or ""))
            & ~(Q(direction=Direction.OUTBOUND) & Q(sender_type=SenderType.SYSTEM))
        )

        return (
            InboundEmail.objects.filter(tenant=tenant)
            .filter(personal)
            .filter(inbox_status__in=[InboxStatus.PENDING, InboxStatus.LINKED])
            .exclude(status=InboundEmail.Status.BOUNCED)
            .distinct()
            .count()
        )

    @staticmethod
    def _reminder_count(tenant, user, is_agent):
        """Pending overdue reminders, scoped by role."""
        try:
            from apps.crm.models import Reminder
        except ImportError:
            return 0

        qs = Reminder.unscoped.filter(
            tenant=tenant,
            completed_at__isnull=True,
            cancelled_at__isnull=True,
            scheduled_at__lt=timezone.now(),
        )

        if is_agent:
            qs = qs.filter(Q(assigned_to=user) | Q(created_by=user))

        return qs.count()

    @staticmethod
    def _inbox_hub_count(tenant, user, membership):
        """Untriaged Hub emails awaiting triage.

        The Inbox Hub is a triage-only desk: it lists exactly the NEW
        (untriaged) backlog. Once an email is converted, assigned,
        escalated or dismissed it leaves the Hub, so the badge counts only
        ``state=NEW`` — matching what the page actually shows.

        Access is department-scoped: supervisors (Manager+) always, agent-tier
        only if they belong to a ``Department``, Viewers never. Anyone locked
        out has their badge zeroed to match the hidden nav entry + 403 page.
        """
        try:
            from apps.inbox_hub.models import HubEmail
        except ImportError:
            return 0

        from django.db.models import Q

        from apps.inbox_hub.access import can_access_inbox_hub, user_department_ids

        if not can_access_inbox_hub(membership, user=user, tenant=tenant):
            return 0

        qs = HubEmail.unscoped.filter(tenant=tenant, state=HubEmail.State.NEW)
        # Agent-tier only count their own departments' untriaged mail (plus the
        # unrouted shared pool, plus any NEW mail assigned to them) so the badge
        # matches the NEW subset of their scoped list view (access.hub_rows_q);
        # supervisors (Manager+) see the whole tenant's NEW backlog.
        if membership.effective_role.hierarchy_level > 20:
            dept_ids = user_department_ids(user, tenant)
            qs = qs.filter(
                Q(department_id__in=list(dept_ids))
                | Q(department__isnull=True)
                | Q(assignee_id=user.pk)
            )
        return qs.count()

    @staticmethod
    def _knowledge_count(tenant, user):
        """Unread KB notifications (review requests and article reviews)."""
        from apps.notifications.models import Notification, NotificationType

        return (
            Notification.unscoped.filter(
                tenant=tenant,
                recipient=user,
                is_read=False,
                type__in=[
                    NotificationType.KB_REVIEW_REQUESTED,
                    NotificationType.KB_ARTICLE_REVIEWED,
                ],
            )
            .count()
        )
