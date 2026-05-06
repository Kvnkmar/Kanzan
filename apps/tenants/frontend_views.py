"""
Frontend views that render Bootstrap templates.

Data is loaded via JavaScript calling the DRF API endpoints.
These views simply serve the page skeleton and handle session auth.
"""

import functools
import re
import secrets
from datetime import timedelta
from urllib.parse import urlencode, urlparse

from django.conf import settings as django_settings
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.password_validation import validate_password
from django.core import signing
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import F
from django.http import Http404, HttpResponse, HttpResponseRedirect
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify
from django.views.decorators.http import require_http_methods

EMAIL_VERIFICATION_TOKEN_TTL = timedelta(hours=24)

# Short-lived signed token used to hand a freshly-authenticated user off
# from the bare domain to a tenant subdomain. 30 seconds is enough for
# the browser's redirect + handoff round trip and short enough to be
# useless if captured.
HANDOFF_TOKEN_SALT = "kanzen.auth.handoff.v1"
HANDOFF_TOKEN_TTL_SECONDS = 30

SESSION_AUTH_VERSION_KEY = "auth_version"


# ---------------------------------------------------------------------------
# Tenant membership helpers
# ---------------------------------------------------------------------------

def _has_tenant_membership(user, tenant):
    """Return True if *user* has an active membership for *tenant*."""
    if tenant is None:
        return False
    from apps.accounts.models import TenantMembership
    return TenantMembership.objects.filter(
        user=user, tenant=tenant, is_active=True,
    ).exists()


# ---------------------------------------------------------------------------
# Root-domain auth routing helpers
# ---------------------------------------------------------------------------

def _on_bare_domain(request) -> bool:
    """True iff the request hit the bare BASE_DOMAIN (no tenant subdomain)."""
    host = request.get_host().split(":")[0].lower()
    return host in ("localhost", "127.0.0.1", django_settings.BASE_DOMAIN.lower())




def _root_url(path: str, query: dict | None = None) -> str:
    """Absolute URL on the bare domain. Used to bounce subdomain visitors back."""
    url = f"{django_settings.BASE_URL}{path}"
    if query:
        url = f"{url}?{urlencode(query)}"
    return url


def _redirect_to_root_login(request) -> HttpResponseRedirect:
    """
    Send the user to /login/ on the bare domain, preserving where they were.

    Prefer an incoming ?next= (meaning @login_required already captured
    their intended destination); otherwise use the page they're currently
    on. Either way, the value we hand off is absolute so _safe_next_url
    can recognise it as a subdomain URL and honour it post-login.
    """
    incoming_next = request.GET.get("next", "").strip()
    if incoming_next:
        if urlparse(incoming_next).netloc:
            next_url = incoming_next
        else:
            next_url = f"{request.scheme}://{request.get_host()}{incoming_next}"
    else:
        next_url = request.build_absolute_uri()
    return redirect(_root_url("/login/", {"next": next_url}))


def _make_handoff_token(user_id, next_path: str = "/dashboard/") -> str:
    """
    Mint a short-lived signed token authorising a tenant subdomain to
    establish a session for *user_id* and redirect to *next_path*.
    """
    return signing.dumps(
        {"uid": str(user_id), "next": next_path},
        salt=HANDOFF_TOKEN_SALT,
    )


def _read_handoff_token(token: str):
    """Decode + verify a handoff token; raises signing.BadSignature on failure."""
    return signing.loads(
        token, salt=HANDOFF_TOKEN_SALT, max_age=HANDOFF_TOKEN_TTL_SECONDS
    )


def _tenant_handoff_url(user, slug: str, next_path: str = "/dashboard/") -> str:
    """
    Build the /auth/handoff/ URL on a tenant subdomain that will log *user*
    in on that host (host-only cookie) and then redirect to *next_path*.
    This is the ONLY supported way to start a session on a tenant from
    code running on the bare domain — direct redirects wouldn't carry a
    session cookie because we don't share cookies across hosts.
    """
    token = _make_handoff_token(user.id, next_path)
    return f"{django_settings.TENANT_URL(slug)}/auth/handoff/?{urlencode({'t': token})}"




