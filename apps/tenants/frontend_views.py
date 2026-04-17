"""
Frontend views that render Bootstrap templates.

Data is loaded via JavaScript calling the DRF API endpoints.
These views simply serve the page skeleton and handle session auth.
"""

import functools

from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_http_methods


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


def _membership_required(view_func):
    """
    View decorator that requires the authenticated user to have an active
    TenantMembership for the current tenant.
    """
    @functools.wraps(view_func)
    @login_required(login_url="/login/")
    def _wrapped(request, *args, **kwargs):
        tenant = getattr(request, "tenant", None)
        if tenant is not None and not _has_tenant_membership(request.user, tenant):
            logout(request)
            return redirect("frontend:login")
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
                return render(
                    request,
                    "pages/403.html",
                    {"message": "Tenant context required."},
                    status=403,
                )

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
        return redirect("frontend:login")
    from django.conf import settings as django_settings

    return render(request, "pages/landing.html", {
        "DEMO_URL": django_settings.TENANT_URL("demo"),
    })


@require_http_methods(["GET", "POST"])
def login_page(request):
    """Login page with session-based auth."""
    if request.user.is_authenticated:
        return redirect("frontend:dashboard")

    error = None
    if request.method == "POST":
        email = request.POST.get("email", "").strip()
        password = request.POST.get("password", "")
        user = authenticate(request, email=email, password=password)
        if user is not None:
            tenant = getattr(request, "tenant", None)
            if tenant and not _has_tenant_membership(user, tenant):
                error = "You are not a member of this organization."
            else:
                login(request, user)
                next_url = request.GET.get("next", "")
                if next_url and url_has_allowed_host_and_scheme(
                    next_url,
                    allowed_hosts={request.get_host()},
                    require_https=request.is_secure(),
                ):
                    return redirect(next_url)
                return redirect("frontend:dashboard")
        else:
            error = "Invalid email or password."

    return render(request, "pages/login.html", {"error": error})


@require_http_methods(["GET", "POST"])
def register_page(request):
    """Registration page."""
    if request.user.is_authenticated:
        return redirect("frontend:dashboard")

    error = None
    if request.method == "POST":
        from django.contrib.auth import get_user_model

        User = get_user_model()
        email = request.POST.get("email", "").strip()
        password = request.POST.get("password", "")
        password2 = request.POST.get("password2", "")
        first_name = request.POST.get("first_name", "").strip()
        last_name = request.POST.get("last_name", "").strip()

        if not email or not password:
            error = "Email and password are required."
        elif password != password2:
            error = "Passwords do not match."
        elif User.objects.filter(email=email).exists():
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
                )
                tenant = getattr(request, "tenant", None)
                if tenant and not _has_tenant_membership(user, tenant):
                    error = "Account created, but you are not a member of this organization. Contact an admin for access."
                else:
                    login(request, user)
                    return redirect("frontend:dashboard")

    return render(request, "pages/register.html", {"error": error})


@require_http_methods(["GET", "POST"])
def logout_page(request):
    """Log out and redirect to landing."""
    # Set agent status to offline before destroying the session
    if request.user.is_authenticated:
        from apps.agents.models import AgentAvailability
        AgentAvailability.objects.filter(user=request.user).update(status="offline")

    logout(request)
    return redirect("frontend:landing")


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
