"""
Validation logic for custom field values.

Provides the CustomFieldValidator class that validates a value against
the field type and validation rules defined in a CustomFieldDefinition.
"""

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.utils.dateparse import parse_date, parse_datetime


class CustomFieldValidator:
    """
    Validates a value against a CustomFieldDefinition's type and rules.

    Usage::

        validator = CustomFieldValidator(field_definition)
        validator.validate(value)  # raises ValidationError on failure
    """

    def __init__(self, field_definition):
        self.field = field_definition
        self.rules = field_definition.validation_rules or {}
        self.options = field_definition.options or []

    def validate(self, value):
        """
        Validate the given value against the field definition.

        Checks:
            1. Required: value must not be empty if the field is required.
            2. Type-specific: delegates to the appropriate type validator.

        Raises:
            ValidationError with a descriptive message on failure.
        """
        # Required check.
        if self.field.is_required and self._is_empty(value):
            raise ValidationError(
                f"Field '{self.field.name}' is required.",
                code="required",
            )

        # Skip further validation if value is empty and not required.
        if self._is_empty(value):
            return

        # Dispatch to type-specific validator.
        type_validators = {
            "text": self._validate_text,
            "textarea": self._validate_text,
            "number": self._validate_number,
            "date": self._validate_date,
            "select": self._validate_select,
            "multiselect": self._validate_multiselect,
            "checkbox": self._validate_checkbox,
            "file": self._validate_file,
        }

        validator_fn = type_validators.get(self.field.field_type)
        if validator_fn is not None:
            validator_fn(value)

    # ------------------------------------------------------------------
    # Type validators
    # ------------------------------------------------------------------

    def _validate_text(self, value):
        """Validate text and textarea fields."""
        if not isinstance(value, str):
            raise ValidationError(
                f"Field '{self.field.name}' expects a text value.",
                code="invalid_type",
            )

        min_length = self.rules.get("min_length")
        max_length = self.rules.get("max_length")
        pattern = self.rules.get("regex")

        if min_length is not None and len(value) < int(min_length):
            raise ValidationError(
                f"Field '{self.field.name}' must be at least {min_length} characters.",
                code="min_length",
            )

        if max_length is not None and len(value) > int(max_length):
            raise ValidationError(
                f"Field '{self.field.name}' must be at most {max_length} characters.",
                code="max_length",
            )

        if pattern:
            try:
                if not re.match(pattern, value):
                    raise ValidationError(
                        f"Field '{self.field.name}' does not match the required pattern.",
                        code="regex",
                    )
            except re.error:
                raise ValidationError(
                    f"Field '{self.field.name}' has an invalid regex pattern in its definition.",
                    code="invalid_regex",
                )

    def _validate_number(self, value):
        """Validate number fields."""
        try:
            num = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError):
            raise ValidationError(
                f"Field '{self.field.name}' expects a numeric value.",
                code="invalid_type",
            )

        min_val = self.rules.get("min")
        max_val = self.rules.get("max")

        if min_val is not None and num < Decimal(str(min_val)):
            raise ValidationError(
                f"Field '{self.field.name}' must be at least {min_val}.",
                code="min_value",
            )

        if max_val is not None and num > Decimal(str(max_val)):
            raise ValidationError(
                f"Field '{self.field.name}' must be at most {max_val}.",
                code="max_value",
            )

    def _validate_date(self, value):
        """Validate date fields."""
        if isinstance(value, (date, datetime)):
            return

        if not isinstance(value, str):
            raise ValidationError(
                f"Field '{self.field.name}' expects a date value.",
                code="invalid_type",
            )

        parsed = parse_datetime(value) or parse_date(value)
        if parsed is None:
            raise ValidationError(
                f"Field '{self.field.name}' has an invalid date format. "
                "Use ISO 8601 format (YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS).",
                code="invalid_date",
            )

    def _validate_select(self, value):
        """Validate single-select fields."""
        valid_values = [opt.get("value") for opt in self.options if isinstance(opt, dict)]

        if value not in valid_values:
            raise ValidationError(
                f"Field '{self.field.name}': '{value}' is not a valid option. "
                f"Valid options: {', '.join(str(v) for v in valid_values)}.",
                code="invalid_choice",
            )

    def _validate_multiselect(self, value):
        """Validate multi-select fields."""
        if not isinstance(value, list):
            raise ValidationError(
                f"Field '{self.field.name}' expects a list of values for multi-select.",
                code="invalid_type",
            )

        valid_values = [opt.get("value") for opt in self.options if isinstance(opt, dict)]

        for item in value:
            if item not in valid_values:
                raise ValidationError(
                    f"Field '{self.field.name}': '{item}' is not a valid option. "
                    f"Valid options: {', '.join(str(v) for v in valid_values)}.",
                    code="invalid_choice",
                )

    def _validate_checkbox(self, value):
        """Validate checkbox (boolean) fields."""
        if not isinstance(value, bool):
            raise ValidationError(
                f"Field '{self.field.name}' expects a boolean value (true/false).",
                code="invalid_type",
            )

    def _validate_file(self, value):
        """
        Validate file fields.

        Only checks that the value is present (non-empty string).
        Actual file validation (size, type) is handled by the attachments app.
        """
        if not isinstance(value, str) or not value.strip():
            raise ValidationError(
                f"Field '{self.field.name}' requires a file reference.",
                code="required",
            )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _is_empty(value):
        """Check whether a value should be considered empty."""
        if value is None:
            return True
        if isinstance(value, str) and not value.strip():
            return True
        if isinstance(value, list) and len(value) == 0:
            return True
        return False
