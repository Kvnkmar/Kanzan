"""
DRF serializers for the custom_fields app.

Provides serializers for CustomFieldDefinition CRUD operations, including
a validated create serializer with checks for select-type options and
validation rules, plus a read-only serializer for CustomFieldValue.
"""

from rest_framework import serializers

from apps.custom_fields.models import (
    CustomFieldDefinition,
    CustomFieldValue,
    FieldType,
    ModuleType,
)


# ---------------------------------------------------------------------------
# CustomFieldDefinition -- read / list
# ---------------------------------------------------------------------------


class CustomFieldDefinitionSerializer(serializers.ModelSerializer):
    """Full CRUD serializer for custom field definitions."""

    module_display = serializers.CharField(
        source="get_module_display", read_only=True
    )
    field_type_display = serializers.CharField(
        source="get_field_type_display", read_only=True
    )

    class Meta:
        model = CustomFieldDefinition
        fields = [
            "id",
            "module",
            "module_display",
            "name",
            "slug",
            "field_type",
            "field_type_display",
            "options",
            "is_required",
            "default_value",
            "validation_rules",
            "order",
            "visible_to_roles",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


# ---------------------------------------------------------------------------
# CustomFieldDefinition -- create / update
# ---------------------------------------------------------------------------


class CustomFieldDefinitionCreateSerializer(serializers.ModelSerializer):
    """
    Create/update serializer for custom field definitions.

    Validates:
        - Select/multiselect fields must have at least one option with
          both ``value`` and ``label`` keys.
        - Validation rules contain only known keys.
    """

    class Meta:
        model = CustomFieldDefinition
        fields = [
            "id",
            "module",
            "name",
            "slug",
            "field_type",
            "options",
            "is_required",
            "default_value",
            "validation_rules",
            "order",
            "visible_to_roles",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    ALLOWED_RULE_KEYS = {
        "min_length",
        "max_length",
        "min",
        "max",
        "regex",
    }

    def validate(self, attrs):
        """Cross-field validation for options and validation_rules."""
        field_type = attrs.get("field_type", getattr(self.instance, "field_type", None))
        options = attrs.get("options", getattr(self.instance, "options", []))
        rules = attrs.get("validation_rules", getattr(self.instance, "validation_rules", {}))

        # Validate options for select types.
        if field_type in (FieldType.SELECT, FieldType.MULTISELECT):
            self._validate_select_options(options)

        # Validate validation_rules keys.
        if rules and isinstance(rules, dict):
            self._validate_rule_keys(rules)

        return attrs

    def _validate_select_options(self, options):
        """Ensure select/multiselect options have the correct structure."""
        if not options or not isinstance(options, list):
            raise serializers.ValidationError(
                {"options": "Select and multi-select fields must have at least one option."}
            )

        for idx, option in enumerate(options):
            if not isinstance(option, dict):
                raise serializers.ValidationError(
                    {"options": f"Option at index {idx} must be a JSON object."}
                )
            if "value" not in option or "label" not in option:
                raise serializers.ValidationError(
                    {"options": f"Option at index {idx} must have 'value' and 'label' keys."}
                )

    def _validate_rule_keys(self, rules):
        """Ensure validation_rules only contains known keys."""
        unknown_keys = set(rules.keys()) - self.ALLOWED_RULE_KEYS
        if unknown_keys:
            raise serializers.ValidationError(
                {
                    "validation_rules": (
                        f"Unknown validation rule(s): {', '.join(sorted(unknown_keys))}. "
                        f"Allowed: {', '.join(sorted(self.ALLOWED_RULE_KEYS))}."
                    )
                }
            )


# ---------------------------------------------------------------------------
# CustomFieldValue -- read-only
# ---------------------------------------------------------------------------


class CustomFieldValueSerializer(serializers.ModelSerializer):
    """Read-only serializer for custom field values."""

    field_name = serializers.CharField(source="field.name", read_only=True)
    field_slug = serializers.SlugField(source="field.slug", read_only=True)
    field_type = serializers.CharField(source="field.field_type", read_only=True)
    display_value = serializers.SerializerMethodField()

    class Meta:
        model = CustomFieldValue
        fields = [
            "id",
            "field",
            "field_name",
            "field_slug",
            "field_type",
            "content_type",
            "object_id",
            "value_text",
            "value_number",
            "value_date",
            "value_bool",
            "display_value",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_display_value(self, obj):
        """Return the value from the appropriate typed column."""
        value = obj.display_value
        if isinstance(value, bool):
            return value
        if value is not None:
            return str(value)
        return None
