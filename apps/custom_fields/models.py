"""
Models for the custom_fields app.

Provides CustomFieldDefinition for tenant-configurable field schemas and
CustomFieldValue for indexed/filterable storage of custom field values,
linked to any model via GenericForeignKey.
"""

from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models

from main.models import TenantScopedModel


class FieldType(models.TextChoices):
    """Supported field types for custom field definitions."""

    TEXT = "text", "Text"
    TEXTAREA = "textarea", "Textarea"
    NUMBER = "number", "Number"
    DATE = "date", "Date"
    SELECT = "select", "Select"
    MULTISELECT = "multiselect", "Multi-select"
    CHECKBOX = "checkbox", "Checkbox"
    FILE = "file", "File"


class ModuleType(models.TextChoices):
    """Entity types that support custom fields."""

    TICKET = "ticket", "Ticket"
    CONTACT = "contact", "Contact"
    COMPANY = "company", "Company"


class CustomFieldDefinition(TenantScopedModel):
    """
    Tenant-scoped custom field schema.

    Defines a field that tenants can add to tickets, contacts, or companies.
    Each definition specifies the field type, validation rules, select options,
    display order, and role-based visibility.
    """

    module = models.CharField(
        max_length=50,
        choices=ModuleType.choices,
        help_text="The entity type this field belongs to.",
    )
    name = models.CharField(
        max_length=100,
        help_text="Human-readable field label.",
    )
    slug = models.SlugField(
        max_length=100,
        help_text="Machine-readable identifier; used as key in custom_data JSON.",
    )
    field_type = models.CharField(
        max_length=20,
        choices=FieldType.choices,
    )
    options = models.JSONField(
        default=list,
        blank=True,
        help_text='For select/multiselect: [{"value": "...", "label": "..."}].',
    )
    is_required = models.BooleanField(
        default=False,
        help_text="Whether this field is mandatory when saving the entity.",
    )
    default_value = models.JSONField(
        null=True,
        blank=True,
        help_text="Default value applied when the field is left empty.",
    )
    validation_rules = models.JSONField(
        default=dict,
        blank=True,
        help_text=(
            "Validation constraints: "
            '{"min_length": 3, "max_length": 500, "min": 0, "max": 100, "regex": "..."}.'
        ),
    )
    order = models.PositiveIntegerField(
        default=0,
        help_text="Display order within the module (lower = first).",
    )
    visible_to_roles = models.ManyToManyField(
        "accounts.Role",
        blank=True,
        related_name="visible_custom_fields",
        help_text="Roles that can see this field. Empty = visible to all.",
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Inactive fields are hidden from forms but data is retained.",
    )

    class Meta:
        verbose_name = "custom field definition"
        verbose_name_plural = "custom field definitions"
        unique_together = [("tenant", "module", "slug")]
        ordering = ["module", "order"]

    def __str__(self):
        return f"{self.name} ({self.get_module_display()} / {self.get_field_type_display()})"


class CustomFieldValue(TenantScopedModel):
    """
    Indexed storage for a single custom field value.

    Uses a generic foreign key to attach to any entity (Ticket, Contact,
    Company) and stores the value in a type-appropriate column for
    efficient querying and filtering.
    """

    field = models.ForeignKey(
        CustomFieldDefinition,
        on_delete=models.CASCADE,
        related_name="values",
    )
    content_type = models.ForeignKey(
        ContentType,
        on_delete=models.CASCADE,
    )
    object_id = models.UUIDField()
    content_object = GenericForeignKey("content_type", "object_id")

    value_text = models.TextField(
        null=True,
        blank=True,
        help_text="Stores text, textarea, select, multiselect, and file values.",
    )
    value_number = models.DecimalField(
        max_digits=20,
        decimal_places=6,
        null=True,
        blank=True,
        help_text="Stores number field values.",
    )
    value_date = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Stores date field values.",
    )
    value_bool = models.BooleanField(
        null=True,
        blank=True,
        help_text="Stores checkbox field values.",
    )

    class Meta:
        verbose_name = "custom field value"
        verbose_name_plural = "custom field values"
        unique_together = [("field", "content_type", "object_id")]
        indexes = [
            models.Index(
                fields=["tenant", "field", "value_text"],
                name="cfv_tenant_field_text",
            ),
            models.Index(
                fields=["tenant", "field", "value_number"],
                name="cfv_tenant_field_number",
            ),
            models.Index(
                fields=["tenant", "field", "value_date"],
                name="cfv_tenant_field_date",
            ),
        ]
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.field.name} = {self.display_value}"

    @property
    def display_value(self):
        """Return the stored value from the appropriate typed column."""
        if self.value_bool is not None:
            return self.value_bool
        if self.value_number is not None:
            return self.value_number
        if self.value_date is not None:
            return self.value_date
        if self.value_text is not None:
            return self.value_text
        return None
