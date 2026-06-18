"""Authenticated media serving.

Closes the unauthenticated ``/media/`` hole: raw media URLs previously served
ANY tenant's uploaded files (attachments, call recordings, customer email
attachments) to anyone. This view gates every request by authentication and
tenant ownership.

Serving strategy:
* Production -- set ``USE_X_ACCEL_REDIRECT = True``; after authorizing, this
  returns an ``X-Accel-Redirect`` so nginx streams the bytes from an internal
  location (no Python in the byte path). See ``docs/deploy/protected-media.md``.
* Dev / tests -- the file is streamed directly via ``FileResponse``.

Public assets (tenant branding logos, KB article images embedded in the public
portal) are served without auth so login/landing/portal pages keep working.
"""

import posixpath
import uuid as uuid_mod

from django.conf import settings
from django.core.files.storage import default_storage
from django.http import FileResponse, Http404, HttpResponse, HttpResponseForbidden

# Prefixes served WITHOUT authentication (branding + public KB portal assets).
PUBLIC_PREFIXES = ("tenants/logos/", "tenants/knowledge/")


def _user_is_tenant_member(user, tenant_id) -> bool:
    from apps.accounts.models import TenantMembership

    return TenantMembership.objects.filter(
        user=user, tenant_id=tenant_id, is_active=True
    ).exists()


def _is_authorized(user, path: str) -> bool:
    """Whether an authenticated ``user`` may read media ``path``."""
    parts = path.split("/")

    # tenants/<uuid>/... -> attachments + recordings: must be a tenant member.
    if len(parts) >= 2 and parts[0] == "tenants":
        try:
            uuid_mod.UUID(parts[1])
        except (ValueError, AttributeError):
            # tenants/<non-uuid>/... (e.g. handled by PUBLIC_PREFIXES already);
            # any authenticated user may read.
            return True
        return _user_is_tenant_member(user, parts[1])

    # inbound_emails/<pk>/... -> customer email attachments: gate by the
    # owning InboundEmail's tenant.
    if len(parts) >= 2 and parts[0] == "inbound_emails":
        from apps.inbound_email.models import InboundEmail

        inbound = (
            InboundEmail.objects.filter(pk=parts[1]).only("tenant_id").first()
        )
        if inbound is None:
            # No such email -> nothing to leak; the 404 follows at the file step.
            return True
        if inbound.tenant_id is None:
            return False
        return _user_is_tenant_member(user, inbound.tenant_id)

    # avatars/ and anything else: authenticated is sufficient.
    return True


def _serve(path: str):
    if not default_storage.exists(path):
        raise Http404("File not found.")

    if getattr(settings, "USE_X_ACCEL_REDIRECT", False):
        # nginx serves the bytes from an internal location after we authorize.
        prefix = getattr(settings, "X_ACCEL_MEDIA_PREFIX", "/protected_media/")
        response = HttpResponse()
        response["X-Accel-Redirect"] = posixpath.join(prefix, path)
        # Let nginx set Content-Type / Content-Length.
        del response["Content-Type"]
        return response

    return FileResponse(default_storage.open(path, "rb"))


def serve_protected_media(request, path):
    """Authorize then serve a file under MEDIA_ROOT."""
    normalized = posixpath.normpath(path)
    # Reject path traversal / absolute escapes.
    if normalized.startswith("..") or normalized.startswith("/") or normalized == ".":
        raise Http404()

    # Public branding / portal assets: no auth required.
    if any(normalized.startswith(p) for p in PUBLIC_PREFIXES):
        return _serve(normalized)

    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return HttpResponseForbidden("Authentication required.")

    if not _is_authorized(user, normalized):
        return HttpResponseForbidden("You do not have access to this file.")

    return _serve(normalized)
