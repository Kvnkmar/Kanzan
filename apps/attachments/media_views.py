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

Public assets (tenant branding logos) are served without auth so login/landing
pages keep working. Nothing else may be public: the prefix match in
``serve_protected_media`` runs before authentication.
"""

import posixpath
import uuid as uuid_mod

from django.conf import settings
from django.core.files.storage import default_storage
from django.http import FileResponse, Http404, HttpResponse, HttpResponseForbidden

# Prefixes served WITHOUT authentication. Only tenant branding belongs here --
# a namespace holding real tenant documents must never be listed, because this
# prefix match short-circuits BOTH the auth check and the tenant check below.
# ``tenants/knowledge/`` was removed: it holds Article file attachments, which
# are now gated by ``_knowledge_path_authorized``.
PUBLIC_PREFIXES = ("tenants/logos/",)


def _user_is_tenant_member(user, tenant_id) -> bool:
    from apps.accounts.models import TenantMembership

    return TenantMembership.objects.filter(
        user=user, tenant_id=tenant_id, is_active=True
    ).exists()


def _is_tenant_manager(user, tenant_id) -> bool:
    """Whether ``user`` is an Admin/Manager (effective hierarchy <= 20)."""
    from apps.accounts.models import TenantMembership

    membership = (
        TenantMembership.objects.filter(
            user=user, tenant_id=tenant_id, is_active=True
        )
        .select_related("role", "temporary_role")
        .first()
    )
    if membership is None:
        return False
    try:
        return membership.effective_role.hierarchy_level <= 20
    except Exception:  # pragma: no cover - defensive
        return False


def _attachment_path_authorized(user, path: str, tenant_id: str) -> bool:
    """Object-level gate for ``tenants/<uuid>/attachments/...`` media.

    A tenant member may only read an attachment whose *target object* they can
    access (a ticket they're on, a non-internal comment, a message in their
    conversation), matching the ``download`` @action — not merely any file in
    their tenant.
    """
    if not _user_is_tenant_member(user, tenant_id):
        return False

    from apps.attachments.access import can_access_target
    from apps.attachments.models import Attachment
    from main.context import tenant_context

    attachment = (
        Attachment.unscoped.filter(file=path)
        .select_related("content_type", "tenant")
        .first()
    )
    if attachment is None:
        # No attachment row for this path -> nothing object-level to leak;
        # membership is already confirmed and the 404 follows at the file step.
        return True
    # /media/ is TenantMiddleware-exempt, so no tenant is bound in context here.
    # can_access_target resolves the target via the tenant-scoped default
    # manager, which would fail closed (return None) off-context and wrongly
    # deny every download. Bind the attachment's tenant for the check.
    with tenant_context(attachment.tenant):
        return can_access_target(
            user, attachment.tenant, attachment.content_type, attachment.object_id
        )


def _export_path_authorized(user, path: str) -> bool:
    """Object-level gate for ``exports/<name>`` analytics export files.

    Export files contain the full-tenant dataset (all contacts' PII, every
    ticket). Only the requester or a Manager+ of the owning tenant may download
    one — never any authenticated user (the previous behaviour), and never a
    user of a different tenant.
    """
    from apps.analytics.models import ExportJob

    job = (
        ExportJob.unscoped.filter(file=path)
        .only("tenant_id", "requested_by_id")
        .first()
    )
    if job is None:
        # No such export -> nothing to leak; the 404 follows at the file step.
        return True
    if job.tenant_id is None or not _user_is_tenant_member(user, job.tenant_id):
        return False
    if job.requested_by_id == getattr(user, "id", None):
        return True
    return _is_tenant_manager(user, job.tenant_id)


def _knowledge_path_authorized(user, path: str) -> bool:
    """Whether ``user`` may read a KB article attachment at ``path``.

    Mirrors the audience gate in ``ArticleViewSet.get_queryset``: tenant
    membership first, then the draft/published and ``allowed_groups``
    restrictions. Admin/Manager (hierarchy <= 20) bypass, as they do there.

    Fails CLOSED when no Article owns the path -- unlike the older helpers in
    this module, an orphaned file on disk is not readable just because its row
    is gone.
    """
    from apps.knowledge.models import Article

    article = (
        Article.unscoped.filter(file=path)
        .only("tenant_id", "author_id", "status")
        .first()
    )
    if article is None or article.tenant_id is None:
        return False
    if not _user_is_tenant_member(user, article.tenant_id):
        return False
    if _is_tenant_manager(user, article.tenant_id):
        return True

    is_author = article.author_id == getattr(user, "id", None)
    if article.status != Article.Status.PUBLISHED and not is_author:
        return False

    # NOTE: read the M2M through its auto-created through model, NOT via
    # ``article.allowed_groups``. UserGroup is tenant-scoped, so that related
    # manager fail-closes to empty whenever no tenant is bound -- and /media/
    # is exempt from TenantMiddleware, so nothing is ever bound here. Using it
    # would silently report "unrestricted" and leak every restricted article.
    from apps.accounts.models import UserGroup

    restricted_to = list(
        Article.allowed_groups.through.objects.filter(
            article_id=article.pk
        ).values_list("usergroup_id", flat=True)
    )
    if not restricted_to:
        return True
    if is_author:
        return True
    return UserGroup.unscoped.filter(id__in=restricted_to, members=user).exists()


def _is_authorized(user, path: str) -> bool:
    """Whether an authenticated ``user`` may read media ``path``."""
    parts = path.split("/")

    # tenants/<uuid>/... -> attachments + recordings: must be a tenant member.
    if len(parts) >= 2 and parts[0] == "tenants":
        # tenants/knowledge/... -> KB article attachments. No tenant segment is
        # present in the path, so the owning Article is what carries the tenant.
        if parts[1] == "knowledge":
            return _knowledge_path_authorized(user, path)
        try:
            uuid_mod.UUID(parts[1])
        except (ValueError, AttributeError):
            # tenants/<non-uuid>/... (e.g. logos, handled by PUBLIC_PREFIXES);
            # any authenticated user may read.
            return True
        # Attachments get an object-level check (ticket/comment/message access);
        # other tenant files (e.g. call recordings) keep the membership gate.
        if len(parts) >= 3 and parts[2] == "attachments":
            return _attachment_path_authorized(user, path, parts[1])
        return _user_is_tenant_member(user, parts[1])

    # exports/<name> -> analytics export files: requester or Manager+ only.
    if len(parts) >= 2 and parts[0] == "exports":
        return _export_path_authorized(user, path)

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
