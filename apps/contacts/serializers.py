"""
DRF serializers for the contacts app.

Provides CRUD representations for Company, Contact, and ContactGroup resources.
List serializers return lightweight payloads; detail serializers include nested data.
"""

from rest_framework import serializers

from apps.contacts.models import Account, Company, Contact, ContactEvent, ContactGroup


# ---------------------------------------------------------------------------
# Company
# ---------------------------------------------------------------------------


class CompanyListSerializer(serializers.ModelSerializer):
    """Lightweight company representation for list endpoints and FK display."""

    class Meta:
        model = Company
        fields = [
            "id",
            "name",
            "domain",
            "industry",
            "size",
            "created_at",
        ]
        read_only_fields = fields


class CompanySerializer(serializers.ModelSerializer):
    """Full company serializer used for detail / create / update operations."""

    contact_count = serializers.IntegerField(
        source="contacts.count",
        read_only=True,
    )

    class Meta:
        model = Company
        fields = [
            "id",
            "name",
            "domain",
            "industry",
            "size",
            "phone",
            "email",
            "address",
            "website",
            "notes",
            "custom_data",
            "contact_count",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def validate_name(self, value):
        """Ensure the company name is unique within the tenant."""
        request = self.context.get("request")
        tenant = getattr(request, "tenant", None) if request else None
        if tenant and value:
            qs = Company.objects.filter(tenant=tenant, name=value)
            if self.instance:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise serializers.ValidationError(
                    "A company with this name already exists."
                )
        return value


# ---------------------------------------------------------------------------
# Account
# ---------------------------------------------------------------------------


class AccountListSerializer(serializers.ModelSerializer):
    """Lightweight account representation for list endpoints."""

    class Meta:
        model = Account
        fields = [
            "id",
            "name",
            "industry",
            "company_size",
            "mrr",
            "health_score",
            "created_at",
        ]
        read_only_fields = fields


class AccountSerializer(serializers.ModelSerializer):
    """Full account serializer for detail / create / update."""

    class Meta:
        model = Account
        fields = [
            "id",
            "name",
            "industry",
            "company_size",
            "website",
            "mrr",
            "health_score",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "health_score", "created_at", "updated_at"]


# ---------------------------------------------------------------------------
# Contact
# ---------------------------------------------------------------------------


class ContactListSerializer(serializers.ModelSerializer):
    """Lightweight contact representation for list endpoints."""

    full_name = serializers.CharField(read_only=True)
    company = CompanyListSerializer(read_only=True)

    class Meta:
        model = Contact
        fields = [
            "id",
            "first_name",
            "last_name",
            "full_name",
            "email",
            "phone",
            "company",
            "job_title",
            "source",
            "is_active",
            "email_bouncing",
            "lead_score",
            "created_at",
        ]
        read_only_fields = fields


class ContactSerializer(serializers.ModelSerializer):
    """Full contact serializer used for detail views."""

    full_name = serializers.CharField(read_only=True)
    company = CompanyListSerializer(read_only=True)
    groups = serializers.SerializerMethodField()

    class Meta:
        model = Contact
        fields = [
            "id",
            "first_name",
            "last_name",
            "full_name",
            "email",
            "phone",
            "company",
            "job_title",
            "source",
            "notes",
            "is_active",
            "email_bouncing",
            "lead_score",
            "custom_data",
            "groups",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "lead_score", "created_at", "updated_at"]

    def get_groups(self, obj):
        """Return minimal group information for the contact."""
        return list(
            obj.groups.values("id", "name")
        )


class ContactCreateSerializer(serializers.ModelSerializer):
    """
    Serializer used for creating and updating contacts.

    Accepts a company UUID rather than nested company data, which is
    more practical for write operations.
    """

    full_name = serializers.CharField(read_only=True)

    class Meta:
        model = Contact
        fields = [
            "id",
            "first_name",
            "last_name",
            "full_name",
            "email",
            "phone",
            "company",
            "job_title",
            "source",
            "notes",
            "is_active",
            "custom_data",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def validate_company(self, value):
        """Ensure the company belongs to the same tenant as the request."""
        request = self.context.get("request")
        if value and request and hasattr(request, "tenant"):
            if value.tenant_id != request.tenant.id:
                raise serializers.ValidationError(
                    "Company does not belong to the current tenant."
                )
        return value

    def validate(self, attrs):
        """Check for duplicate email within the tenant."""
        request = self.context.get("request")
        tenant = getattr(request, "tenant", None) if request else None
        email = attrs.get("email")
        if tenant and email:
            qs = Contact.objects.filter(tenant=tenant, email=email)
            if self.instance:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise serializers.ValidationError(
                    {"email": "A contact with this email already exists."}
                )
        return attrs


# ---------------------------------------------------------------------------
# ContactGroup
# ---------------------------------------------------------------------------


class ContactGroupSerializer(serializers.ModelSerializer):
    """Full contact group serializer with nested contact summaries."""

    contacts = serializers.SerializerMethodField()
    contact_count = serializers.SerializerMethodField()
    contact_ids = serializers.PrimaryKeyRelatedField(
        queryset=Contact.objects.all(),
        many=True,
        write_only=True,
        required=False,
        source="contacts",
    )

    class Meta:
        model = ContactGroup
        fields = [
            "id",
            "name",
            "description",
            "contacts",
            "contact_count",
            "contact_ids",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def get_contacts(self, obj):
        """Return at most 50 contacts to prevent unbounded responses."""
        qs = obj.contacts.all()[:50]
        return ContactListSerializer(qs, many=True).data

    def get_contact_count(self, obj):
        return obj.contacts.count()


# ---------------------------------------------------------------------------
# ContactEvent (Timeline)
# ---------------------------------------------------------------------------


class ContactEventSerializer(serializers.ModelSerializer):
    """Read-only serializer for the unified contact timeline."""

    actor_name = serializers.SerializerMethodField()

    class Meta:
        model = ContactEvent
        fields = [
            "id",
            "event_type",
            "description",
            "metadata",
            "actor",
            "actor_name",
            "occurred_at",
            "source",
        ]
        read_only_fields = fields

    def get_actor_name(self, obj):
        if obj.actor:
            return obj.actor.get_full_name() or str(obj.actor)
        return None
