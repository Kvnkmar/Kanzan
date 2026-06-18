"""
DRF ViewSets for the contacts app.

Provides full CRUD for Company, Contact, and ContactGroup resources.
All querysets are automatically tenant-scoped via TenantAwareManager.
"""

from django.db.models import Count
from drf_spectacular.utils import (
    OpenApiExample,
    extend_schema,
    extend_schema_view,
)
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.accounts.permissions import HasTenantPermission
from apps.contacts.filters import CompanyFilter, ContactFilter
from apps.contacts.models import Account, Company, Contact, ContactEvent, ContactGroup
from apps.contacts.serializers import (
    AccountListSerializer,
    AccountSerializer,
    CompanyListSerializer,
    CompanySerializer,
    ContactCreateSerializer,
    ContactEventSerializer,
    ContactGroupSerializer,
    ContactListSerializer,
    ContactSerializer,
)


@extend_schema_view(
    list=extend_schema(
        summary="List CRM accounts",
        description="Returns CRM accounts (book-of-business records) for the current tenant. Ordered by `-created_at` by default.",
        tags=["Accounts"],
    ),
    create=extend_schema(summary="Create a CRM account", tags=["Accounts"]),
    retrieve=extend_schema(summary="Retrieve a CRM account", tags=["Accounts"]),
    update=extend_schema(summary="Replace a CRM account", tags=["Accounts"]),
    partial_update=extend_schema(summary="Patch a CRM account", tags=["Accounts"]),
    destroy=extend_schema(summary="Delete a CRM account", tags=["Accounts"]),
)
class AccountViewSet(viewsets.ModelViewSet):
    """
    CRUD for CRM accounts within the current tenant.

    health_score is read-only (calculated nightly by Celery task).
    """

    permission_classes = [IsAuthenticated, HasTenantPermission]
    search_fields = ["name"]
    ordering_fields = ["name", "health_score", "mrr", "created_at"]
    ordering = ["-created_at"]
    permission_resource = "account"

    def get_queryset(self):
        return Account.objects.all()

    def get_serializer_class(self):
        if self.action == "list":
            return AccountListSerializer
        return AccountSerializer

    def perform_create(self, serializer):
        serializer.save(tenant=self.request.tenant)


