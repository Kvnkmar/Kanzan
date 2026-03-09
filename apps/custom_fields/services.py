"""
Business-logic services for the custom_fields app.

Provides functions for validating custom data payloads, synchronising
CustomFieldValue rows from an instance's custom_data JSONField, and
retrieving field definitions filtered by role visibility.
"""

import json
import logging
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.utils.dateparse import parse_datetime

from apps.custom_fields.models import CustomFieldDefinition, CustomFieldValue, FieldType
from apps.custom_fields.validators import CustomFieldValidator

logger = logging.getLogger(__name__)


def validate_custom_data(tenant, module, custom_data):
    """
    Validate an entire custom_data dict against all active field definitions
    for the given tenant and module.

    Args:
        tenant: Tenant instance.
        module: Module string (e.g. "ticket", "contact", "company").
        custom_data: dict mapping field slugs to values.

    Raises:
        ValidationError with a dict of field-slug -> error messages.
    """
    if not isinstance(custom_data, dict):
        raise ValidationError(
            {"custom_data": "custom_data must be a JSON object."},
            code="invalid_type",
        )

    definitions = CustomFieldDefinition.unscoped.filter(
        tenant=tenant,
        module=module,
        is_active=True,
    )

    errors = {}
    for field_def in definitions:
        value = custom_data.get(field_def.slug)
        validator = CustomFieldValidator(field_def)

        try:
            validator.validate(value)
        except ValidationError as exc:
            errors[field_def.slug] = exc.messages if hasattr(exc, "messages") else [str(exc)]

    if errors:
        raise ValidationError(errors, code="custom_field_validation")


def sync_custom_field_values(instance, module):
    """
    Create or update CustomFieldValue rows from an instance's custom_data JSONField.

    Reads the ``custom_data`` attribute from the instance and upserts a
    CustomFieldValue record for each field definition that has a corresponding
    key in the data. Removes CustomFieldValue rows for fields no longer
    present in custom_data.

    Args:
        instance: Model instance with a ``custom_data`` JSONField and ``tenant``
                  attribute (e.g. Ticket, Contact).
        module: Module string (e.g. "ticket", "contact").
    """
    custom_data = getattr(instance, "custom_data", None)
    if custom_data is None or not isinstance(custom_data, dict):
        return

    content_type = ContentType.objects.get_for_model(instance)
    tenant = instance.tenant

    definitions = {
        fd.slug: fd
        for fd in CustomFieldDefinition.unscoped.filter(
            tenant=tenant,
            module=module,
            is_active=True,
        )
    }

    # Track which definition IDs we've processed so we can clean up stale values.
    processed_field_ids = set()

    for slug, value in custom_data.items():
        field_def = definitions.get(slug)
        if field_def is None:
            continue

        processed_field_ids.add(field_def.id)

        # Determine which typed column to populate.
        value_kwargs = _build_value_kwargs(field_def, value)

        CustomFieldValue.unscoped.update_or_create(
            field=field_def,
            content_type=content_type,
            object_id=instance.id,
            defaults={
                "tenant": tenant,
                **value_kwargs,
            },
        )

    # Remove CustomFieldValue rows for fields no longer in custom_data.
    CustomFieldValue.unscoped.filter(
        content_type=content_type,
        object_id=instance.id,
        field__tenant=tenant,
        field__module=module,
    ).exclude(
        field_id__in=processed_field_ids,
    ).delete()

    logger.debug(
        "Synced %d custom field values for %s %s (tenant %s).",
        len(processed_field_ids),
        module,
        instance.id,
        tenant,
    )


def get_field_definitions(tenant, module, user_role=None):
    """
    Return active custom field definitions for a tenant and module,
    optionally filtered by role visibility.

    Args:
        tenant: Tenant instance.
        module: Module string (e.g. "ticket", "contact", "company").
        user_role: Optional Role instance. If provided, only fields visible
                   to this role (or to all roles) are returned.

    Returns:
        QuerySet of CustomFieldDefinition instances.
    """
    qs = CustomFieldDefinition.unscoped.filter(
        tenant=tenant,
        module=module,
        is_active=True,
    )

    if user_role is not None:
        # Include fields with no role restrictions (visible to all)
        # plus fields explicitly visible to the user's role.
        from django.db.models import Q

        qs = qs.filter(
            Q(visible_to_roles__isnull=True)
            | Q(visible_to_roles=user_role)
        ).distinct()

    return qs.order_by("order")


def _build_value_kwargs(field_def, value):
    """
    Determine the appropriate typed column and build the kwargs dict for
    CustomFieldValue creation/update.

    Resets all typed columns and sets only the relevant one based on field_type.
    """
    kwargs = {
        "value_text": None,
        "value_number": None,
        "value_date": None,
        "value_bool": None,
    }

    if value is None:
        return kwargs

    field_type = field_def.field_type

    if field_type in (FieldType.TEXT, FieldType.TEXTAREA, FieldType.FILE):
        kwargs["value_text"] = str(value)

    elif field_type == FieldType.SELECT:
        kwargs["value_text"] = str(value)

    elif field_type == FieldType.MULTISELECT:
        # Store as JSON string for text-based searching.
        if isinstance(value, list):
            kwargs["value_text"] = json.dumps(value)
        else:
            kwargs["value_text"] = str(value)

    elif field_type == FieldType.NUMBER:
        try:
            kwargs["value_number"] = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError):
            kwargs["value_text"] = str(value)

    elif field_type == FieldType.DATE:
        if isinstance(value, (date, datetime)):
            kwargs["value_date"] = value
        elif isinstance(value, str):
            parsed = parse_datetime(value)
            if parsed:
                kwargs["value_date"] = parsed
            else:
                kwargs["value_text"] = value
        else:
            kwargs["value_text"] = str(value)

    elif field_type == FieldType.CHECKBOX:
        if isinstance(value, bool):
            kwargs["value_bool"] = value
        else:
            kwargs["value_bool"] = bool(value)

    return kwargs
