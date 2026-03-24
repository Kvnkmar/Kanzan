"""
Phase 4f (part 1) — Custom fields tests.

Covers:
- CustomFieldDefinition CRUD
- Field type enforcement
- Tenant scoping
"""

import pytest

from conftest import CustomFieldDefinitionFactory
from main.context import clear_current_tenant, set_current_tenant


@pytest.mark.django_db
class TestCustomFieldDefinitionCRUD:
    def test_create_definition(self, admin_client):
        resp = admin_client.post("/api/v1/custom-fields/definitions/", {
            "module": "ticket",
            "name": "Region",
            "slug": "region",
            "field_type": "select",
            "options": [{"value": "us", "label": "US"}, {"value": "eu", "label": "EU"}],
        }, format="json")
        assert resp.status_code == 201
        assert resp.data["slug"] == "region"

    def test_list_definitions(self, admin_client, tenant):
        set_current_tenant(tenant)
        CustomFieldDefinitionFactory(tenant=tenant, slug="cf1")
        CustomFieldDefinitionFactory(tenant=tenant, slug="cf2")
        clear_current_tenant()

        resp = admin_client.get("/api/v1/custom-fields/definitions/")
        assert resp.status_code == 200
        assert resp.data["count"] >= 2

    def test_unique_slug_per_tenant_module(self, tenant):
        set_current_tenant(tenant)
        CustomFieldDefinitionFactory(tenant=tenant, module="ticket", slug="dup-slug")
        from django.db import IntegrityError
        with pytest.raises(IntegrityError):
            CustomFieldDefinitionFactory(tenant=tenant, module="ticket", slug="dup-slug")

    def test_same_slug_different_modules(self, tenant):
        set_current_tenant(tenant)
        CustomFieldDefinitionFactory(tenant=tenant, module="ticket", slug="shared-slug")
        cf2 = CustomFieldDefinitionFactory(tenant=tenant, module="contact", slug="shared-slug")
        assert cf2.pk is not None


@pytest.mark.django_db
class TestCustomFieldIsolation:
    def test_fields_scoped_to_tenant(self, tenant, tenant_b):
        set_current_tenant(tenant)
        CustomFieldDefinitionFactory(tenant=tenant, name="Tenant A Field", slug="a-field")
        clear_current_tenant()

        set_current_tenant(tenant_b)
        CustomFieldDefinitionFactory(tenant=tenant_b, name="Tenant B Field", slug="b-field")
        clear_current_tenant()

        from apps.custom_fields.models import CustomFieldDefinition
        set_current_tenant(tenant)
        assert CustomFieldDefinition.objects.count() == 1
        assert CustomFieldDefinition.objects.first().name == "Tenant A Field"
