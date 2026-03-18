"""
Tenant-aware admin mixin and superuser-only admin site.

Usage:
    from main.admin import TenantFilteredAdmin

    @admin.register(MyModel)
    class MyModelAdmin(TenantFilteredAdmin, admin.ModelAdmin):
        ...
"""

from django.contrib import admin


class SuperuserOnlyAdminSite(admin.AdminSite):
    """Django admin restricted to the application owner (superuser) only."""

    def has_permission(self, request):
        return request.user.is_active and request.user.is_superuser


# Replace the default admin site so only superusers can access /admin/.
admin.site.__class__ = SuperuserOnlyAdminSite


class TenantFilteredAdmin:
    """
    Mixin for ModelAdmin classes that filters querysets by the tenant
    resolved from the request subdomain.

    - If a tenant is present (e.g. meeting.localhost), only that tenant's
      data is shown.
    - If no tenant is present (e.g. bare localhost), all data is shown
      (superuser cross-tenant view).

    Uses `Model.unscoped` if available to bypass the TenantAwareManager,
    then applies tenant filtering explicitly.
    """

    def get_queryset(self, request):
        model = self.model
        # Use unscoped manager if available to bypass TenantAwareManager
        if hasattr(model, "unscoped"):
            qs = model.unscoped.all()
        else:
            qs = super().get_queryset(request)

        # Filter by tenant if one is resolved from the subdomain
        tenant = getattr(request, "tenant", None)
        if tenant is not None and hasattr(model, "tenant"):
            qs = qs.filter(tenant=tenant)

        return qs