def _safe_next_url(next_url: str) -> str | None:
    """
    Return next_url iff it is an absolute URL on a tenant subdomain of our
    BASE_DOMAIN. Relative URLs and URLs on the bare domain itself are
    rejected — tenant pages only live on subdomains, so on the bare domain
    we always want membership-based routing to pick the correct workspace.
    """
    if not next_url:
        return None
    parsed = urlparse(next_url)
    if not parsed.netloc:
        return None
    host = parsed.netloc.split(":")[0].lower()
    base = django_settings.BASE_DOMAIN.lower()
    if host == base:
        return None
    if host.endswith(f".{base}"):
        return next_url
    return None


def _post_auth_redirect(user, next_url: str | None = None) -> HttpResponseRedirect:
    """
    Decide where a freshly authenticated user should land.

    Priority:
        1. Safe ?next= pointing at a tenant subdomain the user is a
           member of → hand off through that tenant so a session is
           established there.
        2. Exactly one active tenant membership → that tenant's dashboard
           via handoff.
        3. Multiple memberships → /workspaces/ picker on the bare domain.
        4. No memberships → /setup-company/ on the bare domain.

    A ?next= URL that targets a tenant the user doesn't belong to is
    ignored — honouring it would just bounce them right back out of
    that tenant and loop.
    """
    from apps.accounts.models import TenantMembership

    memberships = list(
        TenantMembership.objects.select_related("tenant")
        .filter(user=user, is_active=True, tenant__is_active=True)
    )
    allowed_slugs = {m.tenant.slug for m in memberships}

    safe_next = _safe_next_url(next_url) if next_url else None
    if safe_next:
        parsed = urlparse(safe_next)
        host = parsed.netloc.split(":")[0].lower()
        base = django_settings.BASE_DOMAIN.lower()
        slug = host.removesuffix(f".{base}") if host.endswith(f".{base}") else None
        if slug and slug in allowed_slugs:
            next_path = parsed.path or "/dashboard/"
            if parsed.query:
                next_path = f"{next_path}?{parsed.query}"
            return redirect(_tenant_handoff_url(user, slug, next_path))
        # Otherwise fall through to membership-based routing below.

    if len(memberships) == 1:
        return redirect(_tenant_handoff_url(user, memberships[0].tenant.slug))
    if len(memberships) > 1:
        return redirect(_root_url("/workspaces/"))
    return redirect(_root_url("/setup-company/"))


def _membership_required(view_func):
    """
    View decorator that requires the authenticated user to have an active
    TenantMembership for the current tenant.
    """
    @functools.wraps(view_func)
    @login_required(login_url="/login/")
    def _wrapped(request, *args, **kwargs):
        tenant = getattr(request, "tenant", None)
        if tenant is None:
            # Tenant pages only make sense on subdomains. When an
            # authenticated user lands on one of these paths on the bare
            # domain (stale bookmark, typed URL, etc.), send them to the
            # right workspace instead of rendering a tenantless page.
            return _post_auth_redirect(request.user)
        if not _has_tenant_membership(request.user, tenant):
            # Authenticated but not a member of THIS tenant. Don't
            # logout+login — that loops when the user's browser keeps
            # bringing them back here. Route to the bare-domain picker,
            # which correctly sends them to their own workspace (or the
            # setup flow if they have none).
            return _post_auth_redirect(request.user)
        return view_func(request, *args, **kwargs)
    return _wrapped


# ---------------------------------------------------------------------------
# Role-based access decorator
# ---------------------------------------------------------------------------