@extend_schema_view(
    list=extend_schema(
        summary="List companies",
        description="Returns companies (organisations) in the current tenant, annotated with `contact_count`. Supports search on `name` / `domain` and filtering by `industry` / `size`.",
        tags=["Companies"],
    ),
    create=extend_schema(summary="Create a company", tags=["Companies"]),
    retrieve=extend_schema(summary="Retrieve a company", tags=["Companies"]),
    update=extend_schema(summary="Replace a company", tags=["Companies"]),
    partial_update=extend_schema(summary="Patch a company", tags=["Companies"]),
    destroy=extend_schema(summary="Delete a company", tags=["Companies"]),
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


@extend_schema_view(
    list=extend_schema(
        summary="List contacts",
        description=(
            "Returns the paginated contact list for the current tenant. Supports `search=` "
            "(matches email, first_name, last_name, phone), `ordering=`, and filterset "
            "params (company, account, etc.). Counts against `Plan.max_contacts` on create."
        ),
        tags=["Contacts"],
    ),
    create=extend_schema(
        summary="Create a contact",
        description="Email must be unique per tenant. Counts against `Plan.max_contacts`.",
        tags=["Contacts"],
        examples=[
            OpenApiExample(
                "Minimal contact",
                value={
                    "email": "jane@example.com",
                    "first_name": "Jane",
                    "last_name": "Doe",
                    "phone": "+1-555-0123",
                },
                request_only=True,
            ),
        ],
    ),
    retrieve=extend_schema(summary="Retrieve a contact", tags=["Contacts"]),
    update=extend_schema(summary="Replace a contact", tags=["Contacts"]),
    partial_update=extend_schema(summary="Patch a contact", tags=["Contacts"]),
    destroy=extend_schema(summary="Delete a contact", tags=["Contacts"]),
    context=extend_schema(
        summary="Contact 360 context",
        description="Return a contact summary for the ticket-detail sidebar (recent tickets, latest events, group memberships).",
        tags=["Contacts"],
    ),
    timeline=extend_schema(
        summary="Contact event timeline",
        description="Append-only `ContactEvent` timeline (360° interaction history).",
        tags=["Contacts"],
    ),
)
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
        "lead_score",
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

                if membership and membership.effective_role.hierarchy_level > 20:
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

    @extend_schema(
        summary="Bulk contact action",
        description=(
            "Apply one of `delete`, `add_to_group`, `remove_from_group` to multiple "
            "contacts at once. `params.group_id` is required for group actions."
        ),
        tags=["Contacts"],
        examples=[
            OpenApiExample(
                "Bulk delete",
                value={
                    "action": "delete",
                    "contact_ids": [
                        "ae12c1f0-1234-4abc-9def-1234567890ab",
                        "ae12c1f0-1234-4abc-9def-1234567890cd",
                    ],
                },
                request_only=True,
            ),
            OpenApiExample(
                "Add to a group",
                value={
                    "action": "add_to_group",
                    "contact_ids": ["ae12c1f0-1234-4abc-9def-1234567890ab"],
                    "params": {"group_id": "2b3c4d5e-6789-4abc-9def-1234567890ab"},
                },
                request_only=True,
            ),
        ],
    )
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

    @action(detail=True, methods=["get"], url_path="context")
    def context(self, request, pk=None):
        """
        Return a contact context summary for the ticket detail sidebar.

        Includes the contact's profile (with company + account value), ticket
        stats (total, open, avg CSAT), and the last 5 tickets for this contact
        within the current tenant.

        Accepts an optional ``?exclude_ticket=<uuid>`` query param to omit
        the currently viewed ticket from recent_tickets.

        Cached per contact per tenant for 60 seconds (see
        :func:`apps.contacts.context.build_contact_context`).
        """
        from apps.contacts.context import build_contact_context

        contact = self.get_object()
        tenant = getattr(request, "tenant", None)
        exclude_ticket = request.query_params.get("exclude_ticket")
        return Response(
            build_contact_context(contact, tenant, exclude_ticket=exclude_ticket)
        )

    @action(detail=True, methods=["get"], url_path="timeline")
    def timeline(self, request, pk=None):
        """
        Return a paginated, unified timeline of ContactEvents for a contact.

        GET /api/v1/contacts/contacts/{id}/timeline/

        Query params:
            source      — filter by source (ticket, activity, email, manual)
            event_type  — filter by event_type
            after       — ISO datetime, events after this timestamp
            before      — ISO datetime, events before this timestamp
        """
        contact = self.get_object()

        qs = ContactEvent.objects.filter(contact=contact)

        # Optional filters
        source = request.query_params.get("source")
        if source:
            qs = qs.filter(source=source)

        event_type = request.query_params.get("event_type")
        if event_type:
            qs = qs.filter(event_type=event_type)

        after = request.query_params.get("after")
        if after:
            qs = qs.filter(occurred_at__gte=after)

        before = request.query_params.get("before")
        if before:
            qs = qs.filter(occurred_at__lte=before)

        # Paginate with page_size=25
        from rest_framework.pagination import PageNumberPagination

        class TimelinePagination(PageNumberPagination):
            page_size = 25
            page_size_query_param = "page_size"
            max_page_size = 100

        paginator = TimelinePagination()
        page = paginator.paginate_queryset(qs, request)
        serializer = ContactEventSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)


@extend_schema_view(
    list=extend_schema(summary="List contact groups", tags=["Contact Groups"]),
    create=extend_schema(summary="Create a contact group", tags=["Contact Groups"]),
    retrieve=extend_schema(summary="Retrieve a contact group", tags=["Contact Groups"]),
    update=extend_schema(summary="Replace a contact group", tags=["Contact Groups"]),
    partial_update=extend_schema(summary="Patch a contact group", tags=["Contact Groups"]),
    destroy=extend_schema(summary="Delete a contact group", tags=["Contact Groups"]),
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

    @extend_schema(
        summary="Add contacts to group",
        description="Add one or more contacts to this group. Set-based — duplicates are silently ignored.",
        tags=["Contact Groups"],
        examples=[
            OpenApiExample(
                "Add by id list",
                value={"contact_ids": ["ae12c1f0-1234-4abc-9def-1234567890ab"]},
                request_only=True,
            ),
        ],
    )
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

    @extend_schema(
        summary="Remove contacts from group",
        description="Remove one or more contacts from this group.",
        tags=["Contact Groups"],
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
