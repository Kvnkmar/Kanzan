"""
Root conftest.py — shared fixtures and factories for the entire test suite.
"""

import uuid

import factory
import pytest

from main.context import clear_current_tenant, set_current_tenant


# ── Celery eager mode ────────────────────────────────────────────────
@pytest.fixture(autouse=True)
def celery_eager(settings):
    settings.CELERY_TASK_ALWAYS_EAGER = True
    settings.CELERY_TASK_EAGER_PROPAGATES = True


# ── Factories ────────────────────────────────────────────────────────

class TenantFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = "tenants.Tenant"

    name = factory.Sequence(lambda n: f"Tenant {n}")
    slug = factory.Sequence(lambda n: f"tenant-{n}")
    is_active = True


class UserFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = "accounts.User"
        skip_postgeneration_save = True

    email = factory.Sequence(lambda n: f"user{n}@test.com")
    first_name = factory.Faker("first_name")
    last_name = factory.Faker("last_name")
    is_active = True

    @classmethod
    def _after_postgeneration(cls, instance, create, results=None):
        if create:
            instance.set_password("testpass123")
            instance.save()


class RoleFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = "accounts.Role"

    name = factory.Sequence(lambda n: f"Role {n}")
    slug = factory.Sequence(lambda n: f"role-{n}")
    tenant = factory.SubFactory(TenantFactory)
    hierarchy_level = 30
    is_system = True

    class Params:
        admin = factory.Trait(name="Admin", slug="admin", hierarchy_level=10)
        manager = factory.Trait(name="Manager", slug="manager", hierarchy_level=20)
        agent = factory.Trait(name="Agent", slug="agent", hierarchy_level=30)
        viewer = factory.Trait(name="Viewer", slug="viewer", hierarchy_level=40)


class MembershipFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = "accounts.TenantMembership"

    user = factory.SubFactory(UserFactory)
    tenant = factory.SubFactory(TenantFactory)
    role = factory.SubFactory(RoleFactory, tenant=factory.SelfAttribute("..tenant"))
    is_active = True


class TicketStatusFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = "tickets.TicketStatus"

    name = factory.Sequence(lambda n: f"Status {n}")
    slug = factory.Sequence(lambda n: f"status-{n}")
    tenant = factory.SubFactory(TenantFactory)
    order = factory.Sequence(lambda n: n)
    is_default = False
    is_closed = False


class QueueFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = "tickets.Queue"

    name = factory.Sequence(lambda n: f"Queue {n}")
    tenant = factory.SubFactory(TenantFactory)


class TicketFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = "tickets.Ticket"

    subject = factory.Faker("sentence", nb_words=5)
    description = factory.Faker("paragraph")
    tenant = factory.SubFactory(TenantFactory)
    status = factory.SubFactory(TicketStatusFactory, tenant=factory.SelfAttribute("..tenant"))
    priority = "medium"
    created_by = factory.SubFactory(UserFactory)
    number = factory.Sequence(lambda n: n + 1)


class CompanyFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = "contacts.Company"

    name = factory.Sequence(lambda n: f"Company {n}")
    tenant = factory.SubFactory(TenantFactory)


class ContactFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = "contacts.Contact"

    first_name = factory.Faker("first_name")
    last_name = factory.Faker("last_name")
    email = factory.Sequence(lambda n: f"contact{n}@example.com")
    tenant = factory.SubFactory(TenantFactory)


class ContactGroupFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = "contacts.ContactGroup"

    name = factory.Sequence(lambda n: f"Group {n}")
    tenant = factory.SubFactory(TenantFactory)


class NotificationFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = "notifications.Notification"

    tenant = factory.SubFactory(TenantFactory)
    recipient = factory.SubFactory(UserFactory)
    type = "ticket_assigned"
    title = factory.Faker("sentence", nb_words=4)
    body = factory.Faker("paragraph")


class PlanFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = "billing.Plan"

    name = factory.Sequence(lambda n: f"Plan {n}")
    tier = "free"
    stripe_product_id = factory.Sequence(lambda n: f"prod_{n}")
    max_users = 3
    max_contacts = 500
    max_tickets_per_month = 100


class SubscriptionFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = "billing.Subscription"

    tenant = factory.SubFactory(TenantFactory)
    plan = factory.SubFactory(PlanFactory)
    stripe_customer_id = factory.Sequence(lambda n: f"cus_{n}")
    status = "active"


class CustomFieldDefinitionFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = "custom_fields.CustomFieldDefinition"

    tenant = factory.SubFactory(TenantFactory)
    module = "ticket"
    name = factory.Sequence(lambda n: f"Custom Field {n}")
    slug = factory.Sequence(lambda n: f"custom-field-{n}")
    field_type = "text"


class ReminderFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = "crm.Reminder"

    subject = factory.Faker("sentence", nb_words=4)
    notes = factory.Faker("paragraph")
    scheduled_at = factory.LazyFunction(lambda: __import__("django.utils.timezone", fromlist=["now"]).now())
    priority = "medium"
    tenant = factory.SubFactory(TenantFactory)
    created_by = factory.SubFactory(UserFactory)


class InboundEmailFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = "inbound_email.InboundEmail"

    message_id = factory.LazyFunction(lambda: f"{uuid.uuid4()}@test.com")
    sender_email = factory.Sequence(lambda n: f"sender{n}@example.com")
    recipient_email = "support+demo@kanzan.io"
    subject = factory.Faker("sentence", nb_words=5)
    body_text = factory.Faker("paragraph")
    status = "pending"


# ── Fixtures ─────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def free_plan(db):
    """Ensure a free plan exists for PlanLimitChecker fallback."""
    from apps.billing.models import Plan
    plan, _ = Plan.objects.get_or_create(
        tier="free",
        defaults=dict(
            name="Free",
            stripe_product_id="prod_free_test",
            max_users=100,
            max_contacts=10000,
            max_tickets_per_month=10000,
        ),
    )
    return plan


@pytest.fixture
def tenant(db, free_plan):
    return TenantFactory()


@pytest.fixture
def tenant_b(db):
    """A second tenant for cross-tenant isolation tests."""
    return TenantFactory(name="Tenant B", slug="tenant-b")


@pytest.fixture
def admin_role(tenant):
    """Get the auto-created Admin role for the tenant (from signals)."""
    from apps.accounts.models import Role
    return Role.unscoped.get(tenant=tenant, slug="admin")


@pytest.fixture
def manager_role(tenant):
    from apps.accounts.models import Role
    return Role.unscoped.get(tenant=tenant, slug="manager")


@pytest.fixture
def agent_role(tenant):
    from apps.accounts.models import Role
    return Role.unscoped.get(tenant=tenant, slug="agent")


@pytest.fixture
def viewer_role(tenant):
    from apps.accounts.models import Role
    return Role.unscoped.get(tenant=tenant, slug="viewer")


@pytest.fixture
def admin_user(tenant, admin_role):
    user = UserFactory()
    MembershipFactory(user=user, tenant=tenant, role=admin_role)
    return user


@pytest.fixture
def manager_user(tenant, manager_role):
    user = UserFactory()
    MembershipFactory(user=user, tenant=tenant, role=manager_role)
    return user


@pytest.fixture
def agent_user(tenant, agent_role):
    user = UserFactory()
    MembershipFactory(user=user, tenant=tenant, role=agent_role)
    return user


@pytest.fixture
def viewer_user(tenant, viewer_role):
    user = UserFactory()
    MembershipFactory(user=user, tenant=tenant, role=viewer_role)
    return user


@pytest.fixture
def default_status(tenant):
    set_current_tenant(tenant)
    status = TicketStatusFactory(tenant=tenant, is_default=True, name="Open", slug="open")
    clear_current_tenant()
    return status


@pytest.fixture
def closed_status(tenant):
    set_current_tenant(tenant)
    status = TicketStatusFactory(tenant=tenant, is_closed=True, name="Closed", slug="closed")
    clear_current_tenant()
    return status


def make_api_client(user, tenant):
    """Create an APIClient authenticated as user with tenant subdomain."""
    from rest_framework.test import APIClient
    client = APIClient()
    client.force_authenticate(user=user)
    client.defaults["HTTP_HOST"] = f"{tenant.slug}.localhost:8001"
    return client


@pytest.fixture
def admin_client(admin_user, tenant):
    return make_api_client(admin_user, tenant)


@pytest.fixture
def manager_client(manager_user, tenant):
    return make_api_client(manager_user, tenant)


@pytest.fixture
def agent_client(agent_user, tenant):
    return make_api_client(agent_user, tenant)


@pytest.fixture
def viewer_client(viewer_user, tenant):
    return make_api_client(viewer_user, tenant)


@pytest.fixture
def anon_client(tenant):
    from rest_framework.test import APIClient
    client = APIClient()
    client.defaults["HTTP_HOST"] = f"{tenant.slug}.localhost:8001"
    return client


@pytest.fixture(autouse=True)
def clear_tenant_context():
    """Ensure tenant context is clean before/after each test."""
    clear_current_tenant()
    yield
    clear_current_tenant()
