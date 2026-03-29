"""
DRF ViewSets for the contacts app.

Provides full CRUD for Company, Contact, and ContactGroup resources.
All querysets are automatically tenant-scoped via TenantAwareManager.
"""

from django.db.models import Count
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.accounts.permissions import HasTenantPermission
from apps.contacts.filters import CompanyFilter, ContactFilter
from apps.contacts.models import Company, Contact, ContactGroup
from apps.contacts.serializers import (
    CompanyListSerializer,
    CompanySerializer,
    ContactCreateSerializer,
    ContactGroupSerializer,
    ContactListSerializer,
    ContactSerializer,
)


class CompanyViewSet(viewsets.ModelViewSet):
    """
    Full CRUD for companies within the current tenant.

    Permission enforcement via ``HasTenantPermission`` and ``permission_resource``.

    list:   GET    /companies/
    create: POST   /companies/
    read:   GET    /companies/{id}/
    update: PUT    /companies/{id}/
    patch:  PATCH  /companies/{id}/
    delete: DELETE /companies/{id}/

    Supports search on ``name`` and ``domain``.
    Supports filtering by ``industry`` and ``size``.
    """

    permission_classes = [IsAuthenticated, HasTenantPermission]
    filterset_class = CompanyFilter
    search_fields = ["name", "domain"]
    ordering_fields = ["name", "created_at", "updated_at"]
    ordering = ["-created_at"]
    permission_resource = "company"

    def get_queryset(self):
        return Company.objects.annotate(contact_count=Count("contacts")).all()

    def get_serializer_class(self):
        if self.action == "list":
            return CompanyListSerializer
        return CompanySerializer

    def perform_create(self, serializer):
        serializer.save(tenant=self.request.tenant)


