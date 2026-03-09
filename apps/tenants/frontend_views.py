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
# Role-based access decorator
# ---------------------------------------------------------------------------

def _role_required(max_hierarchy):
    """
    View decorator that requires the user's role hierarchy_level to be
    <= *max_hierarchy* within the current tenant.

    Superusers bypass the check.  Users without a membership or with
    insufficient role see a 403 page.

    Usage::

        @_role_required(20)  # Admin (10) + Manager (20)
        def settings_page(request):
            ...
    """

    def decorator(view_func):
        @functools.wraps(view_func)
        @login_required(login_url="/login/")
        def _wrapped(request, *args, **kwargs):
            if request.user.is_superuser:
                return view_func(request, *args, **kwargs)

            tenant = getattr(request, "tenant", None)
            if tenant is None:
                return render(
                    request,
                    "pages/403.html",
                    {"message": "Tenant context required."},
                    status=403,
                )

            # Reuse or create cached membership lookup
            cache_attr = "_cached_tenant_membership"
            if hasattr(request, cache_attr):
                membership = getattr(request, cache_attr)
            else:
                from apps.accounts.models import TenantMembership

                membership = (
                    TenantMembership.objects.select_related("role")
                    .filter(user=request.user, tenant=tenant, is_active=True)
                    .first()
                )
                setattr(request, cache_attr, membership)

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
    return render(request, "pages/landing.html")


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
            login(request, user)
            next_url = request.GET.get("next", "")
            if next_url and url_has_allowed_host_and_scheme(
                next_url,
                allowed_hosts={request.get_host()},
                require_https=request.is_secure(),
            ):
                return redirect(next_url)
            return redirect("frontend:dashboard")
        error = "Invalid email or password."

    return render(request, "pages/login.html", {"error": error})


@require_http_methods(["GET", "POST"])
def register_page(request):
    """Registration page."""
    if request.user.is_authenticated:
        return redirect("frontend:dashboard")

    error = None
    success = None
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
                login(request, user)
                return redirect("frontend:dashboard")

    return render(request, "pages/register.html", {"error": error, "success": success})


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

@login_required(login_url="/login/")
def profile_page(request):
    return render(request, "pages/profile.html")


@login_required(login_url="/login/")
def dashboard_page(request):
    return render(request, "pages/dashboard.html")


@login_required(login_url="/login/")
def ticket_list_page(request):
    return render(request, "pages/tickets/list.html")


@login_required(login_url="/login/")
def ticket_create_page(request):
    return render(request, "pages/tickets/create.html")


@login_required(login_url="/login/")
def ticket_detail_page(request, ticket_number):
    return render(request, "pages/tickets/detail.html", {"ticket_number": ticket_number})


@login_required(login_url="/login/")
def contact_list_page(request):
    return render(request, "pages/contacts/list.html")


@login_required(login_url="/login/")
def contact_create_page(request):
    return render(request, "pages/contacts/create.html")


@login_required(login_url="/login/")
def contact_detail_page(request, contact_id):
    return render(request, "pages/contacts/detail.html", {"contact_id": contact_id})


@login_required(login_url="/login/")
def calendar_page(request):
    return render(request, "pages/calendar.html")


@login_required(login_url="/login/")
def kanban_page(request):
    return render(request, "pages/kanban/board.html")


@login_required(login_url="/login/")
def messaging_page(request):
    return render(request, "pages/messaging/chat.html")


@login_required(login_url="/login/")
def analytics_page(request):
    return render(request, "pages/analytics/overview.html")


# ---------------------------------------------------------------------------
# Admin-only pages (hierarchy_level <= 10)
# ---------------------------------------------------------------------------

@_role_required(10)
def settings_page(request):
    return render(request, "pages/settings/tenant.html")


@_role_required(10)
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
