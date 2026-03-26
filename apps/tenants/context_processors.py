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
                TenantMembership.objects.select_related("role")
                .filter(user=request.user, tenant=tenant, is_active=True)
                .first()
            )
            setattr(request, cache_attr, membership)

        if membership:
            user_role = membership.role
            is_admin = user_role.hierarchy_level <= 10
            is_admin_or_manager = user_role.hierarchy_level <= 20
            is_agent_or_above = user_role.hierarchy_level <= 30

    from django.conf import settings as django_settings

    return {
        "tenant": tenant,
        "membership": membership,
        "user_role": user_role,
        "is_admin": is_admin,
        "is_admin_or_manager": is_admin_or_manager,
        "is_agent_or_above": is_agent_or_above,
        "BASE_URL": django_settings.BASE_URL,
    }
