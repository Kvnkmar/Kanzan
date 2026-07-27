"""
Service layer for API-key minting, regenerating, and revoking.

All public functions write an immutable ``comments.ActivityLog`` audit row and
defer the "key created" email until after the surrounding transaction commits
(via ``transaction.on_commit``). The cleartext secret is returned exactly once
from ``create_api_key`` / ``regenerate_api_key``; it is never persisted.
"""

from __future__ import annotations

import hashlib
import secrets
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.db import transaction

from apps.accounts.models import Role, TenantMembership
from apps.api_keys.models import APIKey
from apps.comments.models import ActivityLog
from apps.tenants.models import Tenant

User = get_user_model()


# ── Public API ─────────────────────────────────────────────────────────


def create_api_key(
    tenant: Tenant,
    name: str,
    role: Role,
    created_by,
    *,
    expires_at=None,
) -> tuple[APIKey, str]:
    """
    Mint a new API key for the given tenant.

    Creates the synthetic service-account ``User`` + ``TenantMembership`` +
    ``APIKey`` rows atomically. Returns ``(apikey, cleartext)`` — the
    cleartext is shown to the admin exactly once and never persisted.
    """
    with transaction.atomic():
        cleartext = _generate_cleartext(tenant)
        service_user = _create_service_user(tenant, name)
        _get_or_create_membership(service_user, tenant, role, created_by)

        apikey = APIKey.objects.create(
            tenant=tenant,
            name=name,
            role=role,
            service_user=service_user,
            prefix=cleartext[:20],
            hashed_key=_hash_key(cleartext),
            created_by=created_by,
            expires_at=expires_at,
        )

        _write_activity_log(
            apikey=apikey,
            actor=created_by,
            action=ActivityLog.Action.API_KEY_CREATED,
            description=f"Created API key '{name}'",
            metadata={"name": name, "role_slug": getattr(role, "slug", "")},
        )

        # Defer the email until after the row is durably committed — otherwise
        # the task could fire before the transaction is visible and find no row.
        recipient = getattr(created_by, "email", "") or ""
        api_key_id = apikey.id

        def _queue_email():
            from apps.api_keys.tasks import send_api_key_created_email_task

            send_api_key_created_email_task.delay(str(api_key_id), recipient)

        transaction.on_commit(_queue_email)

    return apikey, cleartext


def regenerate_api_key(apikey: APIKey, by) -> str:
    """
    Rotate the cleartext for an existing key. Old cleartext is immediately
    invalid. Returns the new cleartext for one-time display. Preserves the
    underlying ``service_user``, ``role``, membership, and name so existing
    audit references stay valid.
    """
    with transaction.atomic():
        cleartext = _generate_cleartext(apikey.tenant)
        apikey.prefix = cleartext[:20]
        apikey.hashed_key = _hash_key(cleartext)
        apikey.save(update_fields=["prefix", "hashed_key", "updated_at"])

        _write_activity_log(
            apikey=apikey,
            actor=by,
            action=ActivityLog.Action.API_KEY_REGENERATED,
            description=f"Regenerated API key '{apikey.name}'",
            metadata={"name": apikey.name},
        )

    return cleartext


def revoke_api_key(apikey: APIKey, by) -> None:
    """
    Soft-revoke a key. Atomically flips ``is_active`` on the APIKey row,
    the service-account User row, and the TenantMembership row so the auth
    class rejects any subsequent presentation of the cleartext.
    """
    with transaction.atomic():
        apikey.is_active = False
        apikey.save(update_fields=["is_active", "updated_at"])

        service_user = apikey.service_user
        if service_user.is_active:
            service_user.is_active = False
            service_user.save(update_fields=["is_active"])

        TenantMembership.objects.filter(
            user=service_user,
            tenant=apikey.tenant,
        ).update(is_active=False)

        _write_activity_log(
            apikey=apikey,
            actor=by,
            action=ActivityLog.Action.API_KEY_REVOKED,
            description=f"Revoked API key '{apikey.name}'",
            metadata={"name": apikey.name},
        )


# ── Private helpers ────────────────────────────────────────────────────


def _generate_cleartext(tenant: Tenant) -> str:
    """
    Build a Stripe-style cleartext: ``kz_live_<slug6>_<32-byte-urlsafe>``.

    The slug fragment is informational (helps integrators recognise which
    tenant a leaked key belongs to). The 32-byte urlsafe portion is the
    cryptographic secret (~256 bits of entropy).
    """
    slug_fragment = (tenant.slug or "")[:6]
    return f"kz_live_{slug_fragment}_{secrets.token_urlsafe(32)}"


def _hash_key(cleartext: str) -> str:
    """SHA-512 hex digest of the cleartext. Used both at mint + auth time."""
    return hashlib.sha512(cleartext.encode()).hexdigest()


def _create_service_user(tenant: Tenant, name: str):
    """
    Create a hidden synthetic ``User`` to back this API key. UUID in the email
    guarantees uniqueness; the user has no usable password (``set_unusable_password``).
    """
    email = f"service+{uuid4().hex}@svc.{tenant.slug}.crm.local"
    user = User(
        email=email,
        first_name=name[:30],
        last_name="(Service Account)",
        is_service_account=True,
        is_active=True,
    )
    user.set_unusable_password()
    user.save()
    return user


def _get_or_create_membership(service_user, tenant: Tenant, role: Role, created_by):
    """
    Ensure the service-account user has a TenantMembership for the chosen
    role. Idempotent — useful if a future flow ever reuses an existing
    service_user (today's create_api_key always mints a fresh one).
    """
    membership, _created = TenantMembership.objects.get_or_create(
        user=service_user,
        tenant=tenant,
        defaults={
            "role": role,
            "is_active": True,
            "invited_by": created_by,
        },
    )
    return membership


def _write_activity_log(*, apikey: APIKey, actor, action, description, metadata):
    """
    Write a polymorphic ``ActivityLog`` row pointing at the APIKey.
    """
    from django.contrib.contenttypes.models import ContentType

    ct = ContentType.objects.get_for_model(APIKey)
    ActivityLog.objects.create(
        tenant=apikey.tenant,
        actor=actor,
        action=action,
        description=description,
        content_type=ct,
        object_id=apikey.id,
        changes=metadata or {},
    )
