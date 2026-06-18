"""Object-level authorization for attachments.

The attachment API was previously gated only by ``IsAuthenticated`` +
``IsTenantMember`` -- so any tenant member could read/download/upload an
attachment on ANY object in the tenant, including tickets they aren't assigned
to and internal comments they shouldn't see (audit findings attachments-1/2/3).

``can_access_target`` is the single source of truth: given a target
(content_type, object_id), decide whether ``user`` may attach to / read
attachments of that object. Admin/Manager (effective hierarchy <= 20) bypass.
Known sensitive targets (tickets, comments, messages) get explicit checks;
other tenant-scoped targets fall through to allow (tenant isolation still
applies) and can be tightened later.
"""

import logging

logger = logging.getLogger(__name__)


def _effective_level(user, tenant):
    """Effective role hierarchy level for ``user`` in ``tenant`` (lower = more
    privileged). Returns a large sentinel when the user has no active
    membership, so non-members are treated as least-privileged."""
    from apps.accounts.models import TenantMembership

    membership = (
        TenantMembership.objects.filter(user=user, tenant=tenant, is_active=True)
        .select_related("role", "temporary_role")
        .first()
    )
    if membership is None:
        return 999
    try:
        return membership.effective_role.hierarchy_level
    except Exception:  # pragma: no cover - defensive
        return 999


def can_access_target(user, tenant, content_type, object_id):
    """Return True if ``user`` may access the (content_type, object_id) target."""
    level = _effective_level(user, tenant)
    if level <= 20:  # Admin / Manager see all tenant data.
        return True

    model_class = content_type.model_class()
    if model_class is None:
        return False

    target = model_class._default_manager.filter(pk=object_id).first()
    if target is None:
        return False

    # Tenant isolation (belt-and-braces; managers already scope by tenant).
    if tenant and hasattr(target, "tenant_id") and target.tenant_id != tenant.pk:
        return False

    key = (content_type.app_label, content_type.model)

    if key == ("tickets", "ticket"):
        from apps.tickets.access import agent_can_see_ticket

        return agent_can_see_ticket(user, target)

    if key == ("comments", "comment"):
        # Internal comments are hidden from viewers (hierarchy > 30).
        if getattr(target, "is_internal", False) and level > 30:
            return False
        parent = getattr(target, "content_object", None)
        if parent is not None and parent.__class__.__name__ == "Ticket":
            from apps.tickets.access import agent_can_see_ticket

            return agent_can_see_ticket(user, parent)
        return True

    if key == ("messaging", "message"):
        conversation_id = getattr(target, "conversation_id", None)
        if conversation_id is None:
            return False
        from apps.messaging.models import ConversationParticipant

        return ConversationParticipant.objects.filter(
            conversation_id=conversation_id, user=user
        ).exists()

    # Other tenant-scoped target types are not in the audit scope; tenant
    # isolation already applies. Tighten on a per-type basis as needed.
    return True
