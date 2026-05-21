"""
Tenant-aware admin mixin and superuser-only admin site.

Usage:
    from main.admin import TenantFilteredAdmin

    @admin.register(MyModel)
    class MyModelAdmin(TenantFilteredAdmin, admin.ModelAdmin):
        ...
"""

from django import forms
from django.contrib import admin


class SuperuserOnlyAdminSite(admin.AdminSite):
    """Django admin restricted to the application owner (superuser) only."""

    def has_permission(self, request):
        return request.user.is_active and request.user.is_superuser


# Replace the default admin site so only superusers can access /admin/.
admin.site.__class__ = SuperuserOnlyAdminSite


class TenantFilteredAdmin:
    """
    Mixin for ModelAdmin classes managing TenantScopedModel subclasses.

    - List view: filters by ``request.tenant`` when a tenant is resolved
      from the host (subdomain or custom domain). On the bare admin
      domain, all tenants' rows are shown.
    - Add/change view: injects a ``tenant`` ``ModelChoiceField`` into the
      form (the model field is ``editable=False`` so admin's auto-form
      omits it). Defaulted to ``request.tenant`` when present, required
      on the bare domain.
    - Save: sets ``obj.tenant`` from the form pick (or ``request.tenant``
      as a fallback) before the model ``save()`` so the no-tenant-context
      guard in :class:`main.models.TenantScopedModel.save` never trips.
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

    def get_form(self, request, obj=None, change=False, **kwargs):
        form = super().get_form(request, obj, change=change, **kwargs)
        if not hasattr(self.model, "tenant"):
            return form

        # Django's `_get_form_for_get_fields` calls `get_form(fields=None)`
        # to discover the form's base_fields, then passes those back to
        # `modelform_factory(fields=...)`. If we inject 'tenant' here in
        # that recursive call, it leaks into Meta.fields, and modelform
        # construction errors because `TenantScopedModel.tenant` is
        # `editable=False`. Return the unmodified form for that case.
        if "fields" in kwargs and kwargs["fields"] is None:
            return form

        # Lazy import to avoid app-loading order issues.
        from apps.tenants.models import Tenant

        request_tenant = getattr(request, "tenant", None)
        instance_tenant_id = getattr(obj, "tenant_id", None) if obj else None

        class _TenantPickerForm(form):
            tenant = forms.ModelChoiceField(
                queryset=Tenant.objects.filter(is_active=True).order_by("name"),
                required=True,
                help_text=(
                    "Tenant this record belongs to. Defaulted from the "
                    "current subdomain when present."
                ),
            )

            def __init__(self, *args, **form_kwargs):
                super().__init__(*args, **form_kwargs)
                initial = instance_tenant_id or (
                    request_tenant.pk if request_tenant is not None else None
                )
                if initial is not None:
                    self.fields["tenant"].initial = initial

        return _TenantPickerForm

    def save_model(self, request, obj, form, change):
        if hasattr(obj, "tenant_id") and not obj.tenant_id:
            picked = form.cleaned_data.get("tenant") if form is not None else None
            if picked is not None:
                obj.tenant = picked
            elif getattr(request, "tenant", None) is not None:
                obj.tenant = request.tenant
        super().save_model(request, obj, form, change)