def _role_required(max_hierarchy):
    """
    View decorator that requires the user's role hierarchy_level to be
    <= *max_hierarchy* within the current tenant.

    Users without a membership or with insufficient role see a 403 page.

    Usage::

        @_role_required(20)  # Admin (10) + Manager (20)
        def settings_page(request):
            ...
    """

    def decorator(view_func):
        @functools.wraps(view_func)
        @login_required(login_url="/login/")
        def _wrapped(request, *args, **kwargs):
            tenant = getattr(request, "tenant", None)
            if tenant is None:
                # Same rationale as _membership_required: on the bare
                # domain, route the user to their workspace.
                return _post_auth_redirect(request.user)

            from apps.accounts.models import TenantMembership

            membership = (
                TenantMembership.objects.select_related("role")
                .filter(user=request.user, tenant=tenant, is_active=True)
                .first()
            )

            if membership is None or membership.role.hierarchy_level > max_hierarchy:
                return render(
                    request,
                    "pages/403.html",
                    {"message": "You do not have permission to access this page."},
                    status=403,
                )

            return view_func(request, *args, **kwargs)

        return _wrapped

    return decorator


# ---------------------------------------------------------------------------
# Public pages
# ---------------------------------------------------------------------------

def landing_page(request):
    """Main landing page on bare domain. Redirects to dashboard if on tenant subdomain."""
    if getattr(request, "tenant", None):
        if request.user.is_authenticated:
            return redirect("frontend:dashboard")
        return _redirect_to_root_login(request)
    # BASE_URL is already injected by apps.tenants.context_processors.tenant_context.
    return render(request, "landing/landing_crm.html")


@require_http_methods(["GET", "POST"])
def login_page(request):
    """
    Sign-in page served on the bare BASE_DOMAIN.

    If accessed on a tenant subdomain (usually because an @login_required
    decorator bounced an anonymous user to /login/), we redirect to the
    bare domain with ?next= preserving their intended destination.
    """
    if not _on_bare_domain(request):
        return _redirect_to_root_login(request)

    if request.user.is_authenticated:
        return _post_auth_redirect(request.user, request.GET.get("next"))

    error = None
    if request.method == "POST":
        email = request.POST.get("email", "").strip()
        password = request.POST.get("password", "")
        user = authenticate(request, email=email, password=password)
        if user is not None:
            login(request, user)
            _stamp_session_auth_version(request, user)
            return _post_auth_redirect(user, request.GET.get("next"))
        # authenticate() returns None for inactive users too (unverified email).
        from django.contrib.auth import get_user_model

        User = get_user_model()
        existing = User.objects.filter(email__iexact=email).first()
        if existing and not existing.is_active:
            error = "Please verify your email address before signing in."
        else:
            error = "Invalid email or password."

    return render(request, "pages/login.html", {"error": error})


@require_http_methods(["GET", "POST"])
def register_page(request):
    """
    Signup page served on the bare BASE_DOMAIN. Creates an inactive user
    and emails a verification link; the user activates and sets up their
    company in subsequent steps.
    """
    if not _on_bare_domain(request):
        return redirect(_root_url("/register/"))

    if request.user.is_authenticated:
        return _post_auth_redirect(request.user)

    error = None
    if request.method == "POST":
        from django.contrib.auth import get_user_model

        User = get_user_model()
        email = request.POST.get("email", "").strip().lower()
        password = request.POST.get("password", "")
        password2 = request.POST.get("password2", "")
        first_name = request.POST.get("first_name", "").strip()
        last_name = request.POST.get("last_name", "").strip()

        if not email or not password:
            error = "Email and password are required."
        elif password != password2:
            error = "Passwords do not match."
        elif User.objects.filter(email__iexact=email).exists():
            error = "An account with this email already exists."
        else:
            try:
                validate_password(password)
            except ValidationError as exc:
                error = " ".join(exc.messages)
            if not error:
                user = User.objects.create_user(
                    email=email,
                    password=password,
                    first_name=first_name,
                    last_name=last_name,
                    is_active=False,
                )
                _send_email_verification(user)
                return redirect(
                    f"{reverse('frontend:verify-email-sent')}?{urlencode({'email': user.email})}"
                )

    return render(request, "pages/register.html", {"error": error})


