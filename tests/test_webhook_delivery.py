"""
Regression tests for ticket webhook delivery.

Targets:
    apps/tickets/webhook_service.py :: deliver_webhook, fire_webhooks
    apps/tickets/tasks.py           :: deliver_webhook_task

All outbound HTTP (requests.post) is mocked — NO real network calls.

Covers:
    (a) On a 2xx response the X-Webhook-Signature header is a correct
        HMAC-SHA256 of the request body using the webhook secret.
    (b) A non-2xx response increments failure_count.
    (c) Reaching MAX_FAILURE_COUNT auto-disables the webhook.
    (d) A requests exception is handled gracefully (no crash, failure recorded).
    (e) fire_webhooks only dispatches to ACTIVE webhooks subscribed to the event.
"""

import hashlib
import hmac
import json
from unittest import mock

import pytest
import requests

from main.context import tenant_context

from apps.tickets.models import Webhook
from apps.tickets.webhook_service import (
    MAX_FAILURE_COUNT,
    deliver_webhook,
    fire_webhooks,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_webhook(tenant, **overrides):
    """Create a Webhook row inside the tenant context."""
    defaults = dict(
        name="Test Hook",
        url="https://example.test/hook",
        secret="topsecret",
        events=[Webhook.EventType.TICKET_CREATED],
        is_active=True,
    )
    defaults.update(overrides)
    with tenant_context(tenant):
        return Webhook.objects.create(tenant=tenant, **defaults)


def _fake_response(status_code):
    """Build a stand-in requests.Response with the given status code."""
    resp = mock.Mock(spec=requests.Response)
    resp.status_code = status_code
    return resp


def _expected_signature(secret, payload):
    """Recompute the signature exactly the way deliver_webhook builds it."""
    body = json.dumps(payload, default=str)
    digest = hmac.new(
        secret.encode("utf-8"),
        body.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"sha256={digest}"


# ---------------------------------------------------------------------------
# (a) HMAC signature correctness
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestWebhookSignature:
    def test_signature_is_correct_hmac_of_body(self, tenant):
        webhook = _make_webhook(tenant, secret="mysecret")
        payload = {"event": "ticket.created", "data": {"id": 1, "n": 7}}

        with mock.patch(
            "apps.tickets.webhook_service.requests.post",
            return_value=_fake_response(200),
        ) as mock_post:
            success, status = deliver_webhook(webhook, payload)

        assert success is True
        assert status == 200

        # The body posted must match what we sign over.
        _, kwargs = mock_post.call_args
        sent_body = kwargs["data"]
        sent_headers = kwargs["headers"]

        assert sent_body == json.dumps(payload, default=str)

        sig = sent_headers["X-Webhook-Signature"]
        assert sig == _expected_signature("mysecret", payload)

        # Verify it independently against the actual bytes that were sent.
        expected = hmac.new(
            b"mysecret", sent_body.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        assert sig == f"sha256={expected}"

    def test_no_signature_header_when_no_secret(self, tenant):
        webhook = _make_webhook(tenant, secret="")
        payload = {"event": "ticket.created", "data": {}}

        with mock.patch(
            "apps.tickets.webhook_service.requests.post",
            return_value=_fake_response(200),
        ) as mock_post:
            success, _ = deliver_webhook(webhook, payload)

        assert success is True
        _, kwargs = mock_post.call_args
        assert "X-Webhook-Signature" not in kwargs["headers"]

    def test_success_resets_failure_count(self, tenant):
        webhook = _make_webhook(tenant, failure_count=4)
        payload = {"event": "ticket.created"}

        with mock.patch(
            "apps.tickets.webhook_service.requests.post",
            return_value=_fake_response(204),
        ):
            success, status = deliver_webhook(webhook, payload)

        assert success is True
        assert status == 204
        webhook.refresh_from_db()
        assert webhook.failure_count == 0
        assert webhook.last_status_code == 204
        assert webhook.last_triggered_at is not None


# ---------------------------------------------------------------------------
# (b) non-2xx increments failure_count
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestWebhookFailureCount:
    def test_non_2xx_increments_failure_count(self, tenant):
        webhook = _make_webhook(tenant, failure_count=0)
        payload = {"event": "ticket.created"}

        with mock.patch(
            "apps.tickets.webhook_service.requests.post",
            return_value=_fake_response(500),
        ):
            success, status = deliver_webhook(webhook, payload)

        assert success is False
        assert status == 500
        webhook.refresh_from_db()
        assert webhook.failure_count == 1
        assert webhook.last_status_code == 500
        assert webhook.is_active is True  # not yet at the disable threshold

    def test_4xx_also_counts_as_failure(self, tenant):
        webhook = _make_webhook(tenant, failure_count=2)
        payload = {"event": "ticket.created"}

        with mock.patch(
            "apps.tickets.webhook_service.requests.post",
            return_value=_fake_response(404),
        ):
            success, _ = deliver_webhook(webhook, payload)

        assert success is False
        webhook.refresh_from_db()
        assert webhook.failure_count == 3


# ---------------------------------------------------------------------------
# (c) auto-disable at MAX_FAILURE_COUNT
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestWebhookAutoDisable:
    def test_reaching_max_failures_disables_webhook(self, tenant):
        # One short of the threshold; this delivery pushes it over.
        webhook = _make_webhook(tenant, failure_count=MAX_FAILURE_COUNT - 1)
        payload = {"event": "ticket.created"}

        with mock.patch(
            "apps.tickets.webhook_service.requests.post",
            return_value=_fake_response(503),
        ):
            success, _ = deliver_webhook(webhook, payload)

        assert success is False
        webhook.refresh_from_db()
        assert webhook.failure_count == MAX_FAILURE_COUNT
        assert webhook.is_active is False

    def test_below_threshold_stays_active(self, tenant):
        webhook = _make_webhook(tenant, failure_count=MAX_FAILURE_COUNT - 3)
        payload = {"event": "ticket.created"}

        with mock.patch(
            "apps.tickets.webhook_service.requests.post",
            return_value=_fake_response(500),
        ):
            deliver_webhook(webhook, payload)

        webhook.refresh_from_db()
        assert webhook.failure_count == MAX_FAILURE_COUNT - 2
        assert webhook.is_active is True


# ---------------------------------------------------------------------------
# (d) requests exception handled gracefully
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestWebhookException:
    def test_request_exception_is_handled(self, tenant):
        webhook = _make_webhook(tenant, failure_count=0)
        payload = {"event": "ticket.created"}

        with mock.patch(
            "apps.tickets.webhook_service.requests.post",
            side_effect=requests.ConnectionError("boom"),
        ):
            # Must NOT raise.
            success, status = deliver_webhook(webhook, payload)

        assert success is False
        assert status is None
        webhook.refresh_from_db()
        assert webhook.failure_count == 1
        assert webhook.last_status_code is None
        assert webhook.last_triggered_at is not None

    def test_timeout_exception_can_trigger_auto_disable(self, tenant):
        webhook = _make_webhook(tenant, failure_count=MAX_FAILURE_COUNT - 1)
        payload = {"event": "ticket.created"}

        with mock.patch(
            "apps.tickets.webhook_service.requests.post",
            side_effect=requests.Timeout("slow"),
        ):
            success, status = deliver_webhook(webhook, payload)

        assert success is False
        assert status is None
        webhook.refresh_from_db()
        assert webhook.failure_count == MAX_FAILURE_COUNT
        assert webhook.is_active is False


# ---------------------------------------------------------------------------
# (e) fire_webhooks dispatches only to matching active webhooks
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestFireWebhooks:
    def test_only_active_subscribed_webhooks_dispatched(self, tenant):
        matching = _make_webhook(
            tenant,
            name="matching",
            events=[Webhook.EventType.TICKET_CREATED],
            is_active=True,
        )
        # Active but subscribed to a different event.
        _make_webhook(
            tenant,
            name="other-event",
            events=[Webhook.EventType.TICKET_CLOSED],
            is_active=True,
        )
        # Subscribed to the right event but inactive.
        _make_webhook(
            tenant,
            name="inactive",
            events=[Webhook.EventType.TICKET_CREATED],
            is_active=False,
        )

        with mock.patch(
            "apps.tickets.tasks.deliver_webhook_task.delay"
        ) as mock_delay:
            fire_webhooks(
                tenant,
                Webhook.EventType.TICKET_CREATED,
                {"ticket_id": "abc"},
            )

        assert mock_delay.call_count == 1
        dispatched_id, payload = mock_delay.call_args[0]
        assert dispatched_id == str(matching.pk)
        assert payload["event"] == Webhook.EventType.TICKET_CREATED
        assert payload["tenant_id"] == str(tenant.pk)
        assert payload["data"] == {"ticket_id": "abc"}
        assert "timestamp" in payload

    def test_no_matching_webhooks_dispatches_nothing(self, tenant):
        _make_webhook(
            tenant,
            events=[Webhook.EventType.TICKET_CLOSED],
            is_active=True,
        )

        with mock.patch(
            "apps.tickets.tasks.deliver_webhook_task.delay"
        ) as mock_delay:
            fire_webhooks(
                tenant,
                Webhook.EventType.TICKET_CREATED,
                {"ticket_id": "abc"},
            )

        mock_delay.assert_not_called()

    def test_fire_webhooks_is_tenant_scoped(self, tenant, tenant_b):
        # A webhook on tenant_b must NOT receive tenant's event.
        _make_webhook(
            tenant_b,
            name="other-tenant",
            events=[Webhook.EventType.TICKET_CREATED],
            is_active=True,
        )
        mine = _make_webhook(
            tenant,
            name="mine",
            events=[Webhook.EventType.TICKET_CREATED],
            is_active=True,
        )

        with mock.patch(
            "apps.tickets.tasks.deliver_webhook_task.delay"
        ) as mock_delay:
            fire_webhooks(
                tenant,
                Webhook.EventType.TICKET_CREATED,
                {"x": 1},
            )

        assert mock_delay.call_count == 1
        dispatched_id, _ = mock_delay.call_args[0]
        assert dispatched_id == str(mine.pk)


# ---------------------------------------------------------------------------
# deliver_webhook_task wrapper
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestDeliverWebhookTask:
    def test_task_delivers_active_webhook(self, tenant):
        webhook = _make_webhook(tenant, is_active=True)
        payload = {"event": "ticket.created", "data": {}}

        from apps.tickets.tasks import deliver_webhook_task

        with mock.patch(
            "apps.tickets.webhook_service.requests.post",
            return_value=_fake_response(200),
        ) as mock_post:
            deliver_webhook_task.run(str(webhook.pk), payload)

        assert mock_post.called
        webhook.refresh_from_db()
        assert webhook.last_status_code == 200

    def test_task_noops_for_inactive_webhook(self, tenant):
        webhook = _make_webhook(tenant, is_active=False)
        payload = {"event": "ticket.created"}

        from apps.tickets.tasks import deliver_webhook_task

        with mock.patch(
            "apps.tickets.webhook_service.requests.post",
            return_value=_fake_response(200),
        ) as mock_post:
            deliver_webhook_task.run(str(webhook.pk), payload)

        # Disabled webhook is filtered out → no HTTP attempt.
        mock_post.assert_not_called()
