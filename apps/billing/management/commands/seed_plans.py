"""
Management command to seed the default billing plans.

Usage::

    python manage.py seed_plans

Creates (or updates) the Free, Pro, and Enterprise plans with their
respective limits and feature flags.  Safe to run multiple times -- it
uses ``update_or_create`` keyed on the ``tier`` field.
"""

from decimal import Decimal

from django.core.management.base import BaseCommand

from apps.billing.models import Plan


PLANS = [
    {
        "tier": Plan.Tier.FREE,
        "name": "Free",
        "stripe_product_id": "prod_free",
        "stripe_price_monthly": "",
        "stripe_price_yearly": "",
        "price_monthly": Decimal("0.00"),
        "price_yearly": Decimal("0.00"),
        "is_active": True,
        # Limits
        "max_users": 3,
        "max_contacts": 500,
        "max_tickets_per_month": 100,
        "max_storage_mb": 1024,       # 1 GB
        "max_custom_fields": 5,
        # Feature flags
        "has_api_access": False,
        "has_realtime": False,
        "has_custom_roles": False,
        "has_sso": False,
        "has_sla_management": False,
        "audit_retention_days": 30,
    },
    {
        "tier": Plan.Tier.PRO,
        "name": "Pro",
        "stripe_product_id": "prod_pro",
        "stripe_price_monthly": "price_pro_monthly",
        "stripe_price_yearly": "price_pro_yearly",
        "price_monthly": Decimal("29.00"),
        "price_yearly": Decimal("290.00"),
        "is_active": True,
        # Limits
        "max_users": 25,
        "max_contacts": 10000,
        "max_tickets_per_month": 5000,
        "max_storage_mb": 25600,      # 25 GB
        "max_custom_fields": 50,
        # Feature flags
        "has_api_access": True,
        "has_realtime": True,
        "has_custom_roles": True,
        "has_sso": False,
        "has_sla_management": True,
        "audit_retention_days": 365,
    },
    {
        "tier": Plan.Tier.ENTERPRISE,
        "name": "Enterprise",
        "stripe_product_id": "prod_enterprise",
        "stripe_price_monthly": "price_enterprise_monthly",
        "stripe_price_yearly": "price_enterprise_yearly",
        "price_monthly": Decimal("99.00"),
        "price_yearly": Decimal("990.00"),
        "is_active": True,
        # Limits (None = unlimited)
        "max_users": None,
        "max_contacts": None,
        "max_tickets_per_month": None,
        "max_storage_mb": None,
        "max_custom_fields": None,
        # Feature flags
        "has_api_access": True,
        "has_realtime": True,
        "has_custom_roles": True,
        "has_sso": True,
        "has_sla_management": True,
        "audit_retention_days": None,  # Unlimited retention
    },
]


class Command(BaseCommand):
    help = "Seed the default Free, Pro, and Enterprise billing plans."

    def handle(self, *args, **options):
        for plan_data in PLANS:
            tier = plan_data.pop("tier")
            plan, created = Plan.objects.update_or_create(
                tier=tier,
                defaults=plan_data,
            )
            # Restore tier for potential re-runs within the same process.
            plan_data["tier"] = tier

            action = "Created" if created else "Updated"
            self.stdout.write(
                self.style.SUCCESS(f"  {action} plan: {plan.name} ({plan.tier})")
            )

        self.stdout.write(self.style.SUCCESS("\nAll plans seeded successfully."))