@require_http_methods(["GET"])
def verify_email_sent_page(request):
    """'Check your inbox' confirmation shown right after signup."""
    return render(
        request,
        "pages/auth/verify_email_sent.html",
        {"email": request.GET.get("email", "")},
    )


@require_http_methods(["GET"])
def verify_email_page(request):
    """
    Consume a verification token, activate the user, sign them in, and
    send them to /setup-company/.
    """
    from apps.accounts.models import EmailVerificationToken

    token_value = request.GET.get("token", "").strip()
    if not token_value:
        return render(
            request,
            "pages/auth/verify_email_error.html",
            {"message": "Missing verification token."},
            status=400,
        )

    token = (
        EmailVerificationToken.objects.select_related("user")
        .filter(token=token_value)
        .first()
    )
    if token is None:
        return render(
            request,
            "pages/auth/verify_email_error.html",
            {"message": "This verification link is invalid."},
            status=400,
        )
    if token.is_consumed:
        # Idempotent success: if they click the link twice, just log them in.
        if token.user.is_active:
            login(request, token.user)
            _stamp_session_auth_version(request, token.user)
            return _post_auth_redirect(token.user)
        return render(
            request,
            "pages/auth/verify_email_error.html",
            {"message": "This verification link has already been used."},
            status=400,
        )
    if token.is_expired:
        return render(
            request,
            "pages/auth/verify_email_error.html",
            {"message": "This verification link has expired. Please sign up again."},
            status=400,
        )

    user = token.user
    with transaction.atomic():
        user.is_active = True
        user.save(update_fields=["is_active"])
        token.consumed_at = timezone.now()
        token.save(update_fields=["consumed_at"])

    login(request, user)
    _stamp_session_auth_version(request, user)
    return _post_auth_redirect(user)


@require_http_methods(["GET", "POST"])
@login_required(login_url="/login/")
def setup_company_page(request):
    """
    One-time flow where a newly verified user names their workspace.
    Creates the Tenant (signals seed TenantSettings + default Admin/
    Manager/Agent roles) and an Admin TenantMembership for the user.
    Users who already have at least one membership are bounced to their
    dashboard (or the picker).
    """
    if not _on_bare_domain(request):
        return redirect(_root_url("/setup-company/"))

    from apps.accounts.models import Role, TenantMembership
    from apps.tenants.models import Tenant

    existing = TenantMembership.objects.filter(
        user=request.user, is_active=True, tenant__is_active=True,
    ).exists()
    if existing:
        return _post_auth_redirect(request.user)

    error = None
    name = ""
    slug = ""
    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        slug = request.POST.get("slug", "").strip().lower()
        if not slug and name:
            slug = slugify(name)

        if not name:
            error = "Company name is required."
        elif not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,62}", slug):
            error = "Slug must be 2–63 chars, lowercase letters, digits or dashes, and start with a letter or digit."
        elif slug in _RESERVED_SLUGS:
            error = "That slug is reserved. Please choose another."
        elif Tenant.objects.filter(slug=slug).exists():
            error = "That workspace URL is already taken. Please choose another."
        else:
            try:
                with transaction.atomic():
                    tenant = Tenant.objects.create(name=name, slug=slug)
                    admin_role = Role.unscoped.get(tenant=tenant, slug="admin")
                    TenantMembership.objects.create(
                        user=request.user, tenant=tenant, role=admin_role,
                    )
            except IntegrityError:
                error = "That workspace URL is already taken. Please choose another."
            else:
                return redirect(_tenant_handoff_url(request.user, tenant.slug))

    return render(
        request,
        "pages/auth/setup_company.html",
        {"error": error, "name": name, "slug": slug},
    )


