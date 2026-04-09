"""
Tests for TicketPresenceConsumer (WebSocket ticket presence tracking).

Validates that:
- Agent connecting joins the group and receives agent_joined broadcast
- Agent disconnecting broadcasts agent_left to remaining members
- Unauthenticated WebSocket connection is rejected (close code 4001)
- Cross-tenant access is rejected (agent from tenant A cannot join tenant B ticket)
"""

import pytest
from channels.db import database_sync_to_async
from channels.testing import WebsocketCommunicator

from conftest import (
    MembershipFactory,
    TenantFactory,
    TicketFactory,
    TicketStatusFactory,
    UserFactory,
)
from main.context import set_current_tenant

from apps.accounts.models import Role
from apps.tickets.consumers import TicketPresenceConsumer


def _make_application():
    """Build a minimal ASGI application for testing the consumer."""
    from channels.auth import AuthMiddlewareStack
    from channels.routing import URLRouter
    from django.urls import re_path

    from apps.tickets.consumers import TicketPresenceConsumer

    return AuthMiddlewareStack(
        URLRouter([
            re_path(
                r"ws/tickets/(?P<ticket_id>[a-f0-9-]+)/presence/$",
                TicketPresenceConsumer.as_asgi(),
            ),
        ])
    )


def _make_communicator(ticket, user, tenant):
    """Build a WebsocketCommunicator with auth scope set."""
    app = _make_application()
    path = f"ws/tickets/{ticket.pk}/presence/"
    communicator = WebsocketCommunicator(app, path)
    communicator.scope["user"] = user
    communicator.scope["tenant"] = tenant
    communicator.scope["url_route"] = {
        "kwargs": {"ticket_id": str(ticket.pk)},
    }
    return communicator


@pytest.fixture
def setup(tenant, admin_user, agent_role):
    """Common setup: tenant, status, ticket, two agents."""
    set_current_tenant(tenant)
    status = TicketStatusFactory(
        tenant=tenant, name="Open", slug="open", is_default=True,
    )
    ticket = TicketFactory(
        tenant=tenant, status=status, created_by=admin_user,
    )

    agent1 = UserFactory()
    MembershipFactory(user=agent1, tenant=tenant, role=agent_role)

    agent2 = UserFactory()
    MembershipFactory(user=agent2, tenant=tenant, role=agent_role)

    return ticket, agent1, agent2


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
class TestAgentJoinsGroup:
    """Agent connecting joins the group and receives agent_joined broadcast."""

    async def test_connect_broadcasts_agent_joined(self, setup, tenant, settings):
        settings.CHANNEL_LAYERS = {
            "default": {
                "BACKEND": "channels.layers.InMemoryChannelLayer",
            },
        }
        ticket, agent1, _ = setup

        comm = _make_communicator(ticket, agent1, tenant)
        connected, _ = await comm.connect()
        assert connected

        # Should receive the agent_joined message (own join broadcast)
        msg = await comm.receive_json_from(timeout=3)
        assert msg["type"] == "agent_joined"
        assert msg["user_id"] == str(agent1.pk)
        assert msg["display_name"]
        assert msg["avatar_initials"]

        await comm.disconnect()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
class TestAgentLeaveBroadcast:
    """Agent disconnecting broadcasts agent_left to remaining members."""

    async def test_disconnect_broadcasts_agent_left(self, setup, tenant, settings):
        settings.CHANNEL_LAYERS = {
            "default": {
                "BACKEND": "channels.layers.InMemoryChannelLayer",
            },
        }
        ticket, agent1, agent2 = setup

        # Agent 1 connects
        comm1 = _make_communicator(ticket, agent1, tenant)
        await comm1.connect()
        await comm1.receive_json_from(timeout=3)  # own join

        # Agent 2 connects
        comm2 = _make_communicator(ticket, agent2, tenant)
        await comm2.connect()
        await comm2.receive_json_from(timeout=3)  # own join

        # Agent 1 receives agent2's join
        msg = await comm1.receive_json_from(timeout=3)
        assert msg["type"] == "agent_joined"
        assert msg["user_id"] == str(agent2.pk)

        # Agent 2 disconnects
        await comm2.disconnect()

        # Agent 1 should receive agent_left
        msg = await comm1.receive_json_from(timeout=3)
        assert msg["type"] == "agent_left"
        assert msg["user_id"] == str(agent2.pk)

        await comm1.disconnect()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
class TestUnauthenticatedRejected:
    """Unauthenticated WebSocket connection is rejected with code 4001."""

    async def test_anon_rejected(self, setup, tenant, settings):
        settings.CHANNEL_LAYERS = {
            "default": {
                "BACKEND": "channels.layers.InMemoryChannelLayer",
            },
        }
        ticket, _, _ = setup

        from django.contrib.auth.models import AnonymousUser

        comm = _make_communicator(ticket, AnonymousUser(), tenant)
        connected, code = await comm.connect()
        assert not connected
        assert code == 4001


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
class TestCrossTenantRejected:
    """Agent from tenant A cannot join ticket from tenant B."""

    async def test_cross_tenant_rejected(self, setup, tenant, settings):
        settings.CHANNEL_LAYERS = {
            "default": {
                "BACKEND": "channels.layers.InMemoryChannelLayer",
            },
        }
        ticket, _, _ = setup  # ticket belongs to `tenant`

        # Create a user who is a member of a DIFFERENT tenant
        tenant_b = await database_sync_to_async(TenantFactory)(slug="other-ws")
        other_user = await database_sync_to_async(UserFactory)()
        other_role = await database_sync_to_async(
            lambda: Role.unscoped.get(tenant=tenant_b, slug="agent")
        )()
        await database_sync_to_async(MembershipFactory)(
            user=other_user, tenant=tenant_b, role=other_role,
        )

        # Try to connect to tenant A's ticket with tenant B context
        comm = _make_communicator(ticket, other_user, tenant_b)
        connected, code = await comm.connect()
        assert not connected
        assert code == 4003  # ticket doesn't belong to tenant_b
