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

from django.contrib.contenttypes.models import ContentType
from django.db.models import Q, Count
from django.utils import timezone
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import IsTenantMember, _get_membership

logger = logging.getLogger(__name__)

MAX_BADGE = 99


def _cap(n):
    """Clamp count to MAX_BADGE."""
    return min(n, MAX_BADGE)


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
        is_agent = membership and membership.role.hierarchy_level > 20

        tickets = self._ticket_count(tenant, user, is_agent)
        calendar = self._calendar_count(tenant, user)
        messages = self._message_count(tenant, user)
        emails = self._email_count(tenant)
        reminders = self._reminder_count(tenant, user, is_agent)
        knowledge = self._knowledge_count(tenant, user)

        return Response(
            {
                "tickets": _cap(tickets),
                "calendar": _cap(calendar),
                "messages": _cap(messages),
                "emails": _cap(emails),
                "reminders": _cap(reminders),
                "knowledge": _cap(knowledge),
            }
        )

    # ------------------------------------------------------------------
    # Individual count helpers — each runs a single aggregated query
    # ------------------------------------------------------------------

    @staticmethod
    def _ticket_count(tenant, user, is_agent):
        """Active tickets (open or in-progress/review), scoped by role."""
        from apps.tickets.models import Ticket, TicketStatus

        active_statuses = TicketStatus.unscoped.filter(
            tenant=tenant,
            is_closed=False,
        ).exclude(
            slug__in=["resolved", "waiting", "closed"],
        ).values_list("pk", flat=True)

        qs = Ticket.unscoped.filter(
            tenant=tenant,
            status_id__in=active_statuses,
        )

        if is_agent:
            qs = qs.filter(Q(assignee=user) | Q(created_by=user))

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
        Unread comments on tickets assigned to the user.

        Unread = no CommentRead row for (comment, user),
        excluding internal notes and the user's own comments.
        """
        from apps.comments.models import Comment, CommentRead
        from apps.tickets.models import Ticket

        ticket_ct = ContentType.objects.get_for_model(Ticket)

        assigned_ticket_ids = Ticket.unscoped.filter(
            tenant=tenant, assignee=user,
        ).values_list("pk", flat=True)

        read_comment_ids = CommentRead.objects.filter(
            user=user,
        ).values_list("comment_id", flat=True)

        return (
            Comment.unscoped.filter(
                tenant=tenant,
                content_type=ticket_ct,
                object_id__in=assigned_ticket_ids,
                is_internal=False,
            )
            .exclude(author=user)
            .exclude(pk__in=read_comment_ids)
            .count()
        )

    @staticmethod
    def _email_count(tenant):
        """Unread inbound emails for this tenant."""
        from apps.inbound_email.models import InboundEmail

        return (
            InboundEmail.objects.filter(
                tenant=tenant,
                is_read=False,
            )
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
