"""Launch-readiness fundamentals: security + correctness regression tests.

Companion to tests/test_qa_audit_followups.py. These pin the second batch of
pre-go-live hardening (VoIP intentionally excluded -- on hold).
"""

import uuid

import pytest
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.test import Client

from conftest import MembershipFactory


def _media_client(tenant, user=None):
    """Django test client (the media view reads request.user from the SESSION,
    like a browser <img> request -- DRF force_authenticate would not apply)."""
    c = Client(HTTP_HOST=f"{tenant.slug}.localhost:8001")
    if user is not None:
        c.force_login(user)
    return c


# ---------------------------------------------------------------------------
# Authenticated, tenant-scoped /media/ serving
# ---------------------------------------------------------------------------
@pytest.mark.django_db
class TestProtectedMedia:
    def _store(self, rel_path, body=b"secret-bytes"):
        return default_storage.save(rel_path, ContentFile(body))

    def test_anonymous_is_denied(self, tenant):
        path = self._store(
            f"tenants/{tenant.id}/attachments/{uuid.uuid4().hex}.txt"
        )
        try:
            resp = _media_client(tenant).get(f"/media/{path}")
            assert resp.status_code == 403
        finally:
            default_storage.delete(path)

    def test_tenant_member_can_read_own_file(self, tenant, agent_user):
        path = self._store(
            f"tenants/{tenant.id}/attachments/{uuid.uuid4().hex}.txt", b"hello"
        )
        try:
            resp = _media_client(tenant, agent_user).get(f"/media/{path}")
            assert resp.status_code == 200
            assert b"hello" == b"".join(resp.streaming_content)
        finally:
            default_storage.delete(path)

    def test_cross_tenant_member_is_denied(self, tenant, tenant_b):
        foreign = MembershipFactory(tenant=tenant_b).user
        path = self._store(
            f"tenants/{tenant.id}/attachments/{uuid.uuid4().hex}.txt"
        )
        try:
            resp = _media_client(tenant_b, foreign).get(f"/media/{path}")
            assert resp.status_code == 403
        finally:
            default_storage.delete(path)

    def test_inbound_email_attachment_is_tenant_scoped(self, tenant, tenant_b):
        from apps.inbound_email.models import InboundEmail

        inbound = InboundEmail.objects.create(
            tenant=tenant,
            sender_email="c@example.com",
            recipient_email=f"support+{tenant.slug}@crm.io",
            message_id=f"{uuid.uuid4()}@test.com",
        )
        path = self._store(f"inbound_emails/{inbound.pk}/{uuid.uuid4().hex}.bin")
        foreign = MembershipFactory(tenant=tenant_b).user
        try:
            resp = _media_client(tenant_b, foreign).get(f"/media/{path}")
            assert resp.status_code == 403
        finally:
            default_storage.delete(path)

    def test_public_logo_served_to_anonymous(self, tenant):
        path = self._store(f"tenants/logos/{uuid.uuid4().hex}.png", b"PNG")
        try:
            resp = _media_client(tenant).get(f"/media/{path}")
            assert resp.status_code == 200
        finally:
            default_storage.delete(path)

    def test_missing_file_is_404(self, tenant, agent_user):
        resp = _media_client(tenant, agent_user).get(
            f"/media/tenants/{tenant.id}/attachments/nope.txt"
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# API register no longer bypasses email verification
# ---------------------------------------------------------------------------
@pytest.mark.django_db
class TestRegisterEmailVerification:
    REGISTER = "/api/v1/accounts/auth/register/"
    LOGIN = "/api/v1/accounts/auth/login/"

    CREDS = {
        "email": "newbie@clientmail.com",
        "password": "Str0ng-Passw0rd!",
        "first_name": "New",
        "last_name": "Bie",
    }

    def test_register_creates_inactive_user_and_no_tokens(self, anon_client):
        from apps.accounts.models import User

        resp = anon_client.post(self.REGISTER, self.CREDS, format="json")
        assert resp.status_code == 201
        assert "tokens" not in resp.data  # no JWT for an unverified account
        user = User.objects.get(email=self.CREDS["email"])
        assert user.is_active is False

    def test_unverified_user_cannot_login(self, anon_client):
        anon_client.post(self.REGISTER, self.CREDS, format="json")
        resp = anon_client.post(
            self.LOGIN,
            {"email": self.CREDS["email"], "password": self.CREDS["password"]},
            format="json",
        )
        assert resp.status_code in (400, 401)

    def test_verification_token_is_issued(self, anon_client):
        from apps.accounts.models import EmailVerificationToken, User

        anon_client.post(self.REGISTER, self.CREDS, format="json")
        user = User.objects.get(email=self.CREDS["email"])
        assert EmailVerificationToken.objects.filter(user=user).exists()


# ---------------------------------------------------------------------------
# Deploy-time DEBUG guard
# ---------------------------------------------------------------------------
class TestDebugDeployCheck:
    def test_check_errors_when_debug_on(self, settings):
        from main.checks import debug_must_be_off

        settings.DEBUG = True
        errors = debug_must_be_off(None)
        assert any(e.id == "crm.E001" for e in errors)

    def test_check_passes_when_debug_off(self, settings):
        from main.checks import debug_must_be_off

        settings.DEBUG = False
        assert debug_must_be_off(None) == []

    def test_settings_init_fails_safe_to_prod(self):
        """The branching default must stay False so an UNSET DJANGO_DEBUG loads
        prod.py (DEBUG off), never dev.py."""
        from pathlib import Path

        src = Path("main/settings/__init__.py").read_text()
        assert 'env.bool("DJANGO_DEBUG", default=False)' in src