@require_http_methods(["GET"])
@login_required(login_url="/login/")
def workspaces_page(request):
    """Picker shown when the signed-in user belongs to multiple tenants."""
    if not _on_bare_domain(request):
        return redirect(_root_url("/workspaces/"))

    from apps.accounts.models import TenantMembership

    memberships = list(
        TenantMembership.objects.select_related("tenant", "role")
        .filter(user=request.user, is_active=True, tenant__is_active=True)
        .order_by("tenant__name")
    )

    workspaces = [
        {
            "name": m.tenant.name,
            "slug": m.tenant.slug,
            "role": m.role.name,
            "url": _tenant_handoff_url(request.user, m.tenant.slug),
        }
        for m in memberships
    ]
    return render(
        request,
        "pages/auth/workspaces.html",
        {"workspaces": workspaces},
    )


@require_http_methods(["GET"])
def auth_handoff(request):
    """
    Single-use entry point on a tenant subdomain.

    Validates the signed token from ``?t=`` minted on the bare domain,
    logs the user in on THIS host (establishing a host-only session),
    and redirects to the target path inside the tenant. Runs on tenant
    subdomains only — on the bare domain there's no session to establish.
    """
    tenant = getattr(request, "tenant", None)
    if tenant is None:
        # Handoff only makes sense in a tenant context.
        return redirect(_root_url("/login/"))

    token = request.GET.get("t", "").strip()
    if not token:
        return redirect(_root_url("/login/"))

    try:
        payload = _read_handoff_token(token)
    except signing.SignatureExpired:
        # Token aged out — bounce back to the bare domain, which will
        # re-mint a fresh token if the user still has a valid bare-domain
        # session.
        return redirect(_root_url("/login/"))
    except signing.BadSignature:
        return redirect(_root_url("/login/"))

    from django.contrib.auth import get_user_model

    User = get_user_model()
    user = User.objects.filter(id=payload.get("uid"), is_active=True).first()
    if user is None:
        return redirect(_root_url("/login/"))

    if not _has_tenant_membership(user, tenant):
        # Token is valid but user isn't a member of this tenant — probably
        # because they picked a workspace they can't access or the membership
        # was revoked between mint and redemption. Send them back to the
        # bare-domain picker which will route them correctly.
        return redirect(_root_url("/workspaces/"))

    login(request, user)
    _stamp_session_auth_version(request, user)

    next_path = payload.get("next") or "/dashboard/"
    # Guard against open redirects: only accept relative paths on this host.
    if next_path.startswith(("http://", "https://", "//")):
        next_path = "/dashboard/"
    if not next_path.startswith("/"):
        next_path = "/dashboard/"
    return redirect(next_path)


def _stamp_session_auth_version(request, user) -> None:
    """Record the user's current auth_version in the session for global-logout checks."""
    version = getattr(user, "auth_version", 0)
    request.session[SESSION_AUTH_VERSION_KEY] = version


@require_http_methods(["GET", "POST"])
def logout_page(request):
    """
    Global logout: bumps User.auth_version so SessionVersionMiddleware
    kills every other session this user has on the next request, then
    terminates the local session.
    """
    if request.user.is_authenticated:
        from apps.agents.models import AgentAvailability

        # Bump the user's auth_version. Every other live session this
        # user has — on the bare domain and on every tenant subdomain —
        # will fail the SessionVersionMiddleware check on its next
        # request and be flushed automatically.
        request.user.__class__.objects.filter(pk=request.user.pk).update(
            auth_version=F("auth_version") + 1
        )
        AgentAvailability.objects.filter(user=request.user).update(status="offline")

    logout(request)
    return redirect(_root_url("/login/"))


# ---------------------------------------------------------------------------
# Email verification helpers
# ---------------------------------------------------------------------------

# Slugs we never want assigned to user-created tenants (they collide with
# reserved hostnames or middleware-exempt paths).
_RESERVED_SLUGS = frozenset({
    "www", "api", "admin", "mail", "smtp", "imap", "ftp",
    "login", "register", "signup", "logout", "auth", "account", "accounts",
    "dashboard", "app", "static", "media", "assets", "docs", "help", "support",
    "billing", "status", "health", "about", "pricing", "demo", "kanzen",
    "verify-email", "setup-company", "workspaces",
})


