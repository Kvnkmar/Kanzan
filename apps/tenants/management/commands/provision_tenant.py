"""
Management command to provision a new tenant.

Usage::

    python manage.py provision_tenant --name "Acme Corp" --slug acme
    python manage.py provision_tenant --name "Acme Corp" --slug acme --domain crm.acme.com
"""

from django.core.management.base import BaseCommand, CommandError

from apps.tenants.models import Tenant


class Command(BaseCommand):
    help = "Provision a new tenant with default settings and RBAC roles."

    def add_arguments(self, parser):
        parser.add_argument(
            "--name",
            required=True,
            help="Human-readable name for the tenant (e.g. 'Acme Corp').",
        )
        parser.add_argument(
            "--slug",
            required=True,
            help="Unique subdomain slug (e.g. 'acme' for acme.localhost).",
        )
        parser.add_argument(
            "--domain",
            default=None,
            help="Optional custom domain (e.g. 'crm.acme.com').",
        )

    def handle(self, *args, **options):
        name = options["name"]
        slug = options["slug"]
        domain = options["domain"]

        if Tenant.objects.filter(slug=slug).exists():
            raise CommandError(f"A tenant with slug '{slug}' already exists.")

        if domain and Tenant.objects.filter(domain=domain).exists():
            raise CommandError(f"A tenant with domain '{domain}' already exists.")

        tenant = Tenant.objects.create(
            name=name,
            slug=slug,
            domain=domain,
        )

        self.stdout.write(
            self.style.SUCCESS(
                f"Successfully provisioned tenant '{tenant.name}' "
                f"(slug={tenant.slug}, id={tenant.id})."
            )
        )

        # The post_save signal auto-creates TenantSettings and default roles.
        if hasattr(tenant, "settings"):
            self.stdout.write(f"  - TenantSettings created (auth={tenant.settings.auth_method})")

        self.stdout.write(
            self.style.NOTICE(
                f"Tenant accessible at: {tenant.slug}.localhost"
            )
        )
        if tenant.domain:
            self.stdout.write(
                self.style.NOTICE(
                    f"Custom domain: {tenant.domain}"
                )
            )
