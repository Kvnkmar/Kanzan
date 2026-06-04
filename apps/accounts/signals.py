import logging

from django.contrib.auth import get_user_model
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from apps.accounts.models import Profile, TenantMembership
from apps.tenants.live import broadcast_live_event

logger = logging.getLogger(__name__)
User = get_user_model()


@receiver(post_save, sender=TenantMembership)
def create_profile_on_membership(sender, instance, created, **kwargs):
    """
    Automatically create a tenant-scoped Profile for the user when a
    TenantMembership is created, if one does not already exist.
    """
    if created:
        profile, was_created = Profile.objects.get_or_create(
            user=instance.user,
            tenant=instance.tenant,
        )
        if was_created:
            logger.info(
                "Auto-created profile for user %s in tenant %s.",
                instance.user.email,
                instance.tenant,
            )


# ── Live broadcasts ───────────────────────────────────────────────────
#
# Membership and profile changes drive things that are rendered on
# every page (sidebar avatar/name, permissions, member counts). When
# they change, every connected client in the tenant should pick them
# up without a manual reload.

def _serialise_membership(m: TenantMembership) -> dict:
    eff = m.effective_role if m.is_active else m.role
    return {
        "id":               str(m.id),
        "user_id":          str(m.user_id),
        "user_email":       m.user.email if m.user_id else None,
        "role_id":          str(m.role_id) if m.role_id else None,
        "role_name":        m.role.name if m.role_id else None,
        "effective_role":   eff.name if eff else None,
        "is_active":        m.is_active,
        "has_temp_role":    bool(getattr(m, "has_active_temporary_role", False)),
    }


def _serialise_profile(p: Profile) -> dict:
    return {
        "id":            str(p.id),
        "user_id":       str(p.user_id),
        "theme":         getattr(p, "theme", None),
        "density":       getattr(p, "density", None),
        "language":      getattr(p, "language", None),
        "date_format":   getattr(p, "date_format", None),
        "time_format":   getattr(p, "time_format", None),
        "dnd_enabled":   getattr(p, "dnd_enabled", None),
        "updated_at":    p.updated_at.isoformat() if getattr(p, "updated_at", None) else None,
    }


@receiver(post_save, sender=TenantMembership)
def broadcast_membership_save(sender, instance, created, **kwargs):
    event = "membership.created" if created else "membership.updated"
    broadcast_live_event(
        tenant=instance.tenant,
        event=event,
        payload=_serialise_membership(instance),
    )


@receiver(post_delete, sender=TenantMembership)
def broadcast_membership_delete(sender, instance, **kwargs):
    broadcast_live_event(
        tenant=instance.tenant,
        event="membership.deleted",
        payload={
            "id":      str(instance.id),
            "user_id": str(instance.user_id) if instance.user_id else None,
        },
    )


@receiver(post_save, sender=Profile)
def broadcast_profile_save(sender, instance, created, **kwargs):
    """Profile changes are only visually relevant to the user themselves
    (theme/density/dnd) but the sidebar name / avatar also lives off the
    related User. Broadcast tenant-wide so multi-tab sessions stay
    in-sync; clients filter by user_id for personal-preference UIs.
    """
    event = "profile.created" if created else "profile.updated"
    broadcast_live_event(
        tenant=instance.tenant,
        event=event,
        payload=_serialise_profile(instance),
    )


# ── User (cross-tenant fan-out) ────────────────────────────────────────
#
# ``User`` rows are not tenant-scoped, but the sidebar's avatar/name and
# the navbar greeting are rendered on every page of every tenant the
# user belongs to. When the user updates their profile we fan out a
# ``user.updated`` event to all of their active tenants so each open tab
# (which only listens to its own tenant's live channel) sees the change.

def _serialise_user(u) -> dict:
    full_name = (u.get_full_name() or "").strip() or u.email
    avatar_url = ""
    if getattr(u, "avatar", None):
        try:
            avatar_url = u.avatar.url
        except (ValueError, AttributeError):
            avatar_url = ""
    return {
        "id":          str(u.pk),
        "email":       u.email,
        "first_name":  u.first_name,
        "last_name":   u.last_name,
        "full_name":   full_name,
        "initial":     (full_name[:1] or "?").upper(),
        "avatar":      avatar_url,
    }


@receiver(post_save, sender=User)
def broadcast_user_save(sender, instance, created, **kwargs):
    if created:
        # New users have no memberships yet; the per-membership signal
        # below handles introducing them to their tenants.
        return
    payload = _serialise_user(instance)
    tenant_ids = TenantMembership.objects.filter(
        user=instance, is_active=True,
    ).values_list("tenant_id", flat=True)
    for tenant_id in tenant_ids:
        broadcast_live_event(
            tenant=tenant_id,
            event="user.updated",
            payload=payload,
        )
