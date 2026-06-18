"""
Template context processor that exposes the current tenant and the
authenticated user's membership / role to all templates.
"""


def tenant_context(request):
    """
    Add the current tenant and, when the user is authenticated, their
    TenantMembership and role information to the template context.

    Context variables added:
        tenant              - the resolved Tenant instance (or None)
        membership          - the user's TenantMembership for this tenant (or None)
        user_role           - the Role instance from the membership (or None)
        is_admin            - True if hierarchy_level <= 10 (Admin only)
        is_admin_or_manager - True if hierarchy_level <= 20 (Admin or Manager)
        is_agent_or_above  - True if hierarchy_level <= 30 (Admin, Manager, or Agent)
    """
    tenant = getattr(request, "tenant", None)
    membership = None
    user_role = None
    is_admin = False
    is_admin_or_manager = False
    is_agent_or_above = False

    if tenant and hasattr(request, "user") and request.user.is_authenticated:
        # Reuse cached membership if already resolved by DRF permissions
        cache_attr = "_cached_tenant_membership"
        if hasattr(request, cache_attr):
            membership = getattr(request, cache_attr)
        else:
            from apps.accounts.models import TenantMembership

            membership = (
                TenantMembership.objects.select_related("role", "temporary_role")
                .filter(user=request.user, tenant=tenant, is_active=True)
                .first()
            )
            setattr(request, cache_attr, membership)

        if membership:
            user_role = membership.effective_role
            is_admin = user_role.hierarchy_level <= 10
            is_admin_or_manager = user_role.hierarchy_level <= 20
            is_agent_or_above = user_role.hierarchy_level <= 30

    # Inbox Hub is department-scoped: Manager+ always, agent-tier only if they
    # belong to a Department, Viewers never. Drives sidebar-entry rendering.
    can_access_inbox_hub = False
    if tenant and membership:
        from apps.inbox_hub.access import can_access_inbox_hub as _can_access_hub

        can_access_inbox_hub = _can_access_hub(
            membership, user=request.user, tenant=tenant,
        )

    # Check if VoIP is enabled for this tenant
    voip_enabled = False
    if tenant and membership:
        from apps.voip.models import VoIPSettings

        voip_enabled = VoIPSettings.objects.filter(
            tenant=tenant, is_active=True,
        ).exists()

    # Derive the full theme palette from the tenant's primary + accent so
    # every --crm-primary*/--crm-accent* variable picks up the choice, not
    # just the handful explicitly listed in base.html.
    from apps.tenants.colors import derive_palette

    settings_obj = getattr(tenant, "settings", None) if tenant else None
    tenant_palette = derive_palette(
        getattr(settings_obj, "primary_color", None),
        getattr(settings_obj, "accent_color", None),
    )

    from django.conf import settings as django_settings

    return {
        "tenant": tenant,
        "membership": membership,
        "user_role": user_role,
        "is_admin": is_admin,
        "is_admin_or_manager": is_admin_or_manager,
        "is_agent_or_above": is_agent_or_above,
        "can_access_inbox_hub": can_access_inbox_hub,
        "voip_enabled": voip_enabled,
        "tenant_palette": tenant_palette,
        "BASE_URL": django_settings.BASE_URL,
    }
