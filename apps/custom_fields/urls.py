"""
URL configuration for the custom_fields app.

Registers the CustomFieldDefinition and CustomFieldValue ViewSets with
the DRF router.
"""

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.custom_fields.views import (
    CustomFieldDefinitionViewSet,
    CustomFieldValueViewSet,
)

app_name = "custom_fields"

router = DefaultRouter()
router.register(
    r"definitions",
    CustomFieldDefinitionViewSet,
    basename="customfielddefinition",
)
router.register(
    r"values",
    CustomFieldValueViewSet,
    basename="customfieldvalue",
)

urlpatterns = [
    path("", include(router.urls)),
]
