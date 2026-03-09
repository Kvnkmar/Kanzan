"""
Billing service layer.

Provides ``PlanLimitChecker`` which validates whether a tenant's current
usage allows additional resource creation according to their plan limits.
"""

import logging

from django.core.exceptions import PermissionDenied

logger = logging.getLogger(__name__)


class PlanLimitChecker:
    """
    Validates tenant resource creation against plan limits.

    Usage::

        checker = PlanLimitChecker(tenant)
        checker.check_can_create_contact()   # raises PermissionDenied if over limit

    A ``None`` limit on the plan means *unlimited*.
    """

    def __init__(self, tenant):
        self.tenant = tenant
        self._load()

    def _load(self):
        """Load the plan and current usage for the tenant."""
        try:
            self.subscription = self.tenant.subscription
            self.plan = self.subscription.plan
        except self.tenant.__class__.subscription.RelatedObjectDoesNotExist:
            # No subscription -- fall back to the free plan.
            from apps.billing.models import Plan

            self.subscription = None
            self.plan = Plan.objects.filter(tier=Plan.Tier.FREE).first()

        try:
            self.usage = self.tenant.usage
        except self.tenant.__class__.usage.RelatedObjectDoesNotExist:
            self.usage = None

    def _check_limit(self, limit_value, current_value, resource_name):
        """
        Raise ``PermissionDenied`` when *current_value* meets or exceeds
        *limit_value*.  A ``None`` limit means unlimited.
        """
        if limit_value is None:
            return  # Unlimited.
        if current_value >= limit_value:
            raise PermissionDenied(
                f"Plan limit reached: your {self.plan.name} plan allows a maximum "
                f"of {limit_value} {resource_name}. Please upgrade to continue."
            )

    def check_can_create_contact(self):
        """Verify the tenant has not exceeded their contact limit."""
        if self.plan is None:
            raise PermissionDenied("No active plan found. Please subscribe to a plan.")
        current = self.usage.contacts_count if self.usage else 0
        self._check_limit(self.plan.max_contacts, current, "contacts")

    def check_can_create_ticket(self):
        """Verify the tenant has not exceeded their monthly ticket limit."""
        if self.plan is None:
            raise PermissionDenied("No active plan found. Please subscribe to a plan.")
        current = self.usage.tickets_created if self.usage else 0
        self._check_limit(self.plan.max_tickets_per_month, current, "tickets per month")

    def check_can_add_user(self):
        """Verify the tenant has not exceeded their user (member) limit."""
        if self.plan is None:
            raise PermissionDenied("No active plan found. Please subscribe to a plan.")
        from apps.accounts.models import TenantMembership

        active_members = TenantMembership.objects.filter(
            tenant=self.tenant,
            is_active=True,
        ).count()
        self._check_limit(self.plan.max_users, active_members, "users")

    def check_can_add_custom_field(self, module):
        """
        Verify the tenant has not exceeded their custom field limit.

        Parameters
        ----------
        module : str
            The module/resource type (e.g. ``"contact"``, ``"ticket"``)
            to which the custom field belongs.  Used for the error message.
        """
        if self.plan is None:
            raise PermissionDenied("No active plan found. Please subscribe to a plan.")

        # Count existing custom fields across all modules for this tenant.
        # Import lazily to avoid circular imports if custom fields live in
        # another app.
        try:
            from apps.custom_fields.models import CustomFieldDefinition

            current_count = CustomFieldDefinition.objects.filter(
                tenant=self.tenant,
            ).count()
        except ImportError:
            # Custom fields app not installed yet; nothing to check.
            current_count = 0

        self._check_limit(
            self.plan.max_custom_fields,
            current_count,
            f"custom fields (adding to {module})",
        )

    def check_storage(self, additional_mb):
        """
        Verify the tenant will not exceed their storage limit after adding
        *additional_mb* megabytes.
        """
        if self.plan is None:
            raise PermissionDenied("No active plan found. Please subscribe to a plan.")
        current = self.usage.storage_used_mb if self.usage else 0
        projected = current + additional_mb
        if self.plan.max_storage_mb is not None and projected > self.plan.max_storage_mb:
            raise PermissionDenied(
                f"Storage limit reached: your {self.plan.name} plan allows "
                f"{self.plan.max_storage_mb} MB of storage. Current usage is "
                f"{current} MB, and you are attempting to add {additional_mb} MB. "
                f"Please upgrade your plan."
            )
