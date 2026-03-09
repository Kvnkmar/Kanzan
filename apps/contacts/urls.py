"""
URL configuration for the contacts app.

Registers all contacts-related ViewSets with the DRF router.
Include this module in the project's root URL configuration:

    path("api/v1/contacts/", include("apps.contacts.urls")),
"""

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.contacts.views import CompanyViewSet, ContactGroupViewSet, ContactViewSet

app_name = "contacts"

router = DefaultRouter()
router.register("companies", CompanyViewSet, basename="company")
router.register("contacts", ContactViewSet, basename="contact")
router.register("contact-groups", ContactGroupViewSet, basename="contactgroup")

urlpatterns = [
    path("", include(router.urls)),
]