def _send_email_verification(user) -> None:
    """Create an EmailVerificationToken and email the link."""
    from apps.accounts.models import EmailVerificationToken
    from django.core.mail import EmailMultiAlternatives
    from django.template.loader import render_to_string

    token_value = secrets.token_urlsafe(32)
    EmailVerificationToken.objects.create(
        user=user,
        token=token_value,
        expires_at=timezone.now() + EMAIL_VERIFICATION_TOKEN_TTL,
    )

    verify_url = _root_url("/verify-email/", {"token": token_value})
    context = {
        "user": user,
        "verify_url": verify_url,
        "ttl_hours": int(EMAIL_VERIFICATION_TOKEN_TTL.total_seconds() // 3600),
    }
    subject = "Verify your Kanzen Suite email"
    text_body = render_to_string("auth/email/verify_email.txt", context)
    html_body = render_to_string("auth/email/verify_email.html", context)

    message = EmailMultiAlternatives(
        subject=subject,
        body=text_body,
        from_email=getattr(django_settings, "DEFAULT_FROM_EMAIL", ""),
        to=[user.email],
    )
    message.attach_alternative(html_body, "text/html")
    message.send(fail_silently=False)


# ---------------------------------------------------------------------------
# Authenticated pages (all roles)
# ---------------------------------------------------------------------------

@_membership_required
def profile_page(request):
    return render(request, "pages/profile.html")


@_membership_required
def dashboard_page(request):
    return render(request, "pages/dashboard.html")


@_membership_required
def ticket_list_page(request):
    return render(request, "pages/tickets/list.html")


@_membership_required
def ticket_create_page(request):
    return render(request, "pages/tickets/create.html")


@_membership_required
def ticket_detail_page(request, ticket_number):
    return render(request, "pages/tickets/detail.html", {"ticket_number": ticket_number})


@_membership_required
def contact_list_page(request):
    return render(request, "pages/contacts/list.html")


@_membership_required
def contact_create_page(request):
    return render(request, "pages/contacts/create.html")


@_membership_required
def contact_detail_page(request, contact_id):
    return render(request, "pages/contacts/detail.html", {"contact_id": contact_id})


@_membership_required
def calendar_page(request):
    return render(request, "pages/calendar.html")


@_membership_required
def kanban_page(request):
    return render(request, "pages/kanban/board.html")


@_membership_required
def messaging_page(request):
    return render(request, "pages/messaging/chat.html")


@_membership_required
def analytics_page(request):
    return render(request, "pages/analytics/overview.html")


@_membership_required
def knowledge_list_page(request):
    return render(request, "pages/knowledge/list.html", {
        "current_user_id": str(request.user.id) if request.user.is_authenticated else "",
    })


@_membership_required
def knowledge_article_page(request, article_slug):
    return render(request, "pages/knowledge/article.html", {
        "article_slug": article_slug,
        "current_user_id": str(request.user.id) if request.user.is_authenticated else "",
    })


# ---------------------------------------------------------------------------
# Admin-only pages (hierarchy_level <= 10)
# ---------------------------------------------------------------------------

@_membership_required
def settings_page(request):
    return render(request, "pages/settings/tenant.html")


@_role_required(20)
def billing_page(request):
    return render(request, "pages/billing/plans.html")


# ---------------------------------------------------------------------------
# Manager+ pages (Admin + Manager, hierarchy_level <= 20)
# ---------------------------------------------------------------------------

@_role_required(20)
def users_page(request):
    return render(request, "pages/users/list.html")


@_role_required(20)
def agents_page(request):
    return render(request, "pages/agents/list.html")


@_role_required(30)
def emails_page(request):
    return render(request, "pages/emails/list.html")


@_membership_required
def inbound_email_page(request):
    return render(request, "pages/inbound_email/list.html")


@_membership_required
def reminders_page(request):
    return render(request, "pages/reminders/list.html")


@_role_required(20)
def audit_log_page(request):
    return render(request, "pages/audit_log/list.html")


@_membership_required
def calls_page(request):
    return render(request, "pages/voip/call_history.html")