class ContactViewSet(viewsets.ModelViewSet):
    """
    Full CRUD for contacts within the current tenant.

    list:        GET    /contacts/
    create:      POST   /contacts/
    read:        GET    /contacts/{id}/
    update:      PUT    /contacts/{id}/
    patch:       PATCH  /contacts/{id}/
    delete:      DELETE /contacts/{id}/

    Supports search on ``first_name``, ``last_name``, ``email``,
    and ``company__name``.
    Supports filtering by ``company``, ``source``, ``is_active``,
    and ``created_at`` date range.
    """

    permission_classes = [IsAuthenticated, HasTenantPermission]
    filterset_class = ContactFilter
    search_fields = [
        "first_name",
        "last_name",
        "email",
        "company__name",
    ]
    ordering_fields = [
        "first_name",
        "last_name",
        "email",
        "created_at",
        "updated_at",
    ]
    ordering = ["-created_at"]
    permission_resource = "contact"

    def get_queryset(self):
        qs = Contact.objects.select_related("company").all()

        # Row-level filtering: viewers only see contacts linked to
        # tickets they created or are assigned to.
        user = self.request.user
        if not user.is_superuser:
            tenant = getattr(self.request, "tenant", None)
            if tenant:
                from apps.accounts.models import TenantMembership

                cache_attr = "_cached_tenant_membership"
                if hasattr(self.request, cache_attr):
                    membership = getattr(self.request, cache_attr)
                else:
                    membership = (
                        TenantMembership.objects.select_related("role")
                        .filter(user=user, tenant=tenant, is_active=True)
                        .first()
                    )
                    setattr(self.request, cache_attr, membership)

                if membership and membership.role.hierarchy_level > 20:
                    from django.db.models import Q

                    from apps.tickets.models import Ticket

                    ticket_contact_ids = (
                        Ticket.unscoped.filter(tenant=tenant)
                        .filter(Q(created_by=user) | Q(assignee=user))
                        .exclude(contact__isnull=True)
                        .values_list("contact_id", flat=True)
                    )
                    qs = qs.filter(id__in=ticket_contact_ids)

        return qs

    def get_serializer_class(self):
        if self.action == "list":
            return ContactListSerializer
        if self.action in ("create", "update", "partial_update"):
            return ContactCreateSerializer
        return ContactSerializer

    def perform_create(self, serializer):
        from apps.billing.services import PlanLimitChecker

        PlanLimitChecker(self.request.tenant).check_can_create_contact()
        serializer.save(tenant=self.request.tenant)

    @action(detail=False, methods=["post"], url_path="bulk-action")
    def bulk_action(self, request):
        """
        Apply an action to multiple contacts at once.

        POST /api/v1/contacts/contacts/bulk-action/
        {
            "action": "delete|add_to_group|remove_from_group",
            "contact_ids": ["uuid1", ...],
            "params": { "group_id": "uuid" }
        }
        """
        action_name = request.data.get("action")
        contact_ids = request.data.get("contact_ids", [])
        params = request.data.get("params", {})

        if not action_name or not contact_ids:
            return Response(
                {"error": "action and contact_ids are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        contacts = Contact.objects.filter(id__in=contact_ids)
        if contacts.count() != len(contact_ids):
            return Response(
                {"error": "Some contacts not found or access denied."},
                status=status.HTTP_404_NOT_FOUND,
            )

        count = 0
        details = []

        if action_name == "delete":
            count = contacts.count()
            contacts.delete()
            details.append(f"Deleted {count} contact(s)")

        elif action_name == "add_to_group":
            group_id = params.get("group_id")
            if not group_id:
                return Response(
                    {"error": "group_id is required."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            group = ContactGroup.objects.filter(id=group_id).first()
            if not group:
                return Response(
                    {"error": "Group not found."},
                    status=status.HTTP_404_NOT_FOUND,
                )
            for contact in contacts:
                if not group.contacts.filter(id=contact.id).exists():
                    group.contacts.add(contact)
                    count += 1
            details.append(f"Added {count} contact(s) to '{group.name}'")

        elif action_name == "remove_from_group":
            group_id = params.get("group_id")
            if not group_id:
                return Response(
                    {"error": "group_id is required."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            group = ContactGroup.objects.filter(id=group_id).first()
            if not group:
                return Response(
                    {"error": "Group not found."},
                    status=status.HTTP_404_NOT_FOUND,
                )
            count = contacts.filter(groups=group).count()
            group.contacts.remove(*contacts)
            details.append(f"Removed {count} contact(s) from '{group.name}'")

        else:
            return Response(
                {"error": f"Unknown action: {action_name}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(
            {
                "success": True,
                "contacts_updated": count,
                "action": action_name,
                "details": details,
            },
            status=status.HTTP_200_OK,
        )


class ContactGroupViewSet(viewsets.ModelViewSet):
    """
    Full CRUD for contact groups within the current tenant.

    list:             GET    /contact-groups/
    create:           POST   /contact-groups/
    read:             GET    /contact-groups/{id}/
    update:           PUT    /contact-groups/{id}/
    patch:            PATCH  /contact-groups/{id}/
    delete:           DELETE /contact-groups/{id}/
    add_contacts:     POST   /contact-groups/{id}/add_contacts/
    remove_contacts:  POST   /contact-groups/{id}/remove_contacts/
    """

    queryset = ContactGroup.objects.prefetch_related("contacts").all()
    serializer_class = ContactGroupSerializer
    permission_classes = [IsAuthenticated, HasTenantPermission]
    search_fields = ["name"]
    ordering_fields = ["name", "created_at"]
    ordering = ["-created_at"]
    permission_resource = "contact_group"

    def perform_create(self, serializer):
        serializer.save(tenant=self.request.tenant)

    @action(detail=True, methods=["post"], url_path="add_contacts")
    def add_contacts(self, request, pk=None):
        """
        Add contacts to this group.

        POST /contact-groups/{id}/add_contacts/
        {"contact_ids": ["<uuid>", ...]}
        """
        group = self.get_object()
        contact_ids = request.data.get("contact_ids", [])

        if not isinstance(contact_ids, list) or not contact_ids:
            return Response(
                {"detail": "A non-empty 'contact_ids' list is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        contacts = Contact.objects.filter(id__in=contact_ids)
        existing_ids = set(
            group.contacts.filter(id__in=contact_ids).values_list("id", flat=True)
        )
        new_contacts = [c for c in contacts if c.id not in existing_ids]
        group.contacts.add(*new_contacts)
        added_count = len(new_contacts)

        return Response(
            {
                "detail": f"{added_count} contact(s) added to group '{group.name}'.",
                "added": added_count,
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=True, methods=["post"], url_path="remove_contacts")
    def remove_contacts(self, request, pk=None):
        """
        Remove contacts from this group.

        POST /contact-groups/{id}/remove_contacts/
        {"contact_ids": ["<uuid>", ...]}
        """
        group = self.get_object()
        contact_ids = request.data.get("contact_ids", [])

        if not isinstance(contact_ids, list) or not contact_ids:
            return Response(
                {"detail": "A non-empty 'contact_ids' list is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        contacts = Contact.objects.filter(id__in=contact_ids)
        removed_count = contacts.count()
        group.contacts.remove(*contacts)

        return Response(
            {
                "detail": f"{removed_count} contact(s) removed from group '{group.name}'.",
                "removed": removed_count,
            },
            status=status.HTTP_200_OK,
        )
