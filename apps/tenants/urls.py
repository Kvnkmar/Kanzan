"""
URL configuration for the tenants app.

Include this in the project root URL conf, e.g.::

    path("api/v1/tenants/", include("apps.tenants.urls")),
"""

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.tenants.views import TenantSettingsViewSet, TenantViewSet

router = DefaultRouter()
router.register(r"tenants", TenantViewSet, basename="tenant")

app_name = "tenants"

urlpatterns = [
    # Standard CRUD routes for Tenant.
    path("", include(router.urls)),
    # Singleton settings endpoints for the current tenant.
    path(
        "settings/",
        TenantSettingsViewSet.as_view(
            {"get": "retrieve", "patch": "partial_update"}
        ),
        name="tenant-settings",
    ),
    # Logo upload/delete for the current tenant.
    path(
        "settings/logo/",
        TenantSettingsViewSet.as_view(
            {"post": "logo", "delete": "logo"}
        ),
        name="tenant-settings-logo",
    ),
]
